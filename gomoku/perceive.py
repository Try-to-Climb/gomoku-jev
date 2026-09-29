"""Does jev *see* the threats, or only fail to act on them?

One jev request can carry many questions about the same state (fan-out), so this
asks, in a single call: the usual "which point do you play" choice, plus a
handful of yes/no questions whose answers the engine already knows. Every
question is objectively scorable, so the result separates two very different
diagnoses:

* perception wrong -> the board representation is the problem, and no amount of
  tactical wording will help;
* perception right but the move wrong -> the instructions or the option design
  are the problem.

The dead/live line pair is the key test: a run that the opponent has already
blocked can never reach five, so adding to it or blocking it are both wasted
moves -- exactly the mistake jev keeps making. The live-line question is the
control: a model that just answers "yes" to everything gets one right and one
wrong.

    python3 -m gomoku.perceive
    python3 -m gomoku.perceive --repeats 3 --only stop_open_four,game1_ply9
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys
from dataclasses import dataclass, field

from .analysis import Run, maximal_runs, open_four_moves, winning_moves
from .board import Move, Stone
from .candidates import candidate_points
from .game import Game, GameView
from .jev_client import JevClient, JevError
from .llm_player import BACKEND, JevConfig, move_question
from .notation import from_notation, to_notation
from .prompts import rules_and_state
from .rules import RuleSet

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")

ORIENTATION = {
    (0, 1): "horizontal line along a row",
    (1, 0): "vertical line along a column",
    (1, 1): "diagonal line",
    (1, -1): "diagonal line",
}

LINE_QUESTION = (
    "Look at these stones of the {side} player: {coords}. They form an unbroken "
    "{orientation}. Counting the empty points at both of its ends and the edges of "
    "the board, can this line still be extended to {n} in a row?"
)


def coords(*texts, size=15):
    return [from_notation(t, size) for t in texts]


@dataclass
class Position:
    id: str
    why: str
    game: Game


def positions() -> list[Position]:
    r9 = RuleSet(size=9)
    return [
        Position(
            "must_block",
            "black has a four; white must take I8",
            Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8")),
        ),
        Position(
            "must_win",
            "white can complete five at C9 or H9",
            Game.replay(coords("A1", "D9", "A3", "E9", "A5", "F9", "A7", "G9", "B1")),
        ),
        Position(
            "make_open_four",
            "white has D4-D6 open at both ends: D7 or D3 wins",
            Game.replay(coords("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", "C5", size=9), r9),
        ),
        Position(
            "stop_open_four",
            "black must break white's open three D4-D6",
            Game.replay(coords("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", size=9), r9),
        ),
        Position(
            "game1_ply9",
            "from the p2 audit: white has G5-G7 open; jev (black) played F7 and lost",
            Game.replay(coords("E5", "D5", "F5", "G5", "H5", "G6", "F6", "G7", size=9), r9),
        ),
        Position(
            "game2_ply12",
            "from the p2 audit: jev (white) played D6 instead of G7/C3",
            Game.replay(
                coords("E5", "D5", "E4", "E3", "E6", "E7", "F4", "F5", "D4", "C4", "F6", size=9),
                r9,
            ),
        ),
    ]


@dataclass
class Fact:
    question_id: str
    truth: bool
    note: str = ""
    answer: float | None = None

    @property
    def said(self) -> bool | None:
        return None if self.answer is None else self.answer > 0.5

    @property
    def correct(self) -> bool | None:
        return None if self.said is None else self.said is self.truth

    def to_dict(self) -> dict:
        return {
            "question": self.question_id,
            "truth": self.truth,
            "note": self.note,
            "probability": self.answer,
            "said": self.said,
            "correct": self.correct,
        }


def pick_run(board, stone: Stone, win_length: int, want_live: bool) -> Run | None:
    """The longest run of ``stone`` that is (or is not) still able to reach five."""
    candidates = [r for r in maximal_runs(board, stone, win_length) if r.live is want_live]
    return candidates[0] if candidates else None


def describe_run(run: Run, side: str, size: int, win_length: int) -> str:
    return LINE_QUESTION.format(
        side=side,
        coords=", ".join(to_notation(c, size) for c in run.cells),
        orientation=ORIENTATION[run.direction],
        n=win_length,
    )


def build_request(view: GameView, candidates: list[Move], version: str):
    """The fan-out question set plus the engine's answers to the factual ones."""
    game = view.to_game()
    me, opp = view.stone, view.opponent
    size, win_length = view.size, view.rules.win_length
    board = game.board

    my_wins = winning_moves(game, me)
    opp_wins = winning_moves(game, opp)
    my_open_fours = open_four_moves(game, me)
    opp_open_fours = open_four_moves(game, opp)

    questions: dict[str, dict] = {"move": move_question(view, candidates, version)}
    facts: list[Fact] = []

    def noul(qid: str, text: str, truth: bool, note: str = "") -> None:
        questions[qid] = {"type": "noul", "instructions": text}
        facts.append(Fact(qid, truth, note))

    noul(
        "win_now",
        f"Can the {me.label} player complete {win_length} in a row by placing a single "
        "stone on this turn?",
        bool(my_wins),
        ", ".join(to_notation(m, size) for m in my_wins),
    )
    noul(
        "opponent_four",
        f"Does the {opp.label} player already have {win_length - 1} in a line such that a "
        f"single stone would complete {win_length} for them on their next turn?",
        bool(opp_wins),
        ", ".join(to_notation(m, size) for m in opp_wins),
    )
    # an open-four question is meaningless once a five is already on the table: any
    # move leaves two completing points, so the answer would be trivially yes
    if not opp_wins:
        noul(
            "opponent_open_four",
            f"Could the {opp.label} player, by placing one stone on their next turn, create a "
            f"line of {win_length - 1} with TWO different empty points that each complete "
            f"{win_length} (an open four that cannot be blocked)?",
            bool(opp_open_fours),
            ", ".join(to_notation(m, size) for m in opp_open_fours),
        )
    if not my_wins:
        noul(
            "own_open_four",
            f"Could the {me.label} player, by placing one stone on this turn, create a line of "
            f"{win_length - 1} with TWO different empty points that each complete {win_length}?",
            bool(my_open_fours),
            ", ".join(to_notation(m, size) for m in my_open_fours),
        )

    # the hypothesis: dead lines are invisible. The live line is the control.
    for want_live, qid in ((False, "own_dead_line_alive"), (True, "own_live_line_alive")):
        run = pick_run(board, me, win_length, want_live)
        if run is not None:
            noul(
                qid,
                describe_run(run, me.label, size, win_length),
                run.live,
                f"{'live' if run.live else 'dead'} run of {run.length}",
            )
    for want_live, qid in ((False, "opponent_dead_line_alive"), (True, "opponent_live_line_alive")):
        run = pick_run(board, opp, win_length, want_live)
        if run is not None:
            noul(
                qid,
                describe_run(run, opp.label, size, win_length),
                run.live,
                f"{'live' if run.live else 'dead'} run of {run.length}",
            )

    best: list[Move] = my_wins or (opp_wins if len(opp_wins) == 1 else []) or my_open_fours or opp_open_fours
    return questions, facts, best


def run_position(client: JevClient, position: Position, config: JevConfig, rng) -> dict:
    view = position.game.view()
    candidates = candidate_points(view, config, rng)
    questions, facts, best = build_request(view, candidates, config.prompt_version)
    state = rules_and_state(view, config.style, None, BACKEND)

    print(f"\n--- {position.id} ---")
    print(position.why)
    print(f"轮到 {view.stone.label} ({view.stone.symbol})，候选 {len(candidates)} 个，"
          f"客观最佳 {[to_notation(m, view.size) for m in best] or '无明确最佳'}")

    try:
        response = client.ask(state, questions)
    except JevError as exc:
        print(f"ERROR: {exc}")
        return {"id": position.id, "error": str(exc)}

    move_answer = response.answers.get("move") or {}
    chosen = (move_answer.get("choice") or "").strip().upper() or None
    move_ok = bool(chosen and best and from_notation(chosen, view.size) in best)

    for fact in facts:
        answer = response.answers.get(fact.question_id) or {}
        value = answer.get("noul")
        fact.answer = float(value) if isinstance(value, (int, float)) else None

    print(f"{'question':28} {'引擎':>6} {'jev':>6} {'p':>6}  判定")
    for fact in facts:
        mark = "—" if fact.correct is None else ("✓" if fact.correct else "✗")
        said = "—" if fact.said is None else ("yes" if fact.said else "no")
        truth = "yes" if fact.truth else "no"
        note = f"  ({fact.note})" if fact.note else ""
        print(f"{fact.question_id:28} {truth:>6} {said:>6} "
              f"{fact.answer if fact.answer is not None else float('nan'):6.2f}  {mark}{note}")
    verdict = "✓" if move_ok else ("✗" if best else "—")
    print(f"{'move':28} {'/'.join(to_notation(m, view.size) for m in best) or '—':>13} "
          f"{chosen or '—':>6}  {verdict}   confidence {move_answer.get('confidence')}")

    right = [f for f in facts if f.correct is True]
    return {
        "id": position.id,
        "why": position.why,
        "stone": view.stone.label,
        "history": [to_notation(m, view.size) for m in view.history],
        "best_moves": [to_notation(m, view.size) for m in best],
        "chose": chosen,
        "move_correct": move_ok if best else None,
        "move_confidence": move_answer.get("confidence"),
        "perception": [f.to_dict() for f in facts],
        "perception_score": f"{len(right)}/{len(facts)}",
        "usage": response.usage,
        "latency_s": round(response.latency_s, 3),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ask jev what it can see, not just what it plays.")
    ap.add_argument("--model", default=None)
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--templates", default=None, metavar="DIR")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", default=None, help="comma-separated position ids")
    ap.add_argument("--candidates", choices=["near", "all"], default="near")
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    config = JevConfig(
        model=args.model or JevConfig().model,
        candidates=args.candidates,
        max_candidates=args.max_candidates,
        seed=args.seed,
    )
    if args.prompt_version:
        config.prompt_version = args.prompt_version

    wanted = {s.strip() for s in args.only.split(",")} if args.only else None
    chosen = [p for p in positions() if wanted is None or p.id in wanted]
    if not chosen:
        raise SystemExit("no positions selected")

    client = JevClient(model=config.model)
    print(f"model: {config.model}  prompt: {config.prompt_version}  key: {client.key_fingerprint}")

    runs = []
    for rep in range(args.repeats):
        if args.repeats > 1:
            print(f"\n########## repeat {rep} ##########")
        for position in chosen:
            record = run_position(client, position, config, random.Random(args.seed + rep))
            record["repeat"] = rep
            runs.append(record)

    # summary: perception accuracy per question, and move accuracy
    per_question: dict[str, list[bool]] = {}
    for record in runs:
        for fact in record.get("perception", []):
            if fact["correct"] is not None:
                per_question.setdefault(fact["question"], []).append(fact["correct"])
    print("\n感知准确率（全部局面合计）")
    for qid, results in per_question.items():
        print(f"  {qid:28} {sum(results)}/{len(results)}")
    scored = [r for r in runs if r.get("move_correct") is not None]
    if scored:
        print(f"  {'move (客观最佳)':28} {sum(r['move_correct'] for r in scored)}/{len(scored)}")

    out = args.out or RESULTS / "perception_jev.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {
            "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "model": config.model,
            "prompt_version": config.prompt_version,
            "runs": runs,
        },
        indent=2, ensure_ascii=False,
    ))
    print(f"\nwrote {out}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
