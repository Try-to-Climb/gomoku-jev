"""Position analysis used both by the baseline bot and by the scorer.

The scorer's job is to turn "the model lost" into something diagnostic: did it
walk past a win it could have taken, did it fail to block a five that was one
move away, did it play a legal but pointless stone?
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .board import DIRECTIONS, Board, Move, Stone
from .game import Game, GameView
from .notation import to_notation
from .rules import RuleSet, judge_placement


def _as_game(position: Game | GameView) -> Game:
    return position.to_game() if isinstance(position, GameView) else position


def wins_immediately(board: Board, move: Move, stone: Stone, rules: RuleSet) -> bool:
    """Would placing ``stone`` on the empty point ``move`` end the game as a win?"""
    board.place(move, stone)
    try:
        return judge_placement(board, move, stone, rules).winner is stone
    finally:
        board.remove(move)


def winning_moves(position: Game | GameView, stone: Stone | None = None) -> list[Move]:
    """Legal moves that would immediately win for ``stone`` (default: side to move)."""
    game = _as_game(position)
    if game.is_over:
        return []
    stone = stone or game.current_stone
    board = game.board
    rules = game.rules
    out = []
    for move in game.board.empties():
        # opening constraints only matter for the side actually to move
        if stone is game.current_stone and game.check(move) is not None:
            continue
        if wins_immediately(board, move, stone, rules):
            out.append(move)
    return out


@dataclass
class MoveAnalysis:
    """Facts about the position *before* a move, plus how the move measured up.

    Two threat levels are tracked. The five level (``*_win``, ``*_block``) is
    decided by a single point. The open-four level (``*_open_four``) is one move
    earlier: a four with two completing points cannot be blocked, so creating one
    wins and allowing one loses -- this is the layer where every recorded jev game
    was actually decided.
    """

    stone: Stone
    own_wins: list[Move] = field(default_factory=list)
    opponent_wins: list[Move] = field(default_factory=list)
    own_open_fours: list[Move] = field(default_factory=list)
    opponent_open_fours: list[Move] = field(default_factory=list)
    took_win: bool = False
    missed_win: bool = False
    blocked_threat: bool = False
    missed_block: bool = False
    took_open_four: bool = False
    missed_open_four: bool = False
    prevented_open_four: bool = False
    allowed_open_four: bool = False
    #: opponent has two or more winning points -- no single move saves us
    unavoidable_loss: bool = False

    def to_dict(self, size: int) -> dict:
        return {
            "own_wins": [to_notation(m, size) for m in self.own_wins],
            "opponent_wins": [to_notation(m, size) for m in self.opponent_wins],
            "own_open_fours": [to_notation(m, size) for m in self.own_open_fours],
            "opponent_open_fours": [to_notation(m, size) for m in self.opponent_open_fours],
            "took_win": self.took_win,
            "missed_win": self.missed_win,
            "blocked_threat": self.blocked_threat,
            "missed_block": self.missed_block,
            "took_open_four": self.took_open_four,
            "missed_open_four": self.missed_open_four,
            "prevented_open_four": self.prevented_open_four,
            "allowed_open_four": self.allowed_open_four,
            "unavoidable_loss": self.unavoidable_loss,
        }


def analyse_position(position: Game | GameView) -> MoveAnalysis:
    """Tactical facts for the side to move, computed before it answers."""
    game = _as_game(position)
    stone = game.current_stone
    own = winning_moves(game, stone)
    opp = winning_moves(game, stone.opponent)
    # the open-four layer only matters while nobody can already finish
    own_of = [] if own else open_four_moves(game, stone)
    opp_of = [] if own or opp else open_four_moves(game, stone.opponent)
    return MoveAnalysis(
        stone=stone,
        own_wins=own,
        opponent_wins=opp,
        own_open_fours=own_of,
        opponent_open_fours=opp_of,
    )


def score_move(
    analysis: MoveAnalysis, move: Move, after: Game | None = None
) -> MoveAnalysis:
    """Fill in the verdict flags for the move that was actually played.

    Levels are exclusive and ordered: finishing beats blocking a five, which
    beats making an open four, which beats preventing one. With two or more
    opposing winning points the position is already lost, and not blocking is not
    counted as a mistake.

    ``after`` is the game *after* the move; when given, open-four prevention is
    judged by recomputing the opponent's options rather than by assuming the only
    defence is to occupy one of their points.
    """
    analysis.took_win = bool(analysis.own_wins) and move in analysis.own_wins
    analysis.missed_win = bool(analysis.own_wins) and not analysis.took_win
    if analysis.own_wins:
        return analysis

    if analysis.opponent_wins:
        if len(analysis.opponent_wins) > 1:
            analysis.unavoidable_loss = True
        else:
            analysis.blocked_threat = move == analysis.opponent_wins[0]
            analysis.missed_block = not analysis.blocked_threat
        return analysis

    if analysis.own_open_fours:
        analysis.took_open_four = move in analysis.own_open_fours
        analysis.missed_open_four = not analysis.took_open_four
        return analysis

    if analysis.opponent_open_fours:
        if after is not None:
            still = open_four_moves(after, analysis.stone.opponent)
            analysis.prevented_open_four = not still
        else:
            analysis.prevented_open_four = move in analysis.opponent_open_fours
        analysis.allowed_open_four = not analysis.prevented_open_four
    return analysis


def open_four_moves(position: Game | GameView, stone: Stone | None = None) -> list[Move]:
    """Moves after which ``stone`` holds two or more immediate winning points.

    One level below :func:`winning_moves`: an "open four" cannot be blocked,
    because the opponent can only cover one of the two completing points, so
    playing one of these wins the game two plies later.

    Returns ``[]`` when ``stone`` can already finish this turn -- the level below
    is moot then, and that also licenses two contiguity shortcuts: a five is an
    unbroken run, so a move that creates one must touch a stone of its own colour,
    and every new completing point must lie on a line through that move.
    """
    game = _as_game(position)
    if game.is_over:
        return []
    stone = stone or game.current_stone
    if winning_moves(game, stone):
        return []
    board = game.board
    rules = game.rules
    check_legality = stone is game.current_stone
    out = []
    for move in _empties_touching(board, stone):
        if check_legality and game.check(move) is not None:
            continue
        board.place(move, stone)
        try:
            if _completion_count(board, move, stone, rules, limit=2) >= 2:
                out.append(move)
        finally:
            board.remove(move)
    return out


def _empties_touching(board: Board, stone: Stone):
    """Empty points adjacent to a stone of that colour, each yielded once."""
    seen: set[Move] = set()
    for origin, cell in board.occupied():
        if cell is not stone:
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                row, col = origin.row + dr, origin.col + dc
                if board.in_bounds(row, col) and board.get(row, col) is Stone.EMPTY:
                    point = Move(row, col)
                    if point not in seen:
                        seen.add(point)
                        yield point


def _completion_count(
    board: Board, move: Move, stone: Stone, rules: RuleSet, limit: int = 2
) -> int:
    """How many empty points would now complete a win for ``stone``.

    Only points collinear with ``move`` are examined: any five that did not exist
    before ``move`` was placed must run through it.
    """
    found = 0
    checked: set[Move] = set()
    reach = rules.win_length - 1
    for dr, dc in DIRECTIONS:
        for step in range(-reach, reach + 1):
            if step == 0:
                continue
            row, col = move.row + dr * step, move.col + dc * step
            if not board.in_bounds(row, col) or board.get(row, col) is not Stone.EMPTY:
                continue
            point = Move(row, col)
            if point in checked:
                continue
            checked.add(point)
            if wins_immediately(board, point, stone, rules):
                found += 1
                if found >= limit:
                    return found
    return found


@dataclass(frozen=True)
class Run:
    """A maximal unbroken run of one colour, plus whether it can still win."""

    cells: tuple[Move, ...]
    direction: tuple[int, int]
    live: bool

    @property
    def length(self) -> int:
        return len(self.cells)


def maximal_runs(board: Board, stone: Stone, win_length: int, min_length: int = 2) -> list[Run]:
    """Every unbroken run of ``stone`` of at least ``min_length``, deduplicated.

    ``live`` is True when some window of ``win_length`` consecutive points
    containing the run holds no opposing stone and stays on the board -- i.e.
    the run can still grow into a win. A run that is not live is *dead*: adding
    stones to it can never produce a five, and blocking it is a wasted move.
    """
    runs: list[Run] = []
    seen: set[tuple[Move, tuple[int, int]]] = set()
    opponent = stone.opponent
    for move, cell in board.occupied():
        if cell is not stone:
            continue
        for direction in DIRECTIONS:
            dr, dc = direction
            # only start a run at its beginning, so each run is reported once
            prev_r, prev_c = move.row - dr, move.col - dc
            if board.in_bounds(prev_r, prev_c) and board.get(prev_r, prev_c) is stone:
                continue
            cells = [move]
            row, col = move.row + dr, move.col + dc
            while board.in_bounds(row, col) and board.get(row, col) is stone:
                cells.append(Move(row, col))
                row, col = row + dr, col + dc
            if len(cells) < min_length:
                continue
            key = (cells[0], direction)
            if key in seen:
                continue
            seen.add(key)
            runs.append(
                Run(tuple(cells), direction, _run_is_live(board, cells, direction, opponent, win_length))
            )
    runs.sort(key=lambda r: -r.length)
    return runs


def _run_is_live(board, cells, direction, opponent: Stone, win_length: int) -> bool:
    """Does any ``win_length`` window covering ``cells`` avoid the opponent and the edge?"""
    dr, dc = direction
    head = cells[0]
    # offset = how many points of the window sit before the run's first stone
    for offset in range(win_length - len(cells) + 1):
        row, col = head.row - dr * offset, head.col - dc * offset
        ok = True
        for _ in range(win_length):
            if not board.in_bounds(row, col) or board.get(row, col) is opponent:
                ok = False
                break
            row, col = row + dr, col + dc
        if ok:
            return True
    return False


def window_stats(board: Board, stone: Stone, win_length: int) -> tuple[int, int]:
    """``(best, ways)`` for ``stone``: the most of its stones inside any still-usable
    window of ``win_length`` consecutive points, and how many windows tie for that.

    A window counts only if it holds no opposing stone and fits on the board, so it
    is a line that could still become a win. ``ways`` is the graded quantity: it
    drops as the opponent blocks, without ever stating a verdict.
    """
    opponent = stone.opponent
    best = 0
    ways = 0
    size = board.size
    for dr, dc in DIRECTIONS:
        for row in range(size):
            for col in range(size):
                end_r, end_c = row + dr * (win_length - 1), col + dc * (win_length - 1)
                if not board.in_bounds(end_r, end_c):
                    continue
                mine = 0
                blocked = False
                r, c = row, col
                for _ in range(win_length):
                    cell = board.get(r, c)
                    if cell is opponent:
                        blocked = True
                        break
                    if cell is stone:
                        mine += 1
                    r, c = r + dr, c + dc
                if blocked or mine == 0:
                    continue
                if mine > best:
                    best, ways = mine, 1
                elif mine == best:
                    ways += 1
    return best, ways


# --------------------------------------------------------------- heuristic bot
# Pattern values for a single axis, keyed by (run length, number of open ends).
# Deliberately simple: contiguous runs only, no broken-three recognition. Good
# enough to punish a model that does not block, cheap enough to run anywhere.
_SHAPE_SCORES = {
    (4, 2): 100_000,
    (4, 1): 10_000,
    (4, 0): 10,
    (3, 2): 5_000,
    (3, 1): 500,
    (3, 0): 10,
    (2, 2): 300,
    (2, 1): 50,
    (2, 0): 5,
    (1, 2): 20,
    (1, 1): 5,
    (1, 0): 1,
}
WIN_SCORE = 10_000_000


def attack_score(board: Board, move: Move, stone: Stone, win_length: int) -> int:
    """How strong ``move`` is for ``stone``, summed over the four axes."""
    total = 0
    for direction in DIRECTIONS:
        length, open_ends = board.line_info(move, stone, direction)
        if length >= win_length:
            total += WIN_SCORE
        else:
            total += _SHAPE_SCORES.get((min(length, 4), open_ends), 1)
    return total


def has_neighbour(board: Board, move: Move, radius: int = 2) -> bool:
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            row, col = move[0] + dr, move[1] + dc
            if (dr or dc) and board.in_bounds(row, col) and board.get(row, col) is not Stone.EMPTY:
                return True
    return False


def candidate_moves(game: Game, radius: int = 2) -> list[Move]:
    """Legal moves near existing stones (the whole board on an empty one)."""
    legal = game.legal_moves()
    if game.board.empty_count == game.board.size**2:
        return legal
    near = [m for m in legal if has_neighbour(game.board, m, radius)]
    return near or legal
