"""Post-mortem of one move: why was that played instead of the forced answer?

Point it at a recorded game and a ply, and it interrogates the backend about that
exact position in three ways, all in as few requests as possible:

1. **perception** -- a fan-out battery of yes/no questions whose answers the
   engine knows: does the opponent have a three, is it still live, are both its
   ends empty, could they make an unstoppable four next turn. Plus a deliberately
   false control, so "says yes to everything" is detectable.
2. **localisation** -- two `choice` questions over the *same candidate menu the
   player was given*: which point sits at the end of the opponent's line, and
   which point prevents the unstoppable four. If perception is right and
   localisation is right, then knowing was never the problem.
3. **weighing** -- the real move question, asked once per fact-injection level
   (``none``/``span``/``status``/``threats``). Comparing how much probability the
   critical points get isolates "did not consider the threat" from "considered it
   and preferred something else".

    python3 -m gomoku.postmortem --game results/audit-live-0929.json --ply 6
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys

from . import backends
from . import factsheet
from .analysis import maximal_runs, open_four_moves, winning_moves
from .backends import battery_for
from .board import Move, Stone
from .candidates import candidate_points
from .game import Game
from .llm_player import BACKEND, JevConfig, move_question
from .notation import from_notation, to_notation
from .prompts import PromptStyle, rules_and_state
from .render import render_board
from .rules import RuleSet

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")
ORIENTATION = {(0, 1): "row", (1, 0): "column", (1, 1): "diagonal", (1, -1): "diagonal"}


def load_position(path: pathlib.Path, ply: int):
    """Rebuild the position *before* ``ply`` from a recorded game, plus its menu."""
    data = json.loads(path.read_text())
    rules = RuleSet.from_dict(data["rules"])
    played = [m["move"] for m in data["moves"] if m["move"]]
    if not 1 <= ply <= len(played):
        raise SystemExit(f"ply must be 1..{len(played)}")
    history = [from_notation(c, rules.size) for c in played[: ply - 1]]
    game = Game.replay(history, rules)
    record = data["moves"][ply - 1]
    menu = (record["attempts"][0].get("meta") or {}).get("menu")
    return data, game, record, menu


def perception_battery(game: Game, menu: list[str]):
    """Questions the engine can mark, about the opponent's strongest live line."""
    view = game.view()
    size, n = view.size, view.rules.win_length
    me, opp = view.stone, view.opponent
    board = game.board

    opp_runs = [r for r in maximal_runs(board, opp, n) if r.live]
    own_runs = [r for r in maximal_runs(board, me, n) if r.live]
    if not opp_runs:
        raise SystemExit("no live opposing line at this ply -- nothing to explain")
    run = max(opp_runs, key=lambda r: r.length)
    # run.cells is ordered along run.direction: use it for geometry, and a
    # display copy sorted left-to-right / bottom-to-top only for the text
    ordered = list(run.cells)
    cells = sorted(ordered, key=lambda c: (c.col, -c.row))
    coords = " ".join(to_notation(c, size) for c in cells)
    orientation = ORIENTATION[run.direction]
    longest_own = max((r.length for r in own_runs), default=0)
    opp_open_fours = open_four_moves(game, opp)
    dr, dc = run.direction
    ends = [
        to_notation(p, size)
        for p in (
            Move(ordered[0].row - dr, ordered[0].col - dc),
            Move(ordered[-1].row + dr, ordered[-1].col + dc),
        )
        if board.in_bounds(p.row, p.col) and board.is_empty(p)
    ]

    questions: dict[str, dict] = {}
    truths: dict[str, object] = {}

    def noul(qid, text, truth):
        questions[qid] = {"type": "noul", "instructions": text}
        truths[qid] = truth

    noul("opp_has_three",
         f"Does the {opp.label} player have {run.length} stones in an unbroken line "
         f"anywhere on this board?", True)
    noul("opp_line_live",
         f"Look at the {opp.label} stones {coords}, an unbroken {orientation}. Can that line "
         f"still be extended to {n} in a row?", True)
    noul("opp_ends_empty",
         f"Are both of the two points immediately beyond the ends of the {opp.label} line "
         f"{coords} empty points on the board?", len(ends) == 2)
    noul("opp_can_open_four",
         f"Could the {opp.label} player, by placing one stone on their next turn, create a "
         f"line of {n - 1} with TWO different empty points that each complete {n}?",
         bool(opp_open_fours))
    noul("open_four_unstoppable",
         f"If a player has a line of {n - 1} with two different empty points that each "
         f"complete {n}, can the other player still stop them from reaching {n}?", False)
    noul("own_has_three",   # control: white only has a shorter line
         f"Does the {me.label} player have {run.length} stones in an unbroken line "
         f"anywhere on this board?", longest_own >= run.length)

    criteria = {c: f"The point {c}." for c in menu}
    questions["which_end"] = {
        "type": "choice",
        "instructions": (
            f"Which one of these points lies immediately beyond an end of the {opp.label} "
            f"line {coords}?"
        ),
        "criteria": dict(criteria),
    }
    truths["which_end"] = set(ends) & set(menu)
    questions["which_prevents"] = {
        "type": "choice",
        "instructions": (
            f"Which one of these points, if {me.label} plays it now, stops the {opp.label} "
            f"player from creating a line of {n - 1} with two completing points on their "
            "next turn?"
        ),
        "criteria": dict(criteria),
    }
    truths["which_prevents"] = {to_notation(m, size) for m in opp_open_fours} & set(menu)
    return questions, truths, {
        "line": coords,
        "ends": ends,
        "open_four_points": [to_notation(m, size) for m in opp_open_fours],
        # a five on the table outranks an open four: that is then the forced answer
        "opponent_wins": [to_notation(m, size) for m in winning_moves(game, opp)],
    }


def score(response, truths: dict) -> dict:
    out = {}
    for qid, truth in truths.items():
        answer = response.answers.get(qid) or {}
        if isinstance(truth, bool):
            value = answer.get("noul")
            p = float(value) if isinstance(value, (int, float)) else None
            said = None if p is None else p > 0.5
            ok = None if said is None else said is truth
            shown = None if said is None else ("yes" if said else "no")
            want = "yes" if truth else "no"
        else:  # a set of acceptable option keys
            shown = (answer.get("choice") or "").strip() or None
            p = answer.get("confidence")
            ok = None if shown is None else shown in truth
            want = " / ".join(sorted(truth)) or "(none)"
        out[qid] = {"truth": want, "said": shown, "p": p, "correct": ok}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Interrogate one recorded position.")
    ap.add_argument("--game", type=pathlib.Path, required=True, help="an audit/cli .json record")
    ap.add_argument("--ply", type=int, required=True)
    ap.add_argument("--backend", choices=backends.names(models_only=True), default="jev")
    ap.add_argument("--model", default=None)
    ap.add_argument("--facts-levels", default="none,span,status,threats")
    ap.add_argument("--option-facts", default="none",
                    help="comma-separated per-option annotations to compare: none,open_four")
    ap.add_argument("--tactics-in-state", action="store_true",
                    help="extra arm: send the same tactics text in the state field instead "
                         "of the question's instructions (same words, different field)")
    ap.add_argument("--prompt-versions", default=None,
                    help="comma-separated tactics versions to compare (default: the built-in one)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)

    data, game, record, menu = load_position(args.game, args.ply)
    view = game.view()
    config = JevConfig(seed=7)
    if not menu:
        menu = [to_notation(m, view.size) for m in
                candidate_points(view, config, random.Random(config.seed))]

    print(f"post-mortem of {args.game.name} ply {args.ply} - actually played "
          f"{record['move']} ({record['player']})")
    print(f"{view.stone.label} ({view.stone.symbol}) to move\n")
    print(render_board(game.board, game.last_move))
    facts = record.get("analysis") or {}
    print(f"\nengine facts: their open-four points = {facts.get('opponent_open_fours')}  "
          f"my immediate wins = {facts.get('own_wins') or 'none'}")

    client = battery_for(args.backend, args.model)
    print(f"backend: {client.describe()}")
    state = rules_and_state(view, PromptStyle(), None, BACKEND)
    questions, truths, info = perception_battery(game, menu)
    print(f"the line under discussion: {info['line']}  open ends {info['ends']}  "
          f"open-four points {info['open_four_points']}  their fives {info['opponent_wins']}")

    report: dict = {
        "game": str(args.game), "ply": args.ply, "played": record["move"],
        "backend": client.name, "info": info, "perception": [], "weighing": [],
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }

    print("\n########## 1) perception and localisation ##########")
    for rep in range(args.repeats):
        response = client.ask(state, questions)
        scored = score(response, truths)
        report["perception"].append(scored)
        print(f"  rep{rep}:")
        for qid, cell in scored.items():
            mark = "✓" if cell["correct"] else "✗"
            p = f"{cell['p']:.2f}" if isinstance(cell["p"], float) else str(cell["p"])
            print(f"      {qid:22} truth={str(cell['truth']):<12} said={str(cell['said']):<12} "
                  f"p={p:<6} {mark}")

    print("\n########## 2) the same move under each condition ##########")
    # what counts as correct here: block the five if there is one, else stop the open four
    critical = set(info["opponent_wins"]) or set(info["open_four_points"])
    print(f"scored against: {'block the five ' + str(sorted(critical)) if info['opponent_wins'] else 'stop the open four ' + str(sorted(critical))}")
    levels = [x.strip() for x in args.facts_levels.split(",") if x.strip()]
    versions = ([x.strip() for x in args.prompt_versions.split(",") if x.strip()]
                if args.prompt_versions else [config.prompt_version])
    opt_levels = [x.strip() for x in args.option_facts.split(",") if x.strip()]
    arms = [(v, lvl, False, o) for v in versions for lvl in levels for o in opt_levels]
    if args.tactics_in_state:
        arms += [(v, "none", True, "none") for v in versions]
    for version, level, in_state, opt in arms:
        if True:
            from .prompts import render as render_template, tactics_block

            moves_menu = [from_notation(c, view.size) for c in menu]
            if in_state:
                # same words, other field: the question keeps only the role line
                state_l = rules_and_state(
                    view, PromptStyle(), None, BACKEND,
                    tactics_block(view.rules, version, BACKEND),
                )
                q = move_question(view, moves_menu, version, opt)
                q["instructions"] = render_template(
                    "instructions.txt", BACKEND,
                    colour=view.stone.label, symbol=view.stone.symbol, tactics="",
                ).rstrip()
            else:
                state_l = rules_and_state(view, PromptStyle(), None, BACKEND,
                                          factsheet.render(view, level))
                q = move_question(view, moves_menu, version, opt)
            for rep in range(args.repeats):
                response = client.ask(state_l, {"move": q})
                answer = response.answers.get("move") or {}
                chosen = (answer.get("choice") or "").strip()
                probs = answer.get("probabilities") or {}
                mass = sum(v for k, v in probs.items()
                           if k in critical and isinstance(v, (int, float)))
                ranked = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])]
                ranks = {c: (ranked.index(c) + 1 if c in ranked else None) for c in critical}
                ok = chosen in critical
                report["weighing"].append({
                    "prompt_version": version, "facts": level, "option_facts": opt,
                    "tactics_in_state": in_state, "repeat": rep,
                    "chose": chosen, "correct": ok,
                    "confidence": answer.get("confidence"),
                    "critical_mass": round(mass, 3), "critical_ranks": ranks,
                    "top": dict(sorted(probs.items(), key=lambda kv: -kv[1])[:5]),
                })
                where = "state" if in_state else "instr"
                print(f"  facts={level:8} opt={opt:10} rep{rep} → {chosen:4} "
                      f"{'OK' if ok else 'NO'}  mass on the answer {mass:.2f}  rank {ranks}  "
                      f"conf {answer.get('confidence')}")

    out = args.out or RESULTS / f"postmortem_{args.game.stem}_ply{args.ply}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
