"""Where exactly does the inference break?

``vision.py`` established that jev reads single points (0.99), reads each end of a
line (0.99), combines them with AND/OR (10/10) and even counts the available
window (10/10) -- yet cannot answer "can this line still become five". Three
groups of questions narrow that gap down:

1. **abstract** -- arithmetic and rule questions with no board involved at all
   ("if only 4 consecutive points are available, can five ever be made there?").
   Failure here means the inference itself is broken, independently of vision.
2. **colours** -- the same named point asked three ways: white? empty? black?
   If a white point is called both "white" and "empty", the representation does
   not separate occupancy from emptiness.
3. **counting the conclusion** -- "how many more stones can still be added to
   this line" as a choice over 0/1/2/3+. Same fact as "is it dead", but phrased
   as a measurement, which is the shape jev is good at.

    python3 -m gomoku.infer --repeats 2
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys

from . import backends
from .analysis import maximal_runs
from .board import Move, Stone
from .game import Game
from .backends import battery_for
from .battery import JevError
from .llm_player import BACKEND, JevConfig
from .notation import from_notation, to_notation
from .prompts import PromptStyle, rules_and_state, rules_block
from .rules import RuleSet
from .vision import SIZE, RULES, Case, cases, longest_window

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")

ROOM_OPTIONS = {
    "0": "No further stone of that colour can ever be added to that line.",
    "1": "Exactly 1 more stone can be added to that line.",
    "2": "Exactly 2 more stones can be added to that line.",
    "3_or_more": "3 or more further stones can be added to that line.",
}

#: board-free questions: pure rule arithmetic, truths deliberately mixed
ABSTRACT = {
    "needs_five_points": (
        "In this game a player wins with 5 stones in an unbroken row. To achieve that, "
        "are 5 consecutive points on one single line required?",
        True,
    ),
    "four_points_enough": (
        "Suppose a line has only 4 consecutive points available to a player, and every "
        "point beyond those 4 on that line is occupied by the opponent. Can that player "
        "ever make 5 in a row within that line?",
        False,
    ),
    "six_points_enough": (
        "Suppose a line has 6 consecutive points available to a player, with none of them "
        "occupied by the opponent. Can that player make 5 in a row within that line?",
        True,
    ),
    "four_stones_one_gap": (
        "Suppose a player already has 4 of their own stones in an unbroken line, and the "
        "point immediately next to that line is empty. Can that player make 5 in a row on "
        "their next move?",
        True,
    ),
    "four_stones_no_gap": (
        "Suppose a player has 4 of their own stones in an unbroken line, and the points "
        "immediately beyond both ends of that line are occupied by the opponent. Can that "
        "player ever make 5 in a row on that line?",
        False,
    ),
}


def abstract_request() -> tuple[str, dict, dict]:
    state = (
        rules_block(RULES, BACKEND)
        + "\n\nCONTEXT\nThe questions below are about the rules of the game in general. "
        "No particular position on a board is under discussion; answer from the rules alone."
    )
    questions = {
        qid: {"type": "noul", "instructions": text} for qid, (text, _) in ABSTRACT.items()
    }
    truths = {qid: truth for qid, (_, truth) in ABSTRACT.items()}
    return state, questions, truths


def colour_request(case: Case):
    """Ask about one occupied point and one empty point, three colours each."""
    game = Game.replay([from_notation(c, SIZE) for c in case.moves], RULES)
    board = game.board
    runs = [r for r in maximal_runs(board, Stone.BLACK, RULES.win_length) if r.direction == (0, 1)]
    run = max(runs, key=lambda r: r.length)
    cells = list(run.cells)
    dr, dc = run.direction
    before = Move(cells[0].row - dr, cells[0].col - dc)
    after = Move(cells[-1].row + dr, cells[-1].col + dc)

    # a white point to interrogate, and a genuinely empty one far from the action
    white_point = next(
        (p for p in (before, after) if board.in_bounds(p.row, p.col)
         and board.get(p.row, p.col) is Stone.WHITE),
        None,
    )
    empty_point = next(
        (p for p in board.empties() if p.row <= 2), None  # top rows are untouched
    )
    room = longest_window(board, cells, run.direction, Stone.WHITE) - len(cells)

    questions: dict[str, dict] = {}
    truths: dict[str, object] = {}

    def ask(qid: str, text: str, truth) -> None:
        questions[qid] = {"type": "noul", "instructions": text}
        truths[qid] = truth

    if white_point is not None:
        name = to_notation(white_point, SIZE)
        ask("white_is_white", f"Is the point {name} occupied by a white stone?", True)
        ask("white_is_empty", f"Is the point {name} an empty point with no stone on it?", False)
        ask("white_is_black", f"Is the point {name} occupied by a black stone?", False)
    if empty_point is not None:
        name = to_notation(empty_point, SIZE)
        ask("empty_is_empty", f"Is the point {name} an empty point with no stone on it?", True)
        ask("empty_is_white", f"Is the point {name} occupied by a white stone?", False)
        ask("empty_is_black", f"Is the point {name} occupied by a black stone?", False)

    stones = ", ".join(to_notation(c, SIZE) for c in cells)
    questions["room_left"] = {
        "type": "choice",
        "instructions": (
            f"Consider the black stones {stones}, an unbroken line. Counting only points that "
            "are on the board and not occupied by a white stone, how many further black stones "
            "could still be added to that same line, at either of its two ends?"
        ),
        "criteria": dict(ROOM_OPTIONS),
    }
    truths["room_left"] = "3_or_more" if room >= 3 else str(room)

    state = rules_and_state(game.view(), PromptStyle(), None, BACKEND)
    return state, questions, truths, room


def score(response, truths: dict) -> dict:
    out = {}
    for qid, truth in truths.items():
        answer = response.answers.get(qid) or {}
        if isinstance(truth, bool):
            value = answer.get("noul")
            p = float(value) if isinstance(value, (int, float)) else None
            said = None if p is None else p > 0.5
        else:
            said = (answer.get("choice") or "").strip() or None
            p = answer.get("confidence")
        out[qid] = {
            "truth": truth,
            "p": p,
            "said": said,
            "correct": None if said is None else said == truth,
        }
    return out


def show(scored: dict) -> None:
    for qid, row in scored.items():
        mark = "✓" if row["correct"] else "✗"
        said = row["said"]
        shown = ("y" if said else "n") if isinstance(row["truth"], bool) else said
        truth = ("y" if row["truth"] else "n") if isinstance(row["truth"], bool) else row["truth"]
        p = f"{row['p']:.2f}" if isinstance(row["p"], float) else str(row["p"])
        print(f"      {qid:22} truth={truth:<11} said={str(shown):<11} p={p:<6} {mark}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Isolate the inference step that fails.")
    ap.add_argument("--backend", choices=backends.names(models_only=True), default="jev")
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)

    client = battery_for(args.backend, args.model)
    print(f"backend: {client.describe()}")

    runs: list[dict] = []

    print("\n########## 1) rule arithmetic, with no board at all ##########")
    state, questions, truths = abstract_request()
    for rep in range(args.repeats):
        try:
            response = client.ask(state, questions)
        except JevError as exc:
            print(f"  rep{rep} ERROR {exc}")
            continue
        scored = score(response, truths)
        print(f"  rep{rep}:")
        show(scored)
        runs.append({"group": "abstract", "repeat": rep, "answers": scored})

    print("\n########## 2) empty / white / black, and how much room is left ##########")
    for case in cases():
        state, questions, truths, room = colour_request(case)
        print(f"\n--- {case.id} ---  engine: {room} more black stone(s) fit on that line")
        for rep in range(args.repeats):
            try:
                response = client.ask(state, questions)
            except JevError as exc:
                print(f"  rep{rep} ERROR {exc}")
                continue
            scored = score(response, truths)
            print(f"  rep{rep}:")
            show(scored)
            runs.append({"group": case.id, "repeat": rep, "answers": scored})

    print("\n########## summary ##########")
    per_question: dict[str, list[bool]] = {}
    for row in runs:
        for qid, cell in row["answers"].items():
            if cell["correct"] is not None:
                per_question.setdefault(qid, []).append(cell["correct"])
    for qid, results in per_question.items():
        print(f"  {qid:24} {sum(results)}/{len(results)}")

    out = args.out or RESULTS / f"infer_{client.name}.json"
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
