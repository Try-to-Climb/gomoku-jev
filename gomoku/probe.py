"""Connectivity and sanity probe for a model backend.

    python3 -m gomoku.probe                              # jev: ping + three fixed positions
    python3 -m gomoku.probe --backend openai             # same, through a chat model
    python3 -m gomoku.probe --game 9                     # also play a 9x9 game vs the heuristic bot

The three positions have an objectively correct answer (except the first), so a
probe run tells you whether the wiring works *and* whether the model can see one
move ahead. Full payloads land in ``results/<backend>_probe.json``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys

from . import backends
from .analysis import analyse_position
from .candidates import candidate_points
from .game import Game
from .match import play_match
from .metrics import format_summary, summarize
from .notation import from_notation, to_notation
from .players import HeuristicPlayer, Player
from .rules import RuleSet

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")


def coords(*texts, size=15):
    return [from_notation(t, size) for t in texts]


def scenarios() -> list[dict]:
    rules9 = RuleSet(size=9)
    return [
        {
            "id": "empty_board",
            "why": "first move on an empty 15x15 board -- no wrong answer, checks the request shape",
            "game": Game(RuleSet()),
            "expect": None,
        },
        {
            "id": "must_block",
            "why": "black has four in a row with one open end; the model (white) must play I8",
            "game": Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8")),
            "expect": ["I8"],
        },
        {
            "id": "must_win",
            "why": "the model (white) has four in a row and can complete five at C9 or H9",
            "game": Game.replay(coords("A1", "D9", "A3", "E9", "A5", "F9", "A7", "G9", "B1")),
            "expect": ["C9", "H9"],
        },
        # --- threat-level cases, taken from the positions both backends misplayed ---
        {
            "id": "make_open_four",
            "why": "white (the model) has D4-D6 with both ends open: D7 or D3 makes an "
                   "unstoppable open four and wins. gpt-oss played E6 here under the old prompt",
            "game": Game.replay(
                coords("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", "C5", size=9), rules9
            ),
            "expect": ["D7", "D3"],
        },
        {
            "id": "stop_open_four",
            "why": "black (the model) must break white's open three D4-D6 now, i.e. play D7 or "
                   "D3; black's own row-5 line is dead at both ends. jev played C5 here",
            "game": Game.replay(
                coords("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", size=9), rules9
            ),
            "expect": ["D7", "D3"],
        },
    ]


def run_scenario(player: Player, scenario: dict) -> dict:
    game: Game = scenario["game"]
    view = game.view()
    facts = analyse_position(game)
    config = getattr(player, "config", None)
    candidates = (
        candidate_points(view, config, random.Random(config.seed))
        if config is not None and getattr(config, "mode", "choice") == "choice"
        else []
    )

    print(f"\n--- {scenario['id']} ---")
    print(scenario["why"])
    print(
        f"to move: {view.stone.label} ({view.stone.symbol})"
        + (f"   candidates offered: {len(candidates)}" if candidates else "   (free answer)")
    )
    if facts.own_wins:
        print(f"winning points available: {[to_notation(m, view.size) for m in facts.own_wins]}")
    if facts.opponent_wins:
        print(f"opponent winning points: {[to_notation(m, view.size) for m in facts.opponent_wins]}")

    response = player.propose(view)
    meta = response.meta
    if response.error:
        status = "error"
    elif scenario["expect"] is None:
        status = "ok"
    else:
        status = "correct" if to_notation(response.move, view.size) in scenario["expect"] else "wrong"

    print(
        f"latency {response.latency_s:.2f}s  usage {meta.get('usage')}  "
        f"model {meta.get('jev_model') or meta.get('api_model')}"
    )
    if response.error:
        print(f"ERROR: {response.error}")
        if response.raw:
            print(f"raw: {response.raw[:300]}")
    else:
        print(
            f"chose {to_notation(response.move, view.size)}"
            + (f"   expected {'/'.join(scenario['expect'])}" if scenario["expect"] else "")
            + f"   confidence {meta.get('confidence')}"
        )
        if meta.get("top_probabilities"):
            print(f"top probabilities: {meta['top_probabilities']}")
    print(f"verdict: {status}")

    return {
        "id": scenario["id"],
        "why": scenario["why"],
        "stone": view.stone.label,
        "history": [to_notation(m, view.size) for m in view.history],
        "candidates": [to_notation(m, view.size) for m in candidates],
        "own_wins": [to_notation(m, view.size) for m in facts.own_wins],
        "opponent_wins": [to_notation(m, view.size) for m in facts.opponent_wins],
        "expect": scenario["expect"],
        "chose": to_notation(response.move, view.size) if response.move else None,
        "error": response.error,
        "raw": response.raw,
        "latency_s": round(response.latency_s, 3),
        "meta": meta,
        "status": status,
    }


def build_backend(args) -> tuple[Player, dict]:
    """Return the player under test plus a JSON-serialisable header."""
    from .prompts import PROMPT_VERSION

    version = args.prompt_version or PROMPT_VERSION
    if args.backend == "jev":
        from .jev_client import JevClient
        from .llm_player import JevConfig, JevPlayer

        config = JevConfig(
            model=args.model or JevConfig().model,
            candidates=args.candidates,
            max_candidates=args.max_candidates,
            prompt_version=version,
            facts=args.facts,
            seed=args.seed,
        )
        client = JevClient(model=config.model)
        header = {"backend": "jev", "url": client.url, "key": client.key_fingerprint}
        print(f"backend: jev\nurl: {client.url}\nmodel: {config.model}\nkey: {client.key_fingerprint}")

        print("\n--- ping ---")
        ping = client.ping()
        print(
            f"HTTP {ping.http_status}  {ping.latency_s:.2f}s  jev {ping.model}  "
            f"usage {ping.usage}  answer {ping.answers}"
        )
        header["ping"] = {
            "ok": True,
            "http": ping.http_status,
            "latency_s": round(ping.latency_s, 3),
            "jev_model": ping.model,
            "usage": ping.usage,
        }
        return JevPlayer(config=config, client=client), header

    from .openai_player import DEFAULT_MODEL, OpenAIConfig, OpenAIPlayer, ensure_auth

    config = OpenAIConfig(
        model=args.model or DEFAULT_MODEL,
        mode=args.mode,
        require_distribution=not args.no_distribution,
        candidates=args.candidates,
        max_candidates=args.max_candidates,
        prompt_version=version,
        facts=args.facts,
        seed=args.seed,
        no_think=args.no_think,
    )
    print(f"backend: openai\nmodel: {config.model}\nmode: {config.mode}\nprompt: {version}")
    method = ensure_auth()
    print(f"auth: {method}")
    header = {"backend": "openai", "auth": method, "ping": {"ok": True}}
    return OpenAIPlayer(config=config), header


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe a model backend for gomoku play.")
    ap.add_argument("--backend", choices=backends.names(models_only=True), default="jev")
    ap.add_argument("--model", default=None)
    ap.add_argument("--mode", choices=["choice", "free"], default="choice",
                    help="openai only: same option menu as jev, or a free coordinate answer")
    ap.add_argument("--no-distribution", action="store_true",
                    help="openai choice mode: ask only for the pick, not a full distribution")
    ap.add_argument("--no-think", action="store_true", help="openai: append /no_think to the prompt")
    ap.add_argument("--candidates", choices=["near", "all"], default="near")
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--templates", default=None, metavar="DIR",
                    help="load prompt text from this directory instead of gomoku/templates")
    ap.add_argument("--facts", choices=["none", "span", "geometry", "status", "threats"], default="none",
                    help="inject engine-computed line facts into the state "
                         "(changes what is being measured; recorded as `facts`)")
    ap.add_argument("--prompt-version", default=None,
                    help="tactical wording to send, e.g. p0-one-move (control) or p1-threat-ladder")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--game", type=int, metavar="SIZE", default=None,
                    help="after the probes, play one game of this board size against the heuristic bot")
    ap.add_argument("--max-plies", type=int, default=40, help="ply cap for --game (API calls cost time)")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    report: dict = {
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    out = args.out or RESULTS / f"{args.backend}_probe.json"

    def write() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    try:
        player, header = build_backend(args)
    except Exception as exc:  # noqa: BLE001 - the point is to report, not to traceback
        print(f"\nFAILED to reach the backend: {type(exc).__name__}: {exc}")
        report["ping"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        write()
        return 1

    report.update(header)
    report["config"] = player.describe()
    report["scenarios"] = [run_scenario(player, s) for s in scenarios()]

    if args.game:
        rules = RuleSet(size=args.game, max_plies=args.max_plies)
        bot = HeuristicPlayer(name="heuristic", seed=args.seed)
        print(f"\n--- game: {player.name} (black) vs heuristic on {args.game}x{args.game} ---")

        def on_move(record, game):
            note = ""
            if record.analysis is not None:
                if record.analysis.missed_win:
                    note = "  <- missed a win"
                elif record.analysis.missed_block:
                    note = "  <- missed a block"
            rejected = len(record.rejected)
            retries = f" ({rejected} rejected)" if rejected else ""
            print(f"{record.ply:3d}. {record.stone.symbol} {record.move}{retries}{note}", flush=True)

        match = play_match(player, bot, rules, on_move=on_move, rng=random.Random(args.seed))
        print(f"\nresult: {match.status.value} ({match.reason}) winner={match.winner_name()}")
        print(format_summary(summarize([match])))
        report["game"] = match.to_dict()

    write()
    print(f"\nwrote {out}")
    player.close()
    return 1 if [s for s in report["scenarios"] if s["status"] in ("error", "wrong")] else 0


if __name__ == "__main__":
    sys.exit(main())
