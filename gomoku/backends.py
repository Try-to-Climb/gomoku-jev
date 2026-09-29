"""Backend registry: the one place that knows which players exist.

Every entry point -- the CLI, the probe, the ablation runner, the audit writer,
the post-mortem -- resolves players and backends through this module, so adding a
model means writing one file and registering it once, instead of editing seven
dispatch sites.

Two kinds of entry live here:

* **players** answer "which point do you play" and plug into the referee
  (:func:`build_player`). Local bots and humans are players too.
* **batteries** answer a whole fan-out question set about one position
  (:func:`battery_for`), which is what the diagnostic tools need. Only model
  backends have one.

A spec string is ``kind[:model][@prompt-version][|mode]``::

    heuristic
    jev                          the default jev model
    jev:jev-1.13.0@p0-one-move   a pinned model, a specific tactical wording
    openai:Qwen/Qwen3-32B|free   a chat model answering with a bare coordinate

Third-party backends do not need this file edited: call :func:`register` before
running a match and the new name works everywhere, including ``--backend``
choices and error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .players import Player

#: a player factory: (arg, seed, overrides) -> Player, where ``arg`` is
#: everything after ``kind:`` and ``overrides`` carries the shared model options
#: (candidate menu, prompt version, fact injection) that local players ignore
PlayerFactory = Callable[[str, "int | None", dict], Player]

#: a battery factory: (model, **kwargs) -> battery client (see gomoku.battery)
BatteryFactory = Callable[..., object]


@dataclass(frozen=True)
class Backend:
    name: str
    summary: str
    player: PlayerFactory
    #: set when the backend can answer a fan-out question set
    battery: BatteryFactory | None = None
    #: model backends accept a model id, a prompt version and fact injection;
    #: local players accept none of that and cost nothing to run
    is_model: bool = False
    #: extra names accepted for the same backend
    aliases: tuple[str, ...] = ()


_REGISTRY: dict[str, Backend] = {}
_ALIASES: dict[str, str] = {}


def register(backend: Backend, *, replace: bool = False) -> Backend:
    """Add a backend. Raises unless ``replace`` is set, so typos cannot shadow."""
    known = _REGISTRY.get(backend.name)
    if known is not None and not replace:
        raise ValueError(f"backend {backend.name!r} is already registered")
    _REGISTRY[backend.name] = backend
    for alias in backend.aliases:
        _ALIASES[alias] = backend.name
    return backend


def get(name: str) -> Backend:
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown backend {name!r}; known: {', '.join(names())}") from None


def names(*, models_only: bool = False, with_battery: bool = False) -> list[str]:
    """Registered names, for argparse ``choices`` and help text."""
    out = [
        b.name
        for b in _REGISTRY.values()
        if (not models_only or b.is_model) and (not with_battery or b.battery is not None)
    ]
    return sorted(out)


def spec_help() -> str:
    """One line listing every accepted player spec, used in error messages."""
    parts = []
    for name in names():
        backend = _REGISTRY[name]
        parts.append(f"{name}[:<model>][@<prompt version>]" if backend.is_model else name)
    return " | ".join(parts)


def describe() -> list[tuple[str, str]]:
    """``[(name, summary)]`` for a ``--list-backends`` style listing."""
    return [(n, _REGISTRY[n].summary) for n in names()]


# ------------------------------------------------------------------ spec parsing
def split_version(arg: str) -> tuple[str, dict]:
    """Peel a trailing ``@<prompt version>`` off a model spec.

    ``"jev-1.13.0@p0-one-move"`` -> ``("jev-1.13.0", {"prompt_version": "p0-one-move"})``.
    The suffix beats a global ``--prompt-version``, which is what lets the two
    seats of one match run different tactical wordings.
    """
    if "@" not in arg:
        return arg, {}
    arg, version = arg.rsplit("@", 1)
    return arg, {"prompt_version": version.strip()}


def split_spec(spec: str) -> tuple[str, str]:
    """``"jev:model@v"`` -> ``("jev", "model@v")``.

    ``"jev@v"`` has no colon, so the version suffix rides on the kind; move it
    back onto the argument where the model factory expects it.
    """
    kind, _, arg = spec.partition(":")
    if "@" in kind:
        kind, _, version = kind.partition("@")
        arg = f"{arg}@{version}" if arg else f"@{version}"
    return kind.strip().lower(), arg.strip()


def build_player(
    spec: str, seed: int | None = None, overrides: dict | None = None
) -> Player:
    """Turn a spec string into a player, or exit with the list of valid specs."""
    kind, arg = split_spec(spec)
    try:
        backend = get(kind)
    except KeyError:
        raise SystemExit(f"unknown player spec {spec!r} ({spec_help()})") from None
    return backend.player(arg, seed, dict(overrides or {}))


def battery_for(name: str, model: str | None = None, **kwargs):
    """The fan-out question client for a backend (see :mod:`gomoku.battery`)."""
    backend = get(name)
    if backend.battery is None:
        raise SystemExit(
            f"backend {backend.name!r} cannot answer a question battery; "
            f"try one of: {', '.join(names(with_battery=True))}"
        )
    return backend.battery(model, **kwargs)


# --------------------------------------------------------------- the built-ins
# Model backends import their SDK lazily: constructing the registry must not drag
# in an HTTP client, and `python -m gomoku.cli --black heuristic` must work with
# nothing installed.
def _random(arg: str, seed: int | None, overrides: dict) -> Player:
    from .players import RandomPlayer

    return RandomPlayer(name=arg or "random", seed=seed)


def _heuristic(arg: str, seed: int | None, overrides: dict) -> Player:
    from .players import HeuristicPlayer

    return HeuristicPlayer(name=arg or "heuristic", seed=seed)


def _human(arg: str, seed: int | None, overrides: dict) -> Player:
    from .players import ConsolePlayer

    return ConsolePlayer(name=arg or "human")


def _jev(arg: str, seed: int | None, overrides: dict) -> Player:
    from .llm_player import build_llm_player

    return build_llm_player(arg, seed=seed, **overrides)


def _jev_battery(model: str | None = None, **kwargs):
    from .battery import JevBattery

    return JevBattery(model, **kwargs)


def _openai(arg: str, seed: int | None, overrides: dict) -> Player:
    from .openai_player import build_openai_player

    return build_openai_player(arg, seed=seed, **overrides)


def _openai_battery(model: str | None = None, **kwargs):
    from .battery import OpenAIBattery

    return OpenAIBattery(model, **kwargs)


register(Backend("random", "uniformly random legal move -- the floor to beat", _random))
register(
    Backend(
        "heuristic",
        "greedy one-ply bot: take a win, block a win, else best pattern value",
        _heuristic,
        aliases=("bot",),
    )
)
register(Backend("human", "you, at the terminal", _human))
register(
    Backend(
        "jev",
        "jev / TypeSafe System One: one `choice` question per move",
        _jev,
        battery=_jev_battery,
        is_model=True,
        aliases=("llm",),
    )
)
register(
    Backend(
        "openai",
        "any OpenAI-compatible chat endpoint (OPENAI_API_KEY / OPENAI_BASE_URL)",
        _openai,
        battery=_openai_battery,
        is_model=True,
    )
)

__all__ = [
    "Backend",
    "battery_for",
    "build_player",
    "describe",
    "get",
    "names",
    "register",
    "spec_help",
    "split_spec",
    "split_version",
]
