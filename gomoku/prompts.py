"""Prompt construction. Every word sent to a model lives in ``gomoku/templates/``.

A prompt is four parts:

1. **rules** -- how gomoku works and how coordinates are written. Shared.
2. **tactics** -- the threat vocabulary and priorities. Shared by default, and the
   experimental axis: ``shared/tactics/<version>.txt``, file name = version.
3. **state** -- the battlefield: whose turn, the grid, the stone lists, the move
   list. Same content for everyone; the *format* may be overridden per backend.
4. **backend glue** -- the little that is genuinely API-specific: how the pieces
   are laid out in a request, and how the answer must be formatted. Kept small.

Layout on disk::

    templates/shared/rules.txt          part 1
    templates/shared/tactics/*.txt      part 2
    templates/shared/state.txt          part 3
    templates/<backend>/*.txt           part 4
    templates/<backend>/rules.txt       optional override of a shared part

Lookup is "backend first, then shared", so overriding part 2 or 3 for one model
means dropping a file with the same name into that model's folder -- no code
change. Nothing overrides by default: a test asserts that no backend file shadows
a shared one, so losing apples-to-apples comparability is always a deliberate,
visible act.

Templates use ``string.Template`` syntax (``$name``), not ``str.format``, so the
JSON examples inside the prompts need no brace escaping.
"""

from __future__ import annotations

import os
import pathlib
import string
from dataclasses import dataclass

from .game import GameView
from .notation import LETTERS
from .rules import RuleSet, describe

#: default location of the prompt text; override with GOMOKU_TEMPLATES or set_template_dir()
DEFAULT_TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"
SHARED = "shared"
TACTICS_SUBDIR = "tactics"

#: the tactical wording used unless a player is configured otherwise; the value is
#: a file name under shared/tactics/ and is recorded in every match log
PROMPT_VERSION = "p1-threat-ladder"

_template_dir: pathlib.Path | None = None
_cache: dict[pathlib.Path, str] = {}


# ------------------------------------------------------------------ plumbing
def template_dir() -> pathlib.Path:
    """Where prompt text is read from, most specific source first."""
    if _template_dir is not None:
        return _template_dir
    env = os.environ.get("GOMOKU_TEMPLATES")
    return pathlib.Path(env).expanduser() if env else DEFAULT_TEMPLATE_DIR


def set_template_dir(path: str | pathlib.Path | None) -> None:
    """Point the loader at another directory (``None`` restores the default)."""
    global _template_dir
    _template_dir = pathlib.Path(path).expanduser() if path is not None else None
    _cache.clear()


def resolve(name: str, backend: str | None = None) -> pathlib.Path:
    """``<backend>/<name>`` if that file exists, else ``shared/<name>``."""
    root = template_dir()
    if backend:
        candidate = root / backend / name
        if candidate.is_file():
            return candidate
    return root / SHARED / name


def load_template(name: str, backend: str | None = None) -> str:
    """Read a template, dropping the file's final newline.

    A template that must end in a blank line simply ends with two newlines.
    """
    path = resolve(name, backend)
    cached = _cache.get(path)
    if cached is None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(
                f"prompt template {name!r} not found for backend {backend!r}: "
                f"looked in {template_dir()/(backend or SHARED)} and {template_dir()/SHARED}"
            ) from None
        cached = text[:-1] if text.endswith("\n") else text
        _cache[path] = cached
    return cached


def render(name: str, backend: str | None = None, **values) -> str:
    """Substitute ``$placeholders`` in a template, naming the file on error."""
    template = string.Template(load_template(name, backend))
    try:
        return template.substitute(**values)
    except KeyError as exc:
        raise KeyError(
            f"template {name!r} uses unknown placeholder ${exc.args[0]}; "
            f"available: {', '.join(sorted(values))}"
        ) from None
    except ValueError as exc:  # a stray '$'
        raise ValueError(f"template {name!r} is malformed: {exc}") from None


def tactics_versions(backend: str | None = None) -> list[str]:
    """Available tactical wordings, shared plus any backend-specific ones."""
    found: set[str] = set()
    for folder in ((backend, SHARED) if backend else (SHARED,)):
        path = template_dir() / folder / TACTICS_SUBDIR
        if path.is_dir():
            found.update(p.stem for p in path.glob("*.txt"))
    return sorted(found)


@dataclass(frozen=True)
class PromptStyle:
    """Knobs worth ablating when measuring a model."""

    #: repeat the position as two lists of coordinates
    include_stone_lists: bool = True
    #: state the tactical situation in words (leaks analysis -- off by default)
    include_hints: bool = False
    #: enumerate legal points (huge on an empty 15x15 board; off by default)
    include_legal_moves: bool = False


# --------------------------------------------------- part 1: rules of the game
def rules_block(rules: RuleSet, backend: str | None = None) -> str:
    return render(
        "rules.txt",
        backend,
        rules=describe(rules),
        first_col=LETTERS[0],
        last_col=LETTERS[rules.size - 1],
        size=rules.size,
        example=f"{LETTERS[7]}8" if rules.size >= 8 else f"{LETTERS[0]}1",
        centre=f"{LETTERS[rules.size // 2]}{rules.size - rules.size // 2}",
    )


# ------------------------------------------------------------ part 2: tactics
def tactics_block(
    rules: RuleSet, version: str = PROMPT_VERSION, backend: str | None = None
) -> str:
    """Threat vocabulary and priorities."""
    name = f"{TACTICS_SUBDIR}/{version}.txt"
    if not resolve(name, backend).is_file():
        raise ValueError(
            f"unknown prompt version {version!r}; available: {tactics_versions(backend)}"
        )
    return render(
        name,
        backend,
        n=rules.win_length,
        n1=rules.win_length - 1,
        n2=rules.win_length - 2,
    )


# -------------------------------------------------- part 3: battlefield state
def state_block(
    view: GameView,
    style: PromptStyle,
    feedback: str | None = None,
    backend: str | None = None,
    facts: str = "",
) -> str:
    """Whose turn it is, the grid, the stone lists and the move list.

    ``facts`` is an optional pre-rendered block of engine-computed facts (see
    :mod:`gomoku.factsheet`); the caller renders it so that this module stays
    independent of the analysis code.
    """
    lines = []
    if style.include_legal_moves:
        lines.append(f"Legal points: {', '.join(view.notation(m) for m in view.legal_moves())}")
    if feedback:
        lines.append(f"IMPORTANT: {feedback}")
    return render(
        "state.txt",
        backend,
        move_no=view.ply + 1,
        colour=view.stone.label,
        symbol=view.stone.symbol,
        opponent=view.opponent.label,
        opponent_symbol=view.opponent.symbol,
        board=view.board_text(),
        stones=view.stone_lists() if style.include_stone_lists else "",
        history=view.history_text(),
        facts="\n" + facts + "\n" if facts else "",
        extra="\n" + "\n".join(lines) + "\n" if lines else "",
    )


# -------------------------------------------------------- composed for a call
def rules_and_state(
    view: GameView,
    style: PromptStyle,
    feedback: str | None = None,
    backend: str | None = None,
    facts: str = "",
) -> str:
    """Parts 1 + 3: a self-contained position, used as jev's ``state`` field."""
    return (
        rules_block(view.rules, backend)
        + "\n\n"
        + state_block(view, style, feedback, backend, facts)
    )



def user_prompt(view: GameView, style: PromptStyle, feedback: str | None = None) -> str:
    """Free-mode user turn: just the battlefield state."""
    return state_block(view, style, feedback, "openai")


def system_prompt(
    rules: RuleSet,
    stone_label: str,
    symbol: str,
    style: PromptStyle,
    version: str = PROMPT_VERSION,
) -> str:
    """Free-mode system prompt for a chat model (answer = one bare coordinate)."""
    return render(
        "system_free.txt",
        "openai",
        colour=stone_label,
        symbol=symbol,
        rules_block=rules_block(rules, "openai"),
        tactics_block=tactics_block(rules, version, "openai"),
    )
