"""Turn the engine's own analysis into a block of plain facts for the prompt.

The diagnostics in ``EXPERIMENTS.md`` (E7/E8) showed jev reading every board fact
correctly and reasoning correctly from facts *stated in words*, while failing to
derive "can this line still reach five" from the board. This module closes that
gap experimentally: it writes the derivation out as text, in three levels, so we
can see which layer of information is the one that was missing.

* ``span``     -- span and *how many* ends are open, without naming them. This is
  the quantity that decides liveness, with no coordinate handed over.
* ``geometry`` -- adds the coordinates of the open points. Careful: for the
  defensive positions those coordinates *are* the correct answer, so a gain here
  over ``span`` measures "the answer was written down", not understanding.
* ``status``   -- adds the DEAD/LIVE verdict and its reason.
* ``threats``  -- adds who can force a five or an unstoppable open four next.

Hard rule: facts only. No ranking of candidate points, no recommendation, no
"best"/"should". The moment the text tells the model where to play, the
experiment stops measuring the model and starts measuring this file.
"""

from __future__ import annotations

from .analysis import Run, maximal_runs, open_four_moves, winning_moves
from .board import Move, Stone
from .game import GameView
from .notation import to_notation
from .prompts import load_template

SPAN = "span"
GEOMETRY = "geometry"
STATUS = "status"
THREATS = "threats"
NONE = "none"
LEVELS = (NONE, SPAN, GEOMETRY, STATUS, THREATS)

ORIENTATION = {
    (0, 1): "row",
    (1, 0): "column",
    (1, 1): "diagonal",
    (1, -1): "diagonal",
}


def sections(text: str) -> dict[str, str]:
    """Split a ``[name]``-delimited template into named chunks."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = out.setdefault(line[1:-1], [])
        elif current is not None:
            current.append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


def _fill(template: str, **values) -> str:
    from string import Template

    return Template(template).substitute(**values)


def _span_and_ends(view: GameView, run: Run) -> tuple[int, list[str]]:
    """Longest opponent-free stretch through the run, plus its empty end points."""
    board = view.board
    stone = board[run.cells[0]]
    opponent = stone.opponent
    dr, dc = run.direction
    span = run.length
    ends: list[str] = []
    for sign, anchor in ((1, run.cells[-1]), (-1, run.cells[0])):
        row, col = anchor.row + dr * sign, anchor.col + dc * sign
        if board.in_bounds(row, col) and board.is_empty(Move(row, col)):
            ends.append(to_notation(Move(row, col), view.size))
        while board.in_bounds(row, col) and board.get(row, col) is not opponent:
            span += 1
            row, col = row + dr * sign, col + dc * sign
    return span, ends


def render(view: GameView, level: str = NONE) -> str:
    """The fact block for this position, or ``""`` when disabled."""
    if level == NONE:
        return ""
    if level not in LEVELS:
        raise ValueError(f"unknown facts level {level!r}; known: {list(LEVELS)}")

    parts = sections(load_template("facts.txt"))
    size, win_length = view.size, view.rules.win_length
    board = view.board
    rows: list[str] = []

    for stone in (view.stone, view.opponent):
        colour = stone.label
        for run in maximal_runs(board, stone, win_length):
            span, ends = _span_and_ends(view, run)
            # read the run the way a human reads the board: left to right, bottom to top
            ordered = sorted(run.cells, key=lambda c: (c.col, -c.row))
            coords = " ".join(to_notation(c, size) for c in ordered)
            orientation = ORIENTATION[run.direction]
            ends_text = ", ".join(ends) if ends else "none"
            if level == SPAN:
                # the quantity that decides liveness, without naming the points:
                # separates "it can use the number" from "the answer was written down"
                rows.append(
                    _fill(parts["row_span"], colour=colour, coords=coords,
                          orientation=orientation, span=span, n_ends=len(ends))
                )
            elif level == GEOMETRY:
                rows.append(
                    _fill(parts["row_geometry"], colour=colour, coords=coords,
                          orientation=orientation, span=span, ends=ends_text)
                )
            else:
                status = parts["status_live"] if run.live else parts["status_dead"]
                reason_key = "reason_live" if run.live else "reason_dead"
                reason = _fill(
                    parts[reason_key], span=span, n=win_length,
                    ends=ends_text, n_ends=len(ends),
                )
                rows.append(
                    _fill(parts["row_status"], colour=colour, coords=coords,
                          orientation=orientation, status=status, reason=reason)
                )

    lines = "\n".join(rows) if rows else parts["no_lines"]
    block = _fill(parts["block"], lines=lines)

    if level == THREATS:
        threat_rows: list[str] = []
        game = view.to_game()
        for stone in (view.stone, view.opponent):
            fives = winning_moves(game, stone)
            if fives:
                threat_rows.append(
                    _fill(parts["threat_five"], colour=stone.label, n=win_length,
                          points=" or ".join(to_notation(m, size) for m in fives))
                )
            open_fours = open_four_moves(game, stone)
            if open_fours:
                threat_rows.append(
                    _fill(parts["threat_open_four"], colour=stone.label,
                          points=" or ".join(to_notation(m, size) for m in open_fours))
                )
        if not threat_rows:
            threat_rows.append(parts["threat_none"])
        block += "\n\n" + _fill(parts["threats"], threats="\n".join(threat_rows))

    return block
