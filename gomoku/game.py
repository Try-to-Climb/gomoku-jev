"""The game object: turn order, legality, and terminal detection.

:class:`Game` is the single source of truth about a position. Players never
mutate it; they receive a read-only :class:`GameView` and return a move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .board import Board, Move, Stone
from .notation import to_notation
from .render import render_board, render_history, render_stone_lists
from .rules import (
    GAME_OVER,
    OCCUPIED,
    OUT_OF_BOUNDS,
    IN_PROGRESS,
    Opening,
    Outcome,
    RuleSet,
    Status,
    check_opening,
    judge_placement,
)


class IllegalMove(Exception):
    def __init__(self, move: Move, reason: str) -> None:
        super().__init__(f"illegal move {move}: {reason}")
        self.move = move
        self.reason = reason


class Game:
    def __init__(self, rules: RuleSet | None = None) -> None:
        self.rules = rules or RuleSet()
        self.board = Board(self.rules.size)
        self.history: list[Move] = []
        self.outcome: Outcome = IN_PROGRESS

    # ------------------------------------------------------------------ state
    @property
    def ply(self) -> int:
        return len(self.history)

    @property
    def status(self) -> Status:
        return self.outcome.status

    @property
    def winner(self) -> Stone | None:
        return self.outcome.winner

    @property
    def is_over(self) -> bool:
        return self.outcome.status.is_over

    @property
    def current_stone(self) -> Stone:
        return Stone.BLACK if self.ply % 2 == 0 else Stone.WHITE

    @property
    def last_move(self) -> Move | None:
        return self.history[-1] if self.history else None

    # --------------------------------------------------------------- legality
    def check(self, move: Move) -> str | None:
        """Return ``None`` if ``move`` is legal now, else a reason code."""
        if self.is_over:
            return GAME_OVER
        row, col = move
        if not self.board.in_bounds(row, col):
            return OUT_OF_BOUNDS
        if self.board.get(row, col) is not Stone.EMPTY:
            return OCCUPIED
        return check_opening(self.rules, self.ply, move)

    def is_legal(self, move: Move) -> bool:
        return self.check(move) is None

    def legal_moves(self) -> list[Move]:
        if self.is_over:
            return []
        if self.rules.opening is Opening.FREE or self.ply > 2:
            return list(self.board.empties())
        return [m for m in self.board.empties() if self.check(m) is None]

    def iter_legal_moves(self) -> Iterator[Move]:
        if self.is_over:
            return
        fast = self.rules.opening is Opening.FREE or self.ply > 2
        for move in self.board.empties():
            if fast or self.check(move) is None:
                yield move

    # ------------------------------------------------------------------- play
    def play(self, move: Move) -> Outcome:
        reason = self.check(move)
        if reason is not None:
            raise IllegalMove(move, reason)
        stone = self.current_stone
        self.board.place(move, stone)
        self.history.append(move)
        self.outcome = judge_placement(self.board, move, stone, self.rules)
        if not self.is_over and self.rules.max_plies is not None and self.ply >= self.rules.max_plies:
            self.outcome = Outcome(Status.DRAW, None, "max_plies_reached")
        return self.outcome

    def resign(self, stone: Stone, reason: str = "resignation") -> Outcome:
        winner = stone.opponent
        self.outcome = Outcome(
            Status.BLACK_WIN if winner is Stone.BLACK else Status.WHITE_WIN, winner, reason
        )
        return self.outcome

    def declare_draw(self, reason: str = "agreed_draw") -> Outcome:
        self.outcome = Outcome(Status.DRAW, None, reason)
        return self.outcome

    # ------------------------------------------------------------------ views
    def view(self, stone: Stone | None = None) -> "GameView":
        return GameView(
            rules=self.rules,
            board=self.board.copy(),
            stone=stone or self.current_stone,
            history=tuple(self.history),
            outcome=self.outcome,
        )

    def notation(self) -> list[str]:
        return [to_notation(m, self.rules.size) for m in self.history]

    @classmethod
    def replay(cls, moves, rules: RuleSet | None = None) -> "Game":
        game = cls(rules)
        for move in moves:
            game.play(Move(*move))
        return game

    def __repr__(self) -> str:
        return f"Game(ply={self.ply}, status={self.status.value}, turn={self.current_stone.label})"


@dataclass(frozen=True)
class GameView:
    """Everything a player is allowed to see. The board is a private copy."""

    rules: RuleSet
    board: Board
    stone: Stone
    history: tuple[Move, ...]
    outcome: Outcome = IN_PROGRESS
    #: free-form extras (e.g. remaining time), reserved for callers
    extras: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.rules.size

    @property
    def ply(self) -> int:
        return len(self.history)

    @property
    def opponent(self) -> Stone:
        return self.stone.opponent

    @property
    def last_move(self) -> Move | None:
        return self.history[-1] if self.history else None

    def to_game(self) -> Game:
        """A writable :class:`Game` sharing this position -- handy for local search."""
        game = Game(self.rules)
        game.board = self.board.copy()
        game.history = list(self.history)
        game.outcome = self.outcome
        return game

    def legal_moves(self) -> list[Move]:
        return self.to_game().legal_moves()

    def notation(self, move: Move) -> str:
        return to_notation(move, self.size)

    def board_text(self, coords: bool = True) -> str:
        return render_board(self.board, self.last_move, coords)

    def stone_lists(self) -> str:
        return render_stone_lists(self.board)

    def history_text(self) -> str:
        return render_history(self.history, self.size)
