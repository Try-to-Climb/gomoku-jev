"""Player interface and non-LLM baselines.

A player is anything that can turn a :class:`~gomoku.game.GameView` into a
:class:`MoveResponse`. The referee owns retry handling, so ``propose`` may be
called several times for the same position with ``feedback`` describing what
was wrong with the previous answer.

The LLM player lives in :mod:`gomoku.llm_player` and is just another subclass:
prompt in, coordinate out.
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .analysis import attack_score, candidate_moves, winning_moves
from .board import Move, Stone
from .game import GameView


@dataclass
class MoveResponse:
    """What a player hands back for one attempt."""

    move: Move | None = None
    #: raw text the player produced, if any (LLM players fill this in)
    raw: str | None = None
    #: why no move could be produced (transport error, unparseable answer, ...)
    error: str | None = None
    latency_s: float = 0.0
    #: token usage, model name, temperature, retries inside the client, ...
    meta: dict = field(default_factory=dict)


class Player(ABC):
    """Base class for all players."""

    def __init__(self, name: str | None = None) -> None:
        self.name = name or type(self).__name__
        self.stone: Stone = Stone.EMPTY

    def start_game(self, stone: Stone, view: GameView) -> None:
        """Called once before the first move of a game."""
        self.stone = stone

    @abstractmethod
    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        """Choose a move for ``view.stone``."""

    def end_game(self, view: GameView) -> None:
        """Called once when the game is over."""

    def close(self) -> None:
        """Release resources (HTTP clients, subprocesses, ...)."""

    def describe(self) -> dict:
        return {"name": self.name, "kind": type(self).__name__}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


class RandomPlayer(Player):
    """Uniformly random legal move. The floor any model must beat."""

    def __init__(self, name: str | None = None, seed: int | None = None) -> None:
        super().__init__(name or "random")
        self.rng = random.Random(seed)
        self.seed = seed

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        start = time.perf_counter()
        moves = view.legal_moves()
        move = self.rng.choice(moves) if moves else None
        return MoveResponse(move=move, latency_s=time.perf_counter() - start)

    def describe(self) -> dict:
        return {**super().describe(), "seed": self.seed}


class HeuristicPlayer(Player):
    """Greedy one-ply bot: take a win, block a win, else maximise pattern value.

    Contiguous-run scoring only (see :mod:`gomoku.analysis`), so it plays a
    solid but beatable game -- a useful fixed yardstick across models.
    """

    def __init__(
        self,
        name: str | None = None,
        seed: int | None = None,
        defence_weight: float = 0.9,
        radius: int = 2,
    ) -> None:
        super().__init__(name or "heuristic")
        self.rng = random.Random(seed)
        self.seed = seed
        self.defence_weight = defence_weight
        self.radius = radius

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        start = time.perf_counter()
        game = view.to_game()
        me, opp = view.stone, view.opponent

        mine = winning_moves(game, me)
        if mine:
            return self._respond(mine[0], start, "win")

        theirs = winning_moves(game, opp)
        if theirs:
            return self._respond(theirs[0], start, "block")

        board = game.board
        win_length = view.rules.win_length
        center = view.rules.center
        best, best_score = None, float("-inf")
        for move in candidate_moves(game, self.radius):
            score = attack_score(board, move, me, win_length)
            score += self.defence_weight * attack_score(board, move, opp, win_length)
            # nudge towards the centre, and break ties reproducibly
            score -= 0.1 * (abs(move.row - center.row) + abs(move.col - center.col))
            score += self.rng.random() * 1e-6
            if score > best_score:
                best, best_score = move, score
        return self._respond(best, start, "pattern", best_score)

    def _respond(self, move: Move | None, start: float, why: str, score: float | None = None):
        meta = {"policy": why}
        if score is not None:
            meta["score"] = round(score, 3)
        return MoveResponse(move=move, latency_s=time.perf_counter() - start, meta=meta)

    def describe(self) -> dict:
        return {
            **super().describe(),
            "seed": self.seed,
            "defence_weight": self.defence_weight,
            "radius": self.radius,
        }


class ScriptedPlayer(Player):
    """Plays a fixed list of moves, then falls back to ``on_exhausted``.

    Used by the tests and for replaying a logged game.
    """

    def __init__(self, moves, name: str | None = None, on_exhausted: Player | None = None) -> None:
        super().__init__(name or "scripted")
        self.moves = [Move(*m) for m in moves]
        self.index = 0
        self.on_exhausted = on_exhausted

    def start_game(self, stone: Stone, view: GameView) -> None:
        super().start_game(stone, view)
        self.index = 0

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        if self.index < len(self.moves):
            move = self.moves[self.index]
            self.index += 1
            return MoveResponse(move=move, meta={"scripted_index": self.index - 1})
        if self.on_exhausted is not None:
            return self.on_exhausted.propose(view, feedback)
        return MoveResponse(error="scripted move list exhausted")


class CallablePlayer(Player):
    """Wrap a ``fn(view) -> Move`` as a player."""

    def __init__(self, fn, name: str | None = None) -> None:
        super().__init__(name or getattr(fn, "__name__", "callable"))
        self.fn = fn

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        start = time.perf_counter()
        try:
            move = self.fn(view)
        except Exception as exc:  # noqa: BLE001 - a player crash is a game event, not a test failure
            return MoveResponse(error=f"{type(exc).__name__}: {exc}", latency_s=time.perf_counter() - start)
        return MoveResponse(move=None if move is None else Move(*move), latency_s=time.perf_counter() - start)
