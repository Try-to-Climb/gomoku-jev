"""Can the backend tell a blocked line from a live one?

The perception probe found jev answering "yes, this line can still reach five"
for every line, dead or alive. That is ambiguous: it could be blindness to
blocking stones, or simple acquiescence to any question of that shape. This test
settles it by decomposing the same judgement into four layers and asking all of
them about the same line, in one fan-out request:

1. ``cell_occupied``  -- can it read one named point? (truth alternates yes/no)
2. ``ends_empty``     -- can it see the two points at the ends of the line?
3. ``count4``         -- can it count the stones in the line? (truth alternates)
4. ``can_reach`` / ``cannot_reach`` -- the inference, asked positively *and*
   negatively. Answering yes to both is acquiescence, not perception.

    python3 -m gomoku.vision --repeats 2
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys
from dataclasses import dataclass

from . import backends
from .analysis import maximal_runs
from .board import DIRECTIONS, Move, Stone
from .candidates import candidate_points
from .game import Game
from .backends import battery_for
from .battery import JevError
from .llm_player import BACKEND, JevConfig
from .notation import from_notation, to_notation
from .perceive import ORIENTATION, describe_run
from .prompts import rules_and_state
from .rules import RuleSet

RESULTS = pathlib.Path(__file__).resolve().parent / "results"
SIZE = 9
RULES = RuleSet(size=SIZE)


@dataclass
class Case:
    id: str
    why: str
    moves: list[str]
    #: the point the questions about a single cell refer to
    cell: str
    #: expected: is that cell occupied by a white stone?
    cell_is_white: bool


def cases() -> list[Case]:
    return [
        Case(
            "blocked_both_ends_4",
            "黑 E5 F5 G5 H5，两端 D5/I5 都是白子 → 死",
            ["E5", "D5", "F5", "I5", "G5", "A1", "H5", "C1"],
            cell="D5",
            cell_is_white=True,
        ),
        Case(
            "blocked_one_end_4",
            "黑 E5 F5 G5 H5，只有左端 D5 是白子，I5 空 → 活（I5 即成五）",
            ["E5", "D5", "F5", "A1", "G5", "C1", "H5", "A9"],
            cell="I5",
            cell_is_white=False,
        ),
        Case(
            "open_both_ends_3",
            "黑 E5 F5 G5，两端全空 → 活",
            ["E5", "A1", "F5", "C1", "G5", "A9"],
            cell="D5",
            cell_is_white=False,
        ),
        Case(
            "blocked_both_ends_2",
            "黑 E5 F5，两端 D5/G5 都是白子 → 死",
            ["E5", "D5", "F5", "G5"],
            cell="G5",
            cell_is_white=True,
        ),
        Case(
            "edge_and_stone_4",
            "黑 F5 G5 H5 I5 靠右边线，左端 E5 是白子 → 死（右边没格子了）",
            ["F5", "E5", "G5", "A1", "H5", "C1", "I5", "A9"],
            cell="E5",
            cell_is_white=True,
        ),
    ]


WINDOW_OPTIONS = {
    "2_or_fewer": "At most 2 consecutive points are available.",
    "3": "Exactly 3 consecutive points are available.",
    "4": "Exactly 4 consecutive points are available.",
    "5_or_more": "5 or more consecutive points are available.",
}


def window_label(length: int) -> str:
    if length <= 2:
        return "2_or_fewer"
    if length >= 5:
        return "5_or_more"
    return str(length)


def longest_window(board, cells, direction, opponent: Stone) -> int:
    """Longest unbroken stretch through ``cells`` free of ``opponent`` and on-board."""
    dr, dc = direction
    length = len(cells)
    for sign, anchor in ((1, cells[-1]), (-1, cells[0])):
        row, col = anchor.row + dr * sign, anchor.col + dc * sign
        while board.in_bounds(row, col) and board.get(row, col) is not opponent:
            length += 1
            row, col = row + dr * sign, col + dc * sign
    return length


def end_question(board, point: Move, last_stone: Move, side: str) -> tuple[str, bool]:
    """Ask whether one end of the line is blocked, by a stone or by the edge."""
    if board.in_bounds(point.row, point.col):
        text = f"Is the point {to_notation(point, SIZE)} occupied by a white stone?"
        return text, board.get(point.row, point.col) is Stone.WHITE
    text = (
        f"The stone {to_notation(last_stone, SIZE)} is the last one of that line on the "
        f"{side} side. Is it right against the edge of the board, with no further point "
        "beyond it in that direction?"
    )
    return text, True


def build(case: Case):
    game = Game.replay([from_notation(c, SIZE) for c in case.moves], RULES)
    board = game.board
    # the line under test: black's longest horizontal run on row 5
    runs = [r for r in maximal_runs(board, Stone.BLACK, RULES.win_length) if r.direction == (0, 1)]
    run = max(runs, key=lambda r: r.length)
    cells = list(run.cells)
    dr, dc = run.direction
    before = Move(cells[0].row - dr, cells[0].col - dc)
    after = Move(cells[-1].row + dr, cells[-1].col + dc)

    def empty_end(point: Move) -> bool:
        return board.in_bounds(point.row, point.col) and board.is_empty(point)

    ends_empty = empty_end(before) and empty_end(after)
    cell = from_notation(case.cell, SIZE)
    longest_black = max(
        (r.length for r in maximal_runs(board, Stone.BLACK, RULES.win_length, min_length=1)),
        default=0,
    )
    stones = ", ".join(to_notation(c, SIZE) for c in cells)
    orientation = ORIENTATION[run.direction]
    line_text = describe_run(run, "black", SIZE, RULES.win_length)

    left_text, left_blocked = end_question(board, before, cells[0], "left")
    right_text, right_blocked = end_question(board, after, cells[-1], "right")
    window = longest_window(board, cells, run.direction, Stone.WHITE)

    questions = {
        "cell_occupied": {
            "type": "noul",
            "instructions": f"Is the point {case.cell} occupied by a white stone?",
        },
        "left_end_blocked": {"type": "noul", "instructions": left_text},
        "right_end_blocked": {"type": "noul", "instructions": right_text},
        "ends_empty": {
            "type": "noul",
            "instructions": (
                f"Consider the black stones {stones}, an unbroken {orientation}. Are BOTH of "
                "the two points immediately beyond its two ends empty points on the board?"
            ),
        },
        # same wording polarity as the single-end questions ("white stone"), but still a
        # conjunction / a disjunction: this separates "cannot combine facts" from
        # "cannot handle the empty/occupied polarity flip"
        "both_ends_white": {
            "type": "noul",
            "instructions": (
                f"Consider the black stones {stones}, an unbroken {orientation}. Are BOTH of "
                "the two points immediately beyond its two ends occupied by white stones?"
            ),
        },
        "either_end_white": {
            "type": "noul",
            "instructions": (
                f"Consider the black stones {stones}, an unbroken {orientation}. Is AT LEAST "
                "ONE of the two points immediately beyond its two ends occupied by a white "
                "stone?"
            ),
        },
        "window": {
            "type": "choice",
            "instructions": (
                f"Consider the black stones {stones} and the {orientation} they lie on. Starting "
                "from those stones and walking outwards in both directions, count how many "
                "consecutive points of that line are available: a point counts if it is on the "
                "board and is not occupied by a white stone. Stop at the first white stone or at "
                "the edge of the board. How many consecutive points are available in total, "
                "including the black stones themselves?"
            ),
            "criteria": dict(WINDOW_OPTIONS),
        },
        "count4": {
            "type": "noul",
            "instructions": (
                "Does the black player have four or more stones in an unbroken line "
                "anywhere on this board?"
            ),
        },
        "can_reach": {"type": "noul", "instructions": line_text},
        "cannot_reach": {
            "type": "noul",
            "instructions": (
                f"Look at these stones of the black player: {stones}. They form an unbroken "
                f"{orientation}. Because of the white stones around it and the edges of the "
                f"board, is it now IMPOSSIBLE for this line ever to become "
                f"{RULES.win_length} in a row?"
            ),
        },
    }
    def is_white(point: Move) -> bool:
        return board.in_bounds(point.row, point.col) and board.get(point.row, point.col) is Stone.WHITE

    truths = {
        "cell_occupied": case.cell_is_white,
        "left_end_blocked": left_blocked,
        "right_end_blocked": right_blocked,
        "ends_empty": ends_empty,
        "both_ends_white": is_white(before) and is_white(after),
        "either_end_white": is_white(before) or is_white(after),
        "window": window_label(window),
        "count4": longest_black >= 4,
        "can_reach": run.live,
        "cannot_reach": not run.live,
    }
    return game, run, questions, truths


NOUL_IDS = (
    "cell_occupied",
    "left_end_blocked",
    "right_end_blocked",
    "ends_empty",
    "both_ends_white",
    "either_end_white",
    "count4",
    "can_reach",
    "cannot_reach",
)
ORDER = (
    "cell_occupied",
    "left_end_blocked",
    "right_end_blocked",
    "ends_empty",
    "both_ends_white",
    "either_end_white",
    "window",
    "count4",
    "can_reach",
    "cannot_reach",
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Can the backend see that a line is blocked?")
    ap.add_argument("--backend", choices=backends.names(models_only=True), default="jev")
    ap.add_argument("--model", default=None)
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)

    config = JevConfig(model=args.model or JevConfig().model, seed=args.seed)
    if args.prompt_version:
        config.prompt_version = args.prompt_version
    client = battery_for(args.backend, args.model)
    print(f"backend: {client.describe()}\n")

    runs: list[dict] = []
    for case in cases():
        game, run, questions, truths = build(case)
        view = game.view()
        state = rules_and_state(view, config.style, None, BACKEND)
        print(f"--- {case.id} ---\n{case.why}")
        print(f"引擎判定: 这条线 {'活' if run.live else '死'}（长度 {run.length}）")
        for rep in range(args.repeats):
            try:
                response = client.ask(state, questions)
            except JevError as exc:
                print(f"  rep{rep} ERROR {exc}")
                continue
            row = {"case": case.id, "repeat": rep, "live": run.live, "answers": {}}
            for qid, truth in truths.items():
                answer = response.answers.get(qid) or {}
                if qid in NOUL_IDS:
                    value = answer.get("noul")
                    p = float(value) if isinstance(value, (int, float)) else None
                    said = None if p is None else p > 0.5
                else:  # the choice question: compare the picked option
                    said = (answer.get("choice") or "").strip() or None
                    p = answer.get("confidence")
                row["answers"][qid] = {
                    "truth": truth, "p": p, "said": said,
                    "correct": None if said is None else said == truth,
                }
            both_yes = bool(
                row["answers"]["can_reach"]["said"] and row["answers"]["cannot_reach"]["said"]
            )
            row["contradiction"] = bool(both_yes)
            runs.append(row)
            parts = []
            for qid in ORDER:
                a = row["answers"][qid]
                mark = "✓" if a["correct"] else "✗"
                if qid in NOUL_IDS:
                    shown = "y" if a["said"] else "n"
                    parts.append(f"{qid}={shown}({a['p']:.2f}){mark}")
                else:
                    parts.append(f"{qid}={a['said']}{mark}")
            print(f"  rep{rep}:\n      " + "\n      ".join(parts)
                  + ("\n      ⚠ 正反问法同时答是" if both_yes else ""))
        print()

    print("汇总（每格 = 正确次数/总次数）")
    header = f"{'case':22}" + "".join(f"{q[:17]:>18}" for q in ORDER)
    print(header)
    print("-" * len(header))
    for case in cases():
        rows = [r for r in runs if r["case"] == case.id]
        line = f"{case.id:22}"
        for qid in ORDER:
            got = [r["answers"][qid]["correct"] for r in rows]
            line += f"{sum(1 for x in got if x)}/{len(got)}".rjust(18)
        print(line)
    totals = f"{'TOTAL':22}"
    for qid in ORDER:
        got = [r["answers"][qid]["correct"] for r in runs]
        totals += f"{sum(1 for x in got if x)}/{len(got)}".rjust(18)
    print(totals)
    contradictions = sum(r["contradiction"] for r in runs)
    print(f"\n正反问法同时答「是」的次数（纯附和的证据）: {contradictions}/{len(runs)}")

    out = args.out or RESULTS / f"vision_{client.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
         "backend": client.name, "model": args.model, "runs": runs},
        indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
