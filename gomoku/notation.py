"""Coordinate notation plus a tolerant parser for LLM replies.

Canonical notation is ``<column letter><row number>``, e.g. ``H8`` on a 15x15
board: columns are ``A``..``O`` left to right, rows are numbered from ``1`` at
the bottom up to ``15`` at the top (standard gomoku/renju notation, letter
``I`` is *not* skipped).

``parse_move`` is deliberately forgiving: the point of this project is to score
how well a model plays, not how well it follows a serialisation spec, so we
accept the common shapes models emit and report separately (via
:class:`ParseResult`) what had to be salvaged.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import NamedTuple

from .board import Move

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class NotationError(ValueError):
    pass


class ParseResult(NamedTuple):
    move: Move | None
    error: str | None = None
    matched: str | None = None
    #: which strategy produced the move: "json" | "letter" | "pair"
    source: str | None = None


# --------------------------------------------------------------------- format
def column_letter(col: int, size: int) -> str:
    if size > len(LETTERS):
        raise NotationError(f"letter notation only supports boards up to {len(LETTERS)} wide")
    return LETTERS[col]


def to_notation(move: Move, size: int) -> str:
    """``Move(7, 7)`` on a 15x15 board -> ``"H8"``."""
    row, col = move
    if not (0 <= row < size and 0 <= col < size):
        raise NotationError(f"{move} is outside a {size}x{size} board")
    return f"{column_letter(col, size)}{size - row}"


def from_notation(text: str, size: int) -> Move:
    """``"h8"`` -> ``Move(7, 7)``. Raises :class:`NotationError`."""
    token = _normalize(text).replace(" ", "").replace(",", "")
    m = re.fullmatch(r"([A-Z])(\d{1,2})", token)
    if not m:
        raise NotationError(f"cannot read {text!r} as a coordinate like 'H8'")
    return _build(m.group(1), int(m.group(2)), size)


def _build(letter: str, row_label: int, size: int) -> Move:
    col = LETTERS.index(letter)
    row = size - row_label
    if not (0 <= col < size):
        raise NotationError(f"column {letter} is outside a {size}x{size} board")
    if not (0 <= row < size):
        raise NotationError(f"row {row_label} is outside a {size}x{size} board")
    return Move(row, col)


# ---------------------------------------------------------------------- parse
_THINK = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```[a-zA-Z]*")
_LETTER_COORD = re.compile(r"\b([A-Z])\s*[,\s]?\s*(\d{1,2})\b")
_LABELLED_PAIR = re.compile(
    r"ROW\s*[:=]?\s*(\d{1,2})\D{0,16}?COL(?:UMN)?\s*[:=]?\s*([A-Z]|\d{1,2})\b"
)
_NUMBER_PAIR = re.compile(r"[\(\[\{]?\s*(\d{1,2})\s*[,，]\s*(\d{1,2})\s*[\)\]\}]?")


def _normalize(text: str) -> str:
    # NFKC folds full-width letters/digits (Ｈ８) that CJK models sometimes emit.
    return unicodedata.normalize("NFKC", text).upper()


def _strip_reasoning(text: str) -> str:
    return _FENCE.sub(" ", _THINK.sub(" ", text))


def _json_candidates(text: str):
    """Yield every balanced ``{...}`` block that parses as JSON."""
    start = None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        yield json.loads(text[start : i + 1])
                    except ValueError:
                        pass


def json_objects(text: str):
    """Yield every balanced ``{...}`` block in ``text`` that parses as JSON.

    Reasoning blocks and code fences are stripped first, so this also works on
    chat replies that wrap their answer in markdown.
    """
    yield from _json_candidates(_strip_reasoning(text))


def parse_move(text: str | None, size: int) -> ParseResult:
    """Extract a move from free-form model output.

    Tried in order: a JSON object with a ``move``/``coordinate`` field (or
    ``row``/``col``), then the last ``<letter><number>`` coordinate in the text,
    then the last ``row, col`` number pair. Later mentions win because models
    typically enumerate options first and state their answer last.
    """
    if not text or not text.strip():
        return ParseResult(None, "empty response")

    body = _strip_reasoning(text)

    for obj in _json_candidates(body):
        if not isinstance(obj, dict):
            continue
        keys = {k.lower(): v for k, v in obj.items()}
        for key in ("move", "coordinate", "coord", "position", "answer"):
            raw = keys.get(key)
            if isinstance(raw, str):
                try:
                    return ParseResult(from_notation(raw, size), None, raw, "json")
                except NotationError as exc:
                    return ParseResult(None, str(exc), raw, "json")
            if isinstance(raw, (list, tuple)) and len(raw) == 2:
                return _from_pair(raw[0], raw[1], size, str(raw), "json")
        if "row" in keys and ("col" in keys or "column" in keys):
            return _from_pair(keys["row"], keys.get("col", keys.get("column")), size, str(obj), "json")

    upper = _normalize(body)
    labelled = _LABELLED_PAIR.findall(upper)
    if labelled:
        row_label, col_value = labelled[-1]
        return _from_pair(row_label, col_value, size, f"row {row_label} col {col_value}", "pair")

    letters = _LETTER_COORD.findall(upper)
    if letters:
        letter, digits = letters[-1]
        matched = f"{letter}{digits}"
        try:
            return ParseResult(_build(letter, int(digits), size), None, matched, "letter")
        except NotationError as exc:
            return ParseResult(None, str(exc), matched, "letter")

    pairs = _NUMBER_PAIR.findall(body)
    if pairs:
        row_label, col_no = pairs[-1]
        return _from_pair(row_label, col_no, size, f"({row_label},{col_no})", "pair")

    return ParseResult(None, "no coordinate found in response")


def _from_pair(row_label, col_value, size: int, matched: str, source: str) -> ParseResult:
    """Interpret a ``(row, col)`` pair using *displayed* labels: row 1..size from
    the bottom, column 1..size (or ``A``..) from the left."""
    try:
        row_label = int(str(row_label).strip())
    except (TypeError, ValueError):
        return ParseResult(None, f"bad row value {row_label!r}", matched, source)
    col_text = str(col_value).strip().upper()
    if re.fullmatch(r"[A-Z]", col_text):
        col_no = LETTERS.index(col_text) + 1
    else:
        try:
            col_no = int(col_text)
        except ValueError:
            return ParseResult(None, f"bad column value {col_value!r}", matched, source)
    if not (1 <= col_no <= size):
        return ParseResult(None, f"column {col_no} is outside a {size}x{size} board", matched, source)
    try:
        return ParseResult(_build(LETTERS[col_no - 1], row_label, size), None, matched, source)
    except NotationError as exc:
        return ParseResult(None, str(exc), matched, source)
