"""Text rendering of a position, used for the console and for LLM prompts."""

from __future__ import annotations

from .board import Board, Move, Stone
from .notation import LETTERS, to_notation


def render_board(board: Board, last_move: Move | None = None, coords: bool = True) -> str:
    """ASCII grid, row ``size`` at the top down to row ``1``.

    The most recent stone is drawn lowercase (``x``/``o``) so a model can see
    what just happened without cross-referencing the move list; column
    alignment is preserved.
    """
    size = board.size
    width = len(str(size))
    header = " " * (width + 1) + " ".join(LETTERS[c] for c in range(size))
    lines = [header] if coords else []
    for row in range(size):
        label = str(size - row).rjust(width)
        cells = []
        for col in range(size):
            sym = board.get(row, col).symbol
            if last_move is not None and last_move[0] == row and last_move[1] == col:
                sym = sym.lower()
            cells.append(sym)
        line = f"{label} " + " ".join(cells)
        lines.append(f"{line} {label}" if coords else line)
    if coords:
        lines.append(header)
    return "\n".join(lines)


def render_stone_lists(board: Board) -> str:
    """Positions grouped by colour -- a redundant view that helps LLMs that
    struggle to read the grid."""
    black, white = [], []
    for move, stone in board.occupied():
        (black if stone is Stone.BLACK else white).append(to_notation(move, board.size))
    return (
        f"Black (X) stones: {', '.join(black) if black else 'none'}\n"
        f"White (O) stones: {', '.join(white) if white else 'none'}"
    )


def render_history(history: list[Move] | tuple[Move, ...], size: int) -> str:
    if not history:
        return "No moves played yet."
    parts = []
    for i, move in enumerate(history):
        who = "X" if i % 2 == 0 else "O"
        parts.append(f"{i + 1}.{who}{to_notation(move, size)}")
    return " ".join(parts)
