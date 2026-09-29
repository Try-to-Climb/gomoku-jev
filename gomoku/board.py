"""Board state and line measurement.

Internal coordinates are 0-indexed ``(row, col)`` with row 0 at the *top*.
Human/LLM facing coordinates live in :mod:`gomoku.notation`.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterator, NamedTuple


class Move(NamedTuple):
    row: int
    col: int


class Stone(IntEnum):
    EMPTY = 0
    BLACK = 1
    WHITE = 2

    @property
    def opponent(self) -> "Stone":
        if self is Stone.BLACK:
            return Stone.WHITE
        if self is Stone.WHITE:
            return Stone.BLACK
        return Stone.EMPTY

    @property
    def symbol(self) -> str:
        return _SYMBOLS[self]

    @property
    def label(self) -> str:
        return _LABELS[self]


_SYMBOLS = {Stone.EMPTY: ".", Stone.BLACK: "X", Stone.WHITE: "O"}
_LABELS = {Stone.EMPTY: "empty", Stone.BLACK: "black", Stone.WHITE: "white"}

#: The four axes a five-in-a-row can run along (horizontal, vertical, both diagonals).
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


class Board:
    """A square grid of stones.

    Only geometry lives here: the board knows nothing about who wins, that is
    :func:`gomoku.rules.judge_placement`'s job.
    """

    __slots__ = ("size", "_cells", "_empty_count")

    def __init__(self, size: int = 15) -> None:
        if size < 5:
            raise ValueError(f"board size must be >= 5, got {size}")
        self.size = size
        self._cells: list[Stone] = [Stone.EMPTY] * (size * size)
        self._empty_count = size * size

    # ------------------------------------------------------------------ access
    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.size and 0 <= col < self.size

    def get(self, row: int, col: int) -> Stone:
        return self._cells[row * self.size + col]

    def __getitem__(self, move: Move) -> Stone:
        return self.get(move[0], move[1])

    def is_empty(self, move: Move) -> bool:
        return self.get(move[0], move[1]) is Stone.EMPTY

    @property
    def empty_count(self) -> int:
        return self._empty_count

    @property
    def is_full(self) -> bool:
        return self._empty_count == 0

    def empties(self) -> Iterator[Move]:
        size = self.size
        for i, cell in enumerate(self._cells):
            if cell is Stone.EMPTY:
                yield Move(i // size, i % size)

    def occupied(self) -> Iterator[tuple[Move, Stone]]:
        size = self.size
        for i, cell in enumerate(self._cells):
            if cell is not Stone.EMPTY:
                yield Move(i // size, i % size), cell

    # ----------------------------------------------------------------- mutate
    def place(self, move: Move, stone: Stone) -> None:
        row, col = move
        if not self.in_bounds(row, col):
            raise ValueError(f"{move} is outside a {self.size}x{self.size} board")
        idx = row * self.size + col
        if self._cells[idx] is not Stone.EMPTY:
            raise ValueError(f"{move} is already occupied by {self._cells[idx].label}")
        self._cells[idx] = stone
        self._empty_count -= 1

    def remove(self, move: Move) -> Stone:
        idx = move[0] * self.size + move[1]
        stone = self._cells[idx]
        if stone is not Stone.EMPTY:
            self._cells[idx] = Stone.EMPTY
            self._empty_count += 1
        return stone

    def copy(self) -> "Board":
        clone = Board.__new__(Board)
        clone.size = self.size
        clone._cells = list(self._cells)
        clone._empty_count = self._empty_count
        return clone

    # ------------------------------------------------------------ measurement
    def line_info(self, move: Move, stone: Stone, direction: tuple[int, int]) -> tuple[int, int]:
        """Return ``(length, open_ends)`` for the run through ``move`` along ``direction``.

        ``move`` is counted as belonging to ``stone`` whether or not it is
        actually placed, so this works for hypothetical moves too.
        ``open_ends`` counts how many of the two ends are an empty in-bounds cell.
        """
        dr, dc = direction
        length = 1
        open_ends = 0
        for sign in (1, -1):
            row, col = move[0] + dr * sign, move[1] + dc * sign
            while self.in_bounds(row, col) and self.get(row, col) is stone:
                length += 1
                row += dr * sign
                col += dc * sign
            if self.in_bounds(row, col) and self.get(row, col) is Stone.EMPTY:
                open_ends += 1
        return length, open_ends

    def line_lengths(self, move: Move, stone: Stone) -> tuple[int, int, int, int]:
        """Run length through ``move`` for each of the four axes."""
        return tuple(self.line_info(move, stone, d)[0] for d in DIRECTIONS)  # type: ignore[return-value]

    def max_run(self, move: Move, stone: Stone) -> int:
        return max(self.line_lengths(move, stone))

    def to_rows(self) -> tuple[tuple[Stone, ...], ...]:
        size = self.size
        return tuple(
            tuple(self._cells[r * size : (r + 1) * size]) for r in range(size)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Board):
            return NotImplemented
        return self.size == other.size and self._cells == other._cells

    def __repr__(self) -> str:
        return f"Board(size={self.size}, stones={self.size * self.size - self._empty_count})"
