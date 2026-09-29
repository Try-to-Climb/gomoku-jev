"""Play one game and write a human-auditable, move-by-move Markdown log.

    python3 -m gomoku.audit --black jev --white openai --size 9

For every ply the document records, in order: the position before the move, the
objective tactical facts the engine computed *before* asking (so you can judge
the answer against something other than hindsight), the exact option menu the
model was given, its raw reply with confidence and token cost, the resulting
position, and the referee's verdict. Rejected attempts are kept too.

The file is flushed after every move, so you can read along while the game runs.
The matching JSON record (same basename, ``.json``) has the full prompts.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys

from .board import Stone
from .backends import build_player
from .cli import _per_seat
from .live import hold_open
from .game import Game
from .metrics import format_summary, summarize
from .notation import to_notation
from .render import render_board
from .rules import IllegalMovePolicy, Opening, OverlineRule, RuleSet, describe

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")

SIDE = {Stone.BLACK: "black (X)", Stone.WHITE: "white (O)"}
STATUS = {
    "in_progress": "in progress",
    "black_win": "black wins",
    "white_win": "white wins",
    "draw": "draw",
}


class AuditWriter:
    """Streams the Markdown document as the game is played."""

    def __init__(self, path: pathlib.Path, rules: RuleSet, players: dict) -> None:
        self.path = path
        self.rules = rules
        self.players = players
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w", encoding="utf-8")
        self._header()

    def _w(self, text: str = "") -> None:
        self.fh.write(text + "\n")
        self.fh.flush()

    def _header(self) -> None:
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self._w("# Gomoku match audit")
        self._w()
        self._w(f"- generated: {now}")
        self._w(f"- black (X): **{self.players['black']['name']}**")
        self._w(f"- white (O): **{self.players['white']['name']}**")
        self._w(f"- board: {self.rules.size}x{self.rules.size}, "
                f"{self.rules.win_length} in a row wins")
        self._w(f"- rules: {describe(self.rules)}")
        self._w()
        self._w("Player configuration:")
        self._w()
        self._w("```json")
        self._w(json.dumps(self.players, indent=2, ensure_ascii=False))
        self._w("```")
        self._w()
        self._w("How to read this: each move starts with the position *before* it was played "
                "and the objective facts the engine derived from that position (which points "
                "win immediately, for either side). Those facts were computed **before** the "
                "model was asked and were not shown to it, so the answer can be judged "
                "against something other than hindsight. Then comes the option menu the "
                "model was given, its raw reply, the resulting position, and the verdict. "
                "Rejected attempts are kept.")
        self._w()
        self._w("---")
        self._w()
        self._w("## Move by move")

    # ------------------------------------------------------------------ moves
    def move(self, record, game: Game) -> None:
        size = self.rules.size
        before = Game.replay(game.history[:-1], self.rules) if record.move else game
        stone = record.stone
        move_text = record.move or "(no move)"

        tag = ""
        a = record.analysis
        if a is not None:
            if a.took_win:
                tag = " - took the win"
            elif a.missed_win:
                tag = " - MISSED a win that was available"
            elif a.blocked_threat:
                tag = " - blocked their winning point"
            elif a.missed_block:
                tag = " - FAILED to block"
            elif a.unavoidable_loss:
                tag = " - already lost: they have two winning points"

        self._w()
        self._w(f"### Move {record.ply} - {SIDE[stone]} - {record.player} -> **{move_text}**{tag}")
        self._w()

        self._w("**Position before this move**")
        self._w()
        self._w("```")
        self._w(render_board(before.board, before.last_move))
        self._w("```")
        self._w()

        if a is not None:
            own = [to_notation(m, size) for m in a.own_wins]
            opp = [to_notation(m, size) for m in a.opponent_wins]
            self._w("**Objective facts from the engine** (computed before asking; "
                    "not shown to the model)")
            self._w()
            self._w(f"- points where this side wins at once: {', '.join(own) if own else 'none'}")
            self._w(f"- points where the opponent wins at once: {', '.join(opp) if opp else 'none'}"
                    + ("  <- must be blocked" if len(opp) == 1 and not own else "")
                    + ("  <- two of them, cannot be blocked" if len(opp) > 1 and not own else ""))
            self._w(f"- legal empty points: {len(before.legal_moves())}")
            self._w()

        for i, attempt in enumerate(record.attempts, 1):
            self._attempt(i, attempt, len(record.attempts))

        if record.fallback:
            self._w("> Retries exhausted; the referee played a random legal move on this "
                    "player's behalf (`random_fallback` policy).")
            self._w()

        self._w("**Position after the move**")
        self._w()
        self._w("```")
        self._w(render_board(game.board, game.last_move))
        self._w("```")
        self._w()
        self._w(f"Verdict: **{STATUS.get(game.status.value, game.status.value)}**"
                + (f" ({game.outcome.reason})" if game.outcome.reason else ""))
        self._w()
        self._w("---")

    def _attempt(self, index: int, attempt, total: int) -> None:
        meta = attempt.meta or {}
        verdict = {
            "accepted": "accepted",
            "illegal": "illegal move, rejected",
            "no_move": "no move could be parsed, rejected",
        }.get(attempt.verdict, attempt.verdict)
        label = "**Model reply**" if total == 1 else f"**Attempt {index} - {verdict}**"
        self._w(label)
        self._w()

        menu = meta.get("menu")
        if menu:
            self._w(f"- options offered ({len(menu)}, shuffled): `{', '.join(menu)}`")
        if attempt.move:
            self._w(f"- picked: **{attempt.move}**")
        if attempt.error:
            self._w(f"- problem: `{attempt.error}`")
        if "confidence" in meta:
            self._w(f"- confidence: {meta['confidence']}")
        if meta.get("top_probabilities"):
            probs = ", ".join(f"{k} {v}" for k, v in meta["top_probabilities"].items())
            self._w(f"- highest probabilities: {probs}")
        usage = meta.get("usage") or {}
        bits = [f"{attempt.latency_s:.2f}s"]
        if usage:
            bits.append(f"tokens in/out {usage.get('input_tokens')}/{usage.get('output_tokens')}")
        if meta.get("finish_reason"):
            bits.append(f"finish_reason={meta['finish_reason']}")
        if meta.get("truncation_retry"):
            bits.append("retried after truncation")
        if meta.get("forced"):
            bits.append("only one legal point, no API call")
        self._w(f"- {', '.join(bits)}")
        if attempt.raw:
            raw = attempt.raw if len(attempt.raw) <= 1200 else attempt.raw[:1200] + " ...(truncated)"
            self._w()
            self._w("Raw reply:")
            self._w()
            self._w("```json")
            self._w(raw)
            self._w("```")
        self._w()

    # ------------------------------------------------------------------ footer
    def finish(self, match) -> None:
        self._w()
        self._w("## Result")
        self._w()
        winner = match.winner_name() or "nobody (draw)"
        self._w(f"- outcome: **{STATUS.get(match.status.value, match.status.value)}**"
                f" ({match.reason})")
        self._w(f"- winner: **{winner}**")
        self._w(f"- plies: {match.plies}, total {match.duration_s:.1f}s")
        self._w(f"- move list: `{' '.join(match.move_list())}`")
        self._w()
        self._w("## Metrics")
        self._w()
        self._w("```")
        self._w(format_summary(summarize([match])))
        self._w("```")
        self._w()
        self._w("How to read these: `winconv` is the share of positions with an immediate "
                "win available where it was taken; `block%` is the share of positions where "
                "the opponent had exactly one winning point and it was covered (two points "
                "counts as `unavoidable_loss` and is excluded from the denominator); "
                "`ill%` and `parse%` are over attempts, not moves.")
        self._w()
        self._w("## Appendix: the full prompt sent to each player")
        self._w()
        self._w("The real request text from each player's first move. Later moves differ "
                "only in the position and the option menu.")
        for seat in ("black", "white"):
            name = self.players[seat]["name"]
            prompt = None
            for record in match.moves:
                if record.player == name:
                    for attempt in record.attempts:
                        prompt = (attempt.meta or {}).get("prompt")
                        if prompt:
                            break
                if prompt:
                    break
            if not prompt:
                continue
            self._w()
            self._w(f"<details><summary>{seat} · {name}</summary>")
            self._w()
            for key, value in prompt.items():
                self._w(f"**{key}**")
                self._w()
                self._w("```" + ("json" if not isinstance(value, str) else ""))
                self._w(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False))
                self._w("```")
                self._w()
            self._w("</details>")
        self._w()
        self.fh.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Play one game and write an audit document.")
    ap.add_argument("--black", default="jev")
    ap.add_argument("--white", default="openai")
    ap.add_argument("--size", type=int, default=9)
    ap.add_argument("--win-length", type=int, default=5)
    ap.add_argument("--overline", choices=[o.value for o in OverlineRule], default=OverlineRule.WIN.value)
    ap.add_argument("--opening", choices=[o.value for o in Opening], default=Opening.FREE.value)
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--illegal", choices=[p.value for p in IllegalMovePolicy],
                    default=IllegalMovePolicy.FORFEIT.value)
    ap.add_argument("--max-plies", type=int, default=None)
    ap.add_argument("--candidates", choices=["near", "all"], default="near")
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--templates", default=None, metavar="DIR",
                    help="load prompt text from this directory instead of gomoku/templates")
    ap.add_argument("--facts", default="none",
                    help="inject engine-computed line facts into the state: "
                         "none|span|status|geometry|threats, optionally per seat, "
                         "e.g. --facts white=status (changes what is measured; recorded)")
    ap.add_argument("--option-facts", default="none",
                    help="annotate each candidate option: none|ways|windows|open_four|threat|full, "
                         "optionally per seat, e.g. --option-facts black=open_four")
    ap.add_argument("--prompt-version", default=None,
                    help="tactical wording: p0-one-move (control) or p1-threat-ladder")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--live", action="store_true",
                    help="serve a live board + raw-reply view on http://127.0.0.1:<port>")
    ap.add_argument("--live-port", type=int, default=8765)
    ap.add_argument("--live-file", type=pathlib.Path, default=None, metavar="PATH",
                    help="also mirror the live view into a self-contained HTML file "
                         "(open it directly; needs no network)")
    ap.add_argument("--live-host", default="127.0.0.1",
                    help="bind address for --live; 0.0.0.0 exposes the view to your network")
    ap.add_argument("--live-hold", type=float, default=0.0, metavar="SECONDS",
                    help="keep serving this long after the last game (0 = until Enter)")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser for --live")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    rules = RuleSet(
        size=args.size,
        win_length=args.win_length,
        overline=OverlineRule(args.overline),
        opening=Opening(args.opening),
        max_retries=args.max_retries,
        illegal_move=IllegalMovePolicy(args.illegal),
        max_plies=args.max_plies,
    )
    overrides = {
        "candidates": args.candidates,
        "max_candidates": args.max_candidates,
        "log_prompts": True,
    }
    if args.prompt_version:
        overrides["prompt_version"] = args.prompt_version
    facts = _per_seat(args.facts)
    option_facts = _per_seat(args.option_facts)
    black = build_player(
        args.black, args.seed,
        {**overrides, "facts": facts["black"], "option_facts": option_facts["black"]},
    )
    white = build_player(
        args.white, None if args.seed is None else args.seed + 1,
        {**overrides, "facts": facts["white"], "option_facts": option_facts["white"]},
    )
    if black.name == white.name:
        black.name, white.name = f"{black.name}#1", f"{white.name}#2"

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or RESULTS / f"audit-{stamp}.md"
    writer = AuditWriter(out, rules, {"black": black.describe(), "white": white.describe()})
    print(f"audit document: {out}")

    from .match import play_match

    live = None
    if args.live:
        from .live import LiveBoard

        live = LiveBoard(
            rules,
            port=args.live_port,
            open_browser=not args.no_open,
            host=args.live_host,
            snapshot=args.live_file,
        )
        live.start_game(0, black, white)
        if live.url:
            print(f"live view: {live.url}")
        if live.snapshot:
            print(f"live file: {live.snapshot}")

    def on_move(record, game):
        writer.move(record, game)
        if live is not None:
            live.add_move(record, game)
        rejected = len(record.rejected)
        note = f" ({rejected} rejected)" if rejected else ""
        print(f"{record.ply:3d}. {record.stone.symbol} {record.move}{note}", flush=True)

    match = play_match(
        black,
        white,
        rules,
        on_move=on_move,
        on_turn=(live.set_thinking if live else None),
        rng=random.Random(args.seed),
    )
    writer.finish(match)
    if live is not None:
        live.finish(match)

    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(match.to_dict(), indent=2, ensure_ascii=False))

    print(f"\n{match.status.value} ({match.reason}) winner={match.winner_name()} plies={match.plies}")
    print(format_summary(summarize([match])))
    print(f"\nwrote {out}\nwrote {json_path}")

    for player in (black, white):
        player.close()
    if live is not None:
        hold_open(args.live_hold)
        live.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
