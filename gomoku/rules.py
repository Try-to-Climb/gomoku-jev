"""Rule configuration and the win/loss/draw verdict.

Supported rule dials:

* ``size`` / ``win_length`` -- board edge and how many stones in a row win.
* ``overline`` -- what a run *longer* than ``win_length`` means:
  ``WIN`` (free-style gomoku, the default), ``IGNORE`` (standard gomoku: only
  an exact five wins, a six is inert) or ``FORBIDDEN`` (renju: making an
  overline loses the game, by default only for black).
* ``opening`` -- ``FREE``, ``CENTER_FIRST`` (black's first stone must be the
  centre) or ``PRO`` (centre first, and black's second stone must be at least
  three intersections away from the centre).
* ``illegal_move`` -- how the referee reacts when a player cannot produce a
  legal move within ``max_retries`` attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from .board import DIRECTIONS, Board, Move, Stone


class Status(str, Enum):
    IN_PROGRESS = "in_progress"
    BLACK_WIN = "black_win"
    WHITE_WIN = "white_win"
    DRAW = "draw"

    @property
    def is_over(self) -> bool:
        return self is not Status.IN_PROGRESS


class OverlineRule(str, Enum):
    WIN = "win"
    IGNORE = "ignore"
    FORBIDDEN = "forbidden"


class Opening(str, Enum):
    FREE = "free"
    CENTER_FIRST = "center_first"
    PRO = "pro"


class IllegalMovePolicy(str, Enum):
    #: the offending player loses immediately
    FORFEIT = "forfeit"
    #: the referee plays a uniformly random legal move on the player's behalf
    RANDOM_FALLBACK = "random_fallback"


class Outcome(NamedTuple):
    status: Status
    winner: Stone | None
    reason: str


IN_PROGRESS = Outcome(Status.IN_PROGRESS, None, "")

# illegal-move reason codes (stable strings, used in logs and metrics)
OUT_OF_BOUNDS = "out_of_bounds"
OCCUPIED = "occupied"
GAME_OVER = "game_over"
OPENING_CENTER_REQUIRED = "opening_center_required"
OPENING_PRO_DISTANCE = "opening_pro_distance"


def win_status(stone: Stone) -> Status:
    return Status.BLACK_WIN if stone is Stone.BLACK else Status.WHITE_WIN


@dataclass(frozen=True)
class RuleSet:
    size: int = 15
    win_length: int = 5
    overline: OverlineRule = OverlineRule.WIN
    overline_forbidden_for: frozenset[Stone] = frozenset({Stone.BLACK})
    opening: Opening = Opening.FREE
    #: extra attempts granted after an unusable answer (0 = one shot only)
    max_retries: int = 2
    illegal_move: IllegalMovePolicy = IllegalMovePolicy.FORFEIT
    #: hard cap on plies; ``None`` means "until the board is full"
    max_plies: int | None = None
    #: per-move wall-clock budget handed to players (they enforce it)
    move_timeout_s: float | None = 120.0
    metadata: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.size < 5:
            raise ValueError("size must be >= 5")
        if self.size > 26:
            raise ValueError("size must be <= 26 (column letters A-Z)")
        if self.win_length < 3:
            raise ValueError("win_length must be >= 3")
        if self.win_length > self.size:
            raise ValueError("win_length cannot exceed size")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.opening is not Opening.FREE and self.size % 2 == 0:
            raise ValueError(f"{self.opening.value} opening needs an odd board size")
        object.__setattr__(self, "overline_forbidden_for", frozenset(self.overline_forbidden_for))

    @property
    def center(self) -> Move:
        mid = self.size // 2
        return Move(mid, mid)

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "win_length": self.win_length,
            "overline": self.overline.value,
            "overline_forbidden_for": sorted(s.label for s in self.overline_forbidden_for),
            "opening": self.opening.value,
            "max_retries": self.max_retries,
            "illegal_move": self.illegal_move.value,
            "max_plies": self.max_plies,
            "move_timeout_s": self.move_timeout_s,
            **({"metadata": self.metadata} if self.metadata else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RuleSet":
        data = dict(data)
        if "overline" in data:
            data["overline"] = OverlineRule(data["overline"])
        if "opening" in data:
            data["opening"] = Opening(data["opening"])
        if "illegal_move" in data:
            data["illegal_move"] = IllegalMovePolicy(data["illegal_move"])
        if "overline_forbidden_for" in data:
            by_label = {s.label: s for s in (Stone.BLACK, Stone.WHITE)}
            data["overline_forbidden_for"] = frozenset(
                by_label[x] if isinstance(x, str) else Stone(x)
                for x in data["overline_forbidden_for"]
            )
        return cls(**data)


def describe(rules: RuleSet) -> str:
    """One-paragraph plain-text rule summary (also fed to LLM players)."""
    lines = [
        f"Board: {rules.size}x{rules.size}. Black (X) moves first, then players alternate.",
        f"Goal: be the first to get {rules.win_length} of your own stones in an unbroken "
        "row, horizontally, vertically or diagonally.",
    ]
    if rules.overline is OverlineRule.WIN:
        lines.append(f"A run longer than {rules.win_length} also wins.")
    elif rules.overline is OverlineRule.IGNORE:
        lines.append(
            f"Only an exact run of {rules.win_length} wins; a longer run does not win."
        )
    else:
        who = ", ".join(sorted(s.label for s in rules.overline_forbidden_for))
        lines.append(
            f"For {who}, only an exact run of {rules.win_length} wins and making a longer "
            f"run is a forbidden move that immediately loses the game; the other side wins "
            f"with {rules.win_length} or more."
        )
    if rules.opening is Opening.CENTER_FIRST:
        lines.append("Black's first stone must be played on the centre point.")
    elif rules.opening is Opening.PRO:
        lines.append(
            "Black's first stone must be the centre point, and black's second stone "
            "must be at least three intersections away from the centre."
        )
    lines.append("Stones are never moved or captured. A full board with no line is a draw.")
    return " ".join(lines)


def check_opening(rules: RuleSet, ply: int, move: Move) -> str | None:
    """Return an illegal-move reason code if ``move`` breaks the opening rule."""
    if rules.opening is Opening.FREE:
        return None
    center = rules.center
    if ply == 0 and move != center:
        return OPENING_CENTER_REQUIRED
    if rules.opening is Opening.PRO and ply == 2:
        if max(abs(move.row - center.row), abs(move.col - center.col)) < 3:
            return OPENING_PRO_DISTANCE
    return None


def judge_placement(board: Board, move: Move, stone: Stone, rules: RuleSet) -> Outcome:
    """Verdict for the position *after* ``stone`` has been placed on ``move``."""
    lengths = [board.line_info(move, stone, d)[0] for d in DIRECTIONS]
    longest = max(lengths)

    if longest > rules.win_length:
        if rules.overline is OverlineRule.FORBIDDEN:
            if stone in rules.overline_forbidden_for:
                # a forbidden move loses on the spot, even if it also makes a five
                winner = stone.opponent
                return Outcome(
                    win_status(winner), winner, f"forbidden_overline_by_{stone.label}"
                )
            # the unrestricted side (white, in renju) simply wins with six or more
            return Outcome(win_status(stone), stone, f"{longest}_in_a_row")
        if rules.overline is OverlineRule.WIN:
            return Outcome(win_status(stone), stone, f"{longest}_in_a_row")
        # OverlineRule.IGNORE: the overline is inert, an exact five may still win

    if rules.win_length in lengths:
        return Outcome(win_status(stone), stone, f"{rules.win_length}_in_a_row")

    if board.is_full:
        return Outcome(Status.DRAW, None, "board_full")
    return IN_PROGRESS
