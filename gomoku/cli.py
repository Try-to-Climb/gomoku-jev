"""Command line entry point.

    python -m gomoku.cli --black heuristic --white random --games 4 --render

LLM players are registered in :mod:`gomoku.llm_player` and appear here as
``llm:<model>`` once that module exists.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import time
import sys

from .board import Stone
from . import backends
from .backends import build_player, describe as describe_backends, spec_help
from .match import play_series
from .metrics import format_summary, summarize
from .players import ConsolePlayer, Player
from .render import render_board
from .rules import IllegalMovePolicy, Opening, OverlineRule, RuleSet, describe

#: re-exported so ``from gomoku.cli import build_player`` keeps working
__all__ = ["ConsolePlayer", "build_player", "main"]


def _per_seat(value: str) -> dict[str, str]:
    """``"status"`` -> both seats; ``"white=status,black=none"`` -> one seat each."""
    if "=" not in value:
        return {"black": value, "white": value}
    out = {"black": "none", "white": "none"}
    for part in value.split(","):
        seat, _, level = part.partition("=")
        seat = seat.strip().lower()
        if seat not in out:
            raise SystemExit(f"unknown seat {seat!r} (black | white)")
        out[seat] = level.strip()
    return out


def _hold_live(seconds: float) -> None:
    """Keep the live view up after the game: for a fixed time, or until Enter."""
    if seconds > 0:
        print(f"\nlive view 保持 {seconds:.0f} 秒后关闭…", flush=True)
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            pass
        return
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\nlive view 仍在运行，按回车关闭…")
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gomoku", description="Run gomoku matches.")
    ap.add_argument("--black", default="heuristic",
                    help="player spec for black, e.g. jev@p2-defence-first")
    ap.add_argument("--white", default="random", help="player spec for white")
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--no-swap", action="store_true", help="do not alternate colours")
    ap.add_argument("--size", type=int, default=15)
    ap.add_argument("--win-length", type=int, default=5)
    ap.add_argument("--overline", choices=[o.value for o in OverlineRule], default=OverlineRule.WIN.value)
    ap.add_argument("--opening", choices=[o.value for o in Opening], default=Opening.FREE.value)
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument(
        "--illegal",
        choices=[p.value for p in IllegalMovePolicy],
        default=IllegalMovePolicy.FORFEIT.value,
    )
    ap.add_argument("--max-plies", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--candidates", choices=["near", "all"], default="near",
                    help="model players: offer nearby points or every legal point")
    ap.add_argument("--max-candidates", type=int, default=30,
                    help="model players: cap on the option menu")
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
    ap.add_argument("--render", action="store_true", help="print the board after every move")
    ap.add_argument("--no-annotate", action="store_true", help="skip missed win/block analysis")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="write match records as JSON")
    ap.add_argument("--show-rules", action="store_true", help="print the rule text and exit")
    ap.add_argument("--list-backends", action="store_true",
                    help="list the registered player backends and exit")
    ap.add_argument("--list-prompts", action="store_true",
                    help="list the available tactical wordings (prompt versions) and exit")
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    if args.list_backends:
        width = max(len(name) for name, _ in describe_backends())
        for name, summary in describe_backends():
            print(f"{name:{width}}  {summary}")
        print(f"\nspec syntax: {spec_help()}")
        return 0

    if args.list_prompts:
        from .prompts import PROMPT_VERSION, tactics_versions

        for version in tactics_versions():
            mark = " (default)" if version == PROMPT_VERSION else ""
            print(f"{version}{mark}")
        for backend in backends.names(models_only=True):
            extra = [v for v in tactics_versions(backend) if v not in tactics_versions()]
            for version in extra:
                print(f"{version} ({backend} only)")
        print("\nUse --prompt-version <name>, or pin one seat with e.g. --black jev@<name>.")
        return 0

    rules = RuleSet(
        size=args.size,
        win_length=args.win_length,
        overline=OverlineRule(args.overline),
        opening=Opening(args.opening),
        max_retries=args.max_retries,
        illegal_move=IllegalMovePolicy(args.illegal),
        max_plies=args.max_plies,
    )
    if args.show_rules:
        print(describe(rules))
        return 0

    candidate_overrides = {
        "candidates": args.candidates,
        "max_candidates": args.max_candidates,
    }
    if args.prompt_version:
        candidate_overrides["prompt_version"] = args.prompt_version
    facts = _per_seat(args.facts)
    option_facts = _per_seat(args.option_facts)
    black = build_player(
        args.black, args.seed,
        {**candidate_overrides, "facts": facts["black"], "option_facts": option_facts["black"]},
    )
    white = build_player(
        args.white, None if args.seed is None else args.seed + 1,
        {**candidate_overrides, "facts": facts["white"], "option_facts": option_facts["white"]},
    )
    if black.name == white.name:
        # stats are keyed by name; keep the two seats apart
        black.name, white.name = f"{black.name}#1", f"{white.name}#2"

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
        if live.url:
            print(f"live view: {live.url}")
        if live.snapshot:
            print(f"live file: {live.snapshot}")

    def on_move(record, game):
        if live is not None:
            live.add_move(record, game)
        note = ""
        if record.analysis is not None:
            if record.analysis.missed_win:
                note = "  <- missed a win"
            elif record.analysis.missed_block:
                note = "  <- missed a block"
        rejected = len(record.rejected)
        retries = f" ({rejected} rejected)" if rejected else ""
        print(f"{record.ply:3d}. {record.stone.symbol} {record.move}{retries}{note}")
        if args.render:
            print(render_board(game.board, game.last_move))
            print()

    def dump(records):
        """Write after every game: a long series must not lose everything if it dies."""
        if not args.out:
            return
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"summary": summarize(records), "games": [r.to_dict() for r in records]},
            indent=2, ensure_ascii=False))

    done: list = []

    def on_game(record):
        done.append(record)
        dump(done)
        if live is not None:
            live.finish(record)

    records = play_series(
        black,
        white,
        games=args.games,
        rules=rules,
        swap_colors=not args.no_swap,
        annotate=not args.no_annotate,
        on_move=on_move,
        on_turn=(live.set_thinking if live else None),
        on_game_start=(live.start_game if live else None),
        on_game=on_game,
        rng=random.Random(args.seed),
    )

    for record in records:
        winner = record.winner_name() or "nobody"
        print(
            f"\ngame {record.game_id}: {record.status.value} ({record.reason}) "
            f"winner={winner} plies={record.plies}"
        )
        print(" ".join(record.move_list()))

    summary = summarize(records)
    print()
    print(format_summary(summary))

    if args.out:
        dump(records)
        print(f"\nwrote {args.out}")

    for player in (black, white):
        player.close()
    if live is not None:
        _hold_live(args.live_hold)
        live.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
