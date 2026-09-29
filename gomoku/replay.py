"""Turn a recorded game back into the live view, as a standalone HTML file.

The live view only ever showed a game *while it was being played*: the state was
built incrementally from the referee's callbacks, and there was no way back from a
saved record. That is the wrong way round for a published repository, where the
first thing a reader wants is to open one of the games in ``results/`` and see
what the models actually said.

    python3 -m gomoku.replay results/audit-jev-vs-gptoss.json
    python3 -m gomoku.replay results/jev_vs_bot.json --game 2 -o /tmp/game2.html

Reads either shape written by this package:

* a single match, as ``gomoku.audit`` writes it;
* ``{"summary": ..., "games": [...]}``, as ``gomoku.cli --out`` writes it.

The output needs no server and no network: the whole state is embedded in the
page, and the move list is scrubbable, so every position can be stepped through.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import webbrowser

from .board import Stone
from .live import PAGE, LiveBoard
from .notation import from_notation
from .rules import RuleSet

EMBED_MARKER = "const EMBEDDED = null; // __EMBEDDED__"


def load_games(path: pathlib.Path) -> list[dict]:
    """Every match record in ``path``, whichever of the two shapes it uses."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("games"), list):
        return data["games"]
    if isinstance(data, dict) and "moves" in data:
        return [data]
    raise SystemExit(
        f"{path} is not a gomoku match record "
        "(expected a single match, or an object with a 'games' list)"
    )


def build_state(record: dict, game_no: int = 1) -> dict:
    """Rebuild the page state from a record, replaying the moves onto a board.

    Deliberately reuses :class:`~gomoku.live.LiveBoard` rather than formatting the
    payload again here: the page contract then has exactly one producer, so a field
    the page needs can never be present live and missing on replay.
    """
    rules = RuleSet.from_dict(record["rules"])
    board = LiveBoard(rules, open_browser=False, serve=False)
    size = rules.size
    grid = [["" for _ in range(size)] for _ in range(size)]
    last: str | None = None

    for index, move in enumerate(record.get("moves", [])):
        stone = Stone.BLACK if move.get("stone") == "black" else Stone.WHITE
        if move.get("move"):
            row, col = from_notation(move["move"], size)
            grid[row][col] = stone.symbol
            last = move["move"]
        board._state["moves"].append(
            {
                "ply": move.get("ply", index + 1),
                "stone": move.get("stone"),
                "symbol": stone.symbol,
                "player": move.get("player", "?"),
                "move": move.get("move"),
                "latency_s": move.get("latency_s", 0),
                "fallback": move.get("fallback", False),
                "attempts": [_attempt(a) for a in move.get("attempts", [])],
                **({"analysis": move["analysis"], "verdict": _verdict(move["analysis"])}
                   if move.get("analysis") else {}),
            }
        )

    board._state.update(
        {
            "game_no": game_no,
            "players": record.get("players", {}),
            "status": record.get("status", "unknown"),
            "reason": record.get("reason", ""),
            "winner": record.get("winner_name"),
            "board": grid,
            "last_move": last,
            "turn": None,
            "thinking": None,
            "summary": _summary(record),
        }
    )
    return board._state


def _attempt(attempt: dict) -> dict:
    meta = dict(attempt.get("meta") or {})
    meta.pop("prompt", None)  # as live does: too long for a page, and in the JSON already
    return {
        "verdict": attempt.get("verdict"),
        "move": attempt.get("move"),
        "raw": attempt.get("raw"),
        "error": attempt.get("error"),
        "latency_s": attempt.get("latency_s", 0),
        "meta": meta,
    }


def _verdict(analysis: dict) -> str | None:
    """The same taxonomy as :func:`gomoku.live._verdict`, from a serialised analysis."""
    order = (
        ("took_win", "took_win"),
        ("missed_win", "missed_win"),
        ("blocked_threat", "blocked_five"),
        ("missed_block", "missed_five_block"),
        ("took_open_four", "took_open_four"),
        ("missed_open_four", "missed_open_four"),
        ("prevented_open_four", "prevented_open_four"),
        ("allowed_open_four", "allowed_open_four"),
        ("unavoidable_loss", "unavoidable_loss"),
    )
    for flag, label in order:
        if analysis.get(flag):
            return label
    return None


def _summary(record: dict) -> dict | None:
    """Re-score the record so the metrics panel is filled in.

    Replays the move list through the referee's own scorer rather than trusting a
    stored summary, which older records may not carry.
    """
    from .match import Attempt, MatchRecord, MoveRecord
    from .metrics import summarize
    from .rules import Status

    try:
        rules = RuleSet.from_dict(record["rules"])
        moves = []
        for move in record.get("moves", []):
            stone = Stone.BLACK if move.get("stone") == "black" else Stone.WHITE
            rebuilt = MoveRecord(
                ply=move.get("ply", 0),
                stone=stone,
                player=move.get("player", "?"),
                move=move.get("move"),
                attempts=[
                    Attempt(
                        verdict=a.get("verdict", "accepted"),
                        move=a.get("move"),
                        latency_s=a.get("latency_s", 0) or 0,
                    )
                    for a in move.get("attempts", [])
                ],
                fallback=move.get("fallback", False),
            )
            rebuilt.analysis = _analysis_from(move.get("analysis"), stone)
            moves.append(rebuilt)
        winner = record.get("winner")
        rebuilt_record = MatchRecord(
            game_id=record.get("game_id", "replay"),
            rules=rules,
            players=record.get("players", {}),
            moves=moves,
            status=Status(record.get("status", "in_progress")),
            winner=None if not winner else (Stone.BLACK if winner == "black" else Stone.WHITE),
            reason=record.get("reason", ""),
        )
        return summarize([rebuilt_record])
    except Exception:  # noqa: BLE001 - a viewer must still open a record it cannot score
        return None


def _analysis_from(data: dict | None, stone: Stone):
    if not data:
        return None
    from .analysis import MoveAnalysis

    analysis = MoveAnalysis(stone=stone)
    # only the flags and counts matter for scoring; coordinates are display-only
    analysis.own_wins = [None] * len(data.get("own_wins", []))
    analysis.opponent_wins = [None] * len(data.get("opponent_wins", []))
    analysis.own_open_fours = [None] * len(data.get("own_open_fours", []))
    analysis.opponent_open_fours = [None] * len(data.get("opponent_open_fours", []))
    for flag in (
        "took_win", "missed_win", "blocked_threat", "missed_block",
        "took_open_four", "missed_open_four", "prevented_open_four",
        "allowed_open_four", "unavoidable_loss",
    ):
        setattr(analysis, flag, bool(data.get(flag)))
    return analysis


def render_page(state: dict) -> str:
    """The live page with the state baked in; no server, no network."""
    page = PAGE.read_text(encoding="utf-8")
    if EMBED_MARKER not in page:  # pragma: no cover - guarded by a test
        raise RuntimeError(f"{PAGE} no longer contains the embed marker")
    return page.replace(EMBED_MARKER, f"const EMBEDDED = {json.dumps(state, ensure_ascii=False)};")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a recorded game as a standalone HTML page."
    )
    ap.add_argument("record", type=pathlib.Path, help="a match .json from results/")
    ap.add_argument("--game", type=int, default=1,
                    help="which game of a multi-game record (1-based, default 1)")
    ap.add_argument("-o", "--out", type=pathlib.Path, default=None,
                    help="output file (default: alongside the record, .html)")
    ap.add_argument("--open", action="store_true", help="open it in a browser afterwards")
    ap.add_argument("--list", action="store_true", help="list the games in the record and exit")
    args = ap.parse_args(argv)

    games = load_games(args.record)
    if args.list:
        for i, game in enumerate(games, 1):
            players = game.get("players", {})
            print(
                f"{i}. {players.get('black', {}).get('name', '?')} (black) vs "
                f"{players.get('white', {}).get('name', '?')} (white) -- "
                f"{game.get('status')} ({game.get('reason')}), {len(game.get('moves', []))} plies"
            )
        return 0
    if not 1 <= args.game <= len(games):
        raise SystemExit(f"--game must be 1..{len(games)} for {args.record}")

    state = build_state(games[args.game - 1], args.game)
    out = args.out or args.record.with_suffix(
        ".html" if len(games) == 1 else f".game{args.game}.html"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(state), encoding="utf-8")
    print(f"wrote {out} ({len(state['moves'])} plies)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
