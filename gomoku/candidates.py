"""Candidate points: turning a position into a bounded menu of legal moves.

Shared by every backend that answers with a *choice* rather than free text
(jev's ``choice`` question, and chat models forced into the same shape), so
different models are always compared on the same menu.

jev needs a bounded option set, and offering all 225 points of an empty 15x15
board costs roughly 3.5x the input tokens and 7x the output tokens of a
30-option menu. Hence the shortlist -- with two safeguards that keep the
measurement honest:

* every immediate win for either side is always on the menu, so a missed win or
  a missed block is the model's decision and not an artefact of our shortlist;
* options are presented in shuffled order, so their position leaks no ranking.

Note the residual confound: when the cap bites, *which* non-tactical points make
the shortlist is decided by the local heuristic. Set ``candidates="all"`` with a
large enough ``max_candidates`` to remove it entirely (practical on 9x9).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from string import Template

from .analysis import attack_score, candidate_moves, winning_moves
from .board import Move
from .game import GameView
from .notation import LETTERS, from_notation, to_notation

ALL = "all"
NEAR = "near"


@dataclass
class CandidateConfig:
    """How the option menu is built. Shared by all choice-style backends."""

    #: "near" = points within ``radius`` of a stone, "all" = every legal point
    candidates: str = NEAR
    max_candidates: int = 30
    radius: int = 2
    #: keep every immediate win/block point on the menu
    include_tactical: bool = True
    #: randomise option order so position in the list carries no signal
    shuffle: bool = True
    seed: int | None = None

    def candidate_dict(self) -> dict:
        return {
            "candidates": self.candidates,
            "max_candidates": self.max_candidates,
            "radius": self.radius,
            "include_tactical": self.include_tactical,
            "shuffle": self.shuffle,
            "seed": self.seed,
        }


def candidate_points(view: GameView, config: CandidateConfig, rng: random.Random) -> list[Move]:
    """The option set offered for this position."""
    game = view.to_game()
    legal = game.legal_moves()
    if not legal:
        return []

    pool = legal if config.candidates == ALL else candidate_moves(game, config.radius)

    must_keep: list[Move] = []
    if config.include_tactical:
        must_keep = winning_moves(game, view.stone) + winning_moves(game, view.opponent)

    if len(pool) > config.max_candidates:
        board = game.board
        win_length = view.rules.win_length
        pool = sorted(
            pool,
            key=lambda m: attack_score(board, m, view.stone, win_length)
            + attack_score(board, m, view.opponent, win_length),
            reverse=True,
        )[: config.max_candidates]

    seen = set(pool)
    for move in must_keep:
        if move not in seen:
            pool.append(move)
            seen.add(move)

    if config.shuffle:
        pool = list(pool)
        rng.shuffle(pool)
    return pool


#: what may be attached to each option, beyond its coordinates
OPTION_PLAIN = "none"
OPTION_OPEN_FOUR = "open_four"
#: a graded quantity instead of a verdict: how much of a win the opponent still has
#: available after this move. Never partitions the options into safe/unsafe, so the
#: model has to compare numbers rather than read an answer off the text.
OPTION_WINDOWS = "windows"
#: the same number, phrased as plainly as possible (one number, no sub-clauses)
OPTION_WAYS = "ways"
#: the most urgent consequence, five level first. ``open_four`` alone goes blind the
#: moment the opponent already has a four -- then its answer is "no open four" for
#: every option, which is true and useless. This level always reports the top threat.
OPTION_THREAT = "threat"
#: ``threat`` reports only what the OPPONENT can do next, so a move that wins on the
#: spot reads the same as any quiet move -- jev missed four immediate wins in one game
#: because of it. ``full`` puts "this move wins" on top of the same ladder.
OPTION_FULL = "full"
OPTION_FACTS = (OPTION_PLAIN, OPTION_OPEN_FOUR, OPTION_WINDOWS, OPTION_WAYS,
                OPTION_THREAT, OPTION_FULL)


def option_descriptions(
    view: GameView, candidates: list[Move], facts: str = OPTION_PLAIN
) -> dict[str, str]:
    """``{"H8": "Play at column H, row 8."}`` plus, optionally, one fact per option.

    With ``facts="open_four"`` each option states whether the opponent could still
    build an unstoppable open four *after* that move -- a consequence the engine
    computes by playing the move and re-running
    :func:`gomoku.analysis.open_four_moves`. It is a fact about the option, not a
    recommendation: several options may be safe, and nothing says which to prefer.

    Note this is a strong hint in a defensive position, where only the blocking
    points are safe. Its value is in testing whether the ``criteria`` text is read
    at all -- every other injection so far went into the ``state``.
    """
    # local imports: analysis imports this module
    from .analysis import (
        open_four_moves,
        window_stats,
        winning_moves,
        wins_immediately,
    )
    from .prompts import load_template

    size = view.size
    if facts not in OPTION_FACTS:
        raise ValueError(f"unknown option facts {facts!r}; known: {list(OPTION_FACTS)}")

    parts = _sections(load_template("options.txt"))
    out: dict[str, str] = {}
    game = view.to_game() if facts != OPTION_PLAIN else None
    for move in candidates:
        values = {"col": LETTERS[move.col], "row": size - move.row}
        if facts == OPTION_PLAIN:
            template = parts["plain"]
        elif facts == OPTION_OPEN_FOUR:
            game.board.place(move, view.stone)
            try:
                still = bool(open_four_moves(game, view.opponent))
            finally:
                game.board.remove(move)
            template = parts["opponent_open_four_yes" if still else "opponent_open_four_no"]
        elif facts in (OPTION_THREAT, OPTION_FULL):
            wins_now = (
                facts == OPTION_FULL
                and wins_immediately(game.board, move, view.stone, view.rules)
            )
            if wins_now:
                values.update(n=view.rules.win_length)
                out[to_notation(move, size)] = Template(parts["full_win"]).substitute(**values)
                continue
            game.board.place(move, view.stone)
            try:
                fives = winning_moves(game, view.opponent)
                open_fours = [] if fives else open_four_moves(game, view.opponent)
            finally:
                game.board.remove(move)
            if fives:
                template = parts["threat_five"]
                values.update(
                    n=view.rules.win_length,
                    points=" or ".join(to_notation(m, size) for m in fives),
                )
            elif open_fours:
                template = parts["threat_open_four"]
            else:
                template = parts["threat_none"]
        else:  # OPTION_WINDOWS / OPTION_WAYS: a graded number, no verdict
            game.board.place(move, view.stone)
            try:
                best, ways = window_stats(
                    game.board, view.opponent, view.rules.win_length
                )
            finally:
                game.board.remove(move)
            template = parts["windows" if facts == OPTION_WINDOWS else "ways"]
            values.update(best=best, ways=ways, n=view.rules.win_length)
        out[to_notation(move, size)] = Template(template).substitute(**values)
    return out


def _sections(text: str) -> dict[str, str]:
    """Split a ``[name]``-delimited template into named chunks."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = out.setdefault(line[1:-1], [])
        elif current is not None:
            current.append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


def read_choice(answer: dict, size: int, allowed: set[str]) -> tuple[Move | None, str | None, dict]:
    """Turn a choice-shaped answer into a move plus metadata.

    Accepts jev's answer object and the same shape from a chat model. Falls back
    to the argmax of ``probabilities`` when ``choice`` is missing.
    """
    meta: dict = {}
    if not isinstance(answer, dict):
        return None, f"unexpected answer type {type(answer).__name__}", meta

    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict) and probabilities:
        top = sorted(
            ((k, v) for k, v in probabilities.items() if isinstance(v, (int, float))),
            key=lambda kv: -kv[1],
        )
        meta["top_probabilities"] = {k: v for k, v in top[:5]}
    if "confidence" in answer:
        meta["confidence"] = answer["confidence"]

    choice = answer.get("choice")
    if not isinstance(choice, str) or not choice.strip():
        if isinstance(probabilities, dict) and probabilities:
            choice = max(probabilities, key=lambda k: probabilities.get(k) or 0)
            meta["choice_from"] = "argmax"
        else:
            return None, "answer has neither 'choice' nor usable 'probabilities'", meta

    coord = choice.strip().upper()
    if coord not in allowed:
        meta["offered"] = len(allowed)
        return None, f"the model chose {coord!r}, which was not one of the offered points", meta
    try:
        return from_notation(coord, size), None, meta
    except Exception as exc:  # noqa: BLE001 - surfaced as a move error, not a crash
        return None, f"cannot read {coord!r} as a coordinate: {exc}", meta
