"""A chat model as a gomoku player, over any OpenAI-compatible endpoint.

This is the pluggable seat: anything that speaks the OpenAI chat-completions
protocol -- the OpenAI API itself, OpenRouter, vLLM, llama.cpp, Ollama, or a
private gateway -- can sit opposite jev with no code change, just a different
``OPENAI_BASE_URL`` and model id.

Two modes, both driven by :mod:`gomoku.candidates` so the comparison with jev is
apples to apples:

* ``choice`` (default) -- the model gets the same option menu jev gets and must
  answer with jev's answer shape (``choice`` + optional ``probabilities`` and
  ``confidence``). Same information, same constraint, comparable numbers.
* ``free`` -- no menu: plain board, "answer with one coordinate". Measures the
  model the way a user would actually use it, and is the only mode where an
  illegal move is possible.

Credentials come from the environment, following the OpenAI SDK's own
conventions: ``OPENAI_API_KEY`` and, optionally, ``OPENAI_BASE_URL``. A
self-hosted endpoint that needs no key works too -- set only the base URL.
:func:`ensure_auth` is called before the first request so a missing key fails
fast with an actionable message instead of mid-game.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

from .board import Move, Stone
from . import factsheet
from .candidates import CandidateConfig, candidate_points, option_descriptions, read_choice
from .game import GameView
from .notation import json_objects, parse_move, to_notation
from .players import MoveResponse, Player
from .prompts import (
    PROMPT_VERSION,
    PromptStyle,
    load_template,
    render,
    rules_and_state,
    system_prompt,
    user_prompt,
)

#: the model used for every recorded experiment in docs/ -- an OpenRouter-style id
DEFAULT_MODEL = "openai/gpt-oss-120b"

#: ids that have been exercised against this player. Which ids are valid depends
#: entirely on the endpoint: OpenAI uses "gpt-4o-mini", OpenRouter prefixes with a
#: vendor ("openai/gpt-oss-120b"), Ollama uses plain names ("qwen3:32b").
MODELS = [
    "openai/gpt-oss-120b",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen2.5-32B-Instruct",
    "Microsoft/phi-4",
]

MODE_CHOICE = "choice"
MODE_FREE = "free"

#: templates/openai/ holds this backend's part-4 glue
BACKEND = "openai"
SYSTEM_TEMPLATE_FILE = "system.txt"
USER_TEMPLATE_FILE = "user.txt"
#: the two answer-format variants: full asks for a distribution, terse asks only for
#: the pick and forbids step-by-step thinking (used after a truncated reply)
ANSWER_FULL_FILE = "answer_full.txt"
ANSWER_TERSE_FILE = "answer_terse.txt"

API_KEY_VAR = "OPENAI_API_KEY"
BASE_URL_VAR = "OPENAI_BASE_URL"
#: sent when only a base URL is configured; local servers ignore the value but the
#: SDK refuses to construct a client without one
PLACEHOLDER_KEY = "not-needed"


class ChatError(RuntimeError):
    """Any failure that stops us getting a reply out of the endpoint."""


class ChatAuthError(ChatError):
    """Credentials are missing or unusable."""


def ensure_auth() -> str:
    """Confirm the endpoint is configured. Returns ``api_key`` or ``base_url``.

    Called before the first request rather than inside the game loop, so a
    misconfigured environment is reported before any tokens are spent.
    """
    if os.environ.get(API_KEY_VAR):
        return "api_key"
    if os.environ.get(BASE_URL_VAR):
        # a self-hosted endpoint (Ollama, vLLM, llama.cpp) usually needs no key
        return "base_url"
    raise ChatAuthError(
        f"no endpoint configured: set {API_KEY_VAR} for a hosted API, or "
        f"{BASE_URL_VAR} alone for a local server that needs no key "
        f"(e.g. {BASE_URL_VAR}=http://localhost:11434/v1)"
    )


@dataclass
class OpenAIConfig(CandidateConfig):
    model: str = DEFAULT_MODEL
    #: "choice" (same menu as jev) or "free" (plain coordinate answer)
    mode: str = MODE_CHOICE
    #: ask for a full probability distribution, as jev returns
    require_distribution: bool = True
    temperature: float = 0.0
    #: reasoning models spend most of this on hidden thinking tokens
    max_tokens: int = 16384
    #: on a truncated reply, retry once without the distribution and with a
    #: "do not think step by step" instruction
    retry_on_truncation: bool = True
    timeout: float = 600.0
    #: append "/no_think" to the user turn (Qwen-style thinking switch)
    no_think: bool = False
    #: which tactical wording to send, i.e. a file in templates/tactics/
    prompt_version: str = PROMPT_VERSION
    #: engine-computed facts injected into the state: none|span|status|geometry|threats
    facts: str = factsheet.NONE
    #: engine-computed facts attached to each candidate option: none|open_four|windows|ways
    option_facts: str = "none"
    #: keep the exact messages in the move record (for audit logs)
    log_prompts: bool = False
    style: PromptStyle = field(default_factory=PromptStyle)

    def to_dict(self) -> dict:
        base = {
            "model": self.model,
            "mode": self.mode,
            "prompt_version": self.prompt_version,
            "facts": self.facts,
            "option_facts": self.option_facts,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.mode == MODE_CHOICE:
            base["require_distribution"] = self.require_distribution
            base.update(self.candidate_dict())
        if self.no_think:
            base["no_think"] = True
        return base


class ChatClient:
    """Minimal wrapper over the OpenAI SDK: one call, usage, system-role fallback."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout: float = 300.0,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.base_url = base_url or os.environ.get(BASE_URL_VAR) or None
        self.api_key = api_key or os.environ.get(API_KEY_VAR) or PLACEHOLDER_KEY
        self.merge_system = False
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - SDK missing
                raise ChatError(
                    f"the openai package is not installed: {exc}. "
                    "Install it with `pip install gomoku-jev[openai]`."
                ) from exc
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, system: str, user: str, *, temperature: float, max_tokens: int) -> dict:
        """Return ``{"text", "usage", "model", "finish_reason", "merged_system"}``."""
        for attempt in (1, 2):
            messages = (
                [{"role": "user", "content": f"{system}\n\n{user}"}]
                if self.merge_system
                else [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - the SDK raises a wide range
                detail = f"{type(exc).__name__}: {exc}"
                # some deployments reject the system role; fold it into the user turn
                if "system role not supported" in detail.lower() and not self.merge_system and attempt == 1:
                    self.merge_system = True
                    continue
                raise ChatError(detail) from exc
            choice = response.choices[0]
            usage = None
            if response.usage:
                usage = {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                }
            return {
                "text": choice.message.content or "",
                "usage": usage,
                "model": getattr(response, "model", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "merged_system": self.merge_system,
            }
        raise ChatError("unreachable")  # pragma: no cover

    def close(self) -> None:
        self._client = None


def choice_system_prompt(view: GameView, config: OpenAIConfig, *, terse: bool = False) -> str:
    """The instruction half. ``terse`` drops the distribution and bans thinking."""
    from .prompts import rules_block, tactics_block

    want_distribution = config.require_distribution and not terse
    answer_file = ANSWER_FULL_FILE if want_distribution else ANSWER_TERSE_FILE
    return render(
        SYSTEM_TEMPLATE_FILE,
        BACKEND,
        colour=view.stone.label,
        symbol=view.stone.symbol,
        rules_block=rules_block(view.rules, BACKEND),
        tactics_block=tactics_block(view.rules, config.prompt_version, BACKEND),
        answer=load_template(answer_file, BACKEND),
    )


def choice_user_prompt(
    view: GameView, config: OpenAIConfig, candidates: list[Move], feedback: str | None
) -> str:
    text = render(
        USER_TEMPLATE_FILE,
        BACKEND,
        state=rules_and_state(
            view, config.style, feedback, BACKEND, factsheet.render(view, config.facts)
        ),
        options=", ".join(option_descriptions(view, candidates)),
    )
    return text + "\n/no_think" if config.no_think else text


def read_free_text(text: str, size: int) -> tuple[Move | None, str | None, dict]:
    """Free mode: any coordinate the model manages to emit."""
    result = parse_move(text, size)
    meta = {"parsed_from": result.source} if result.source else {}
    if result.matched:
        meta["matched"] = result.matched
    return result.move, result.error, meta


def read_choice_text(text: str, size: int, allowed: set[str]) -> tuple[Move | None, str | None, dict]:
    """Choice mode: prefer the JSON answer, fall back to a bare coordinate."""
    for obj in json_objects(text):
        if isinstance(obj, dict) and ("choice" in obj or "probabilities" in obj):
            move, error, meta = read_choice(obj, size, allowed)
            meta["parsed_from"] = "json"
            return move, error, meta
    move, error, meta = read_free_text(text, size)
    if move is not None and to_notation(move, size) not in allowed:
        return None, f"{to_notation(move, size)} was not one of the offered points", meta
    if error:
        error = f"no usable JSON answer and no coordinate in the reply ({error})"
    return move, error, meta


class OpenAIPlayer(Player):
    """Plays gomoku through a chat model on an OpenAI-compatible endpoint."""

    def __init__(
        self,
        name: str | None = None,
        config: OpenAIConfig | None = None,
        client: ChatClient | None = None,
    ) -> None:
        self.config = config or OpenAIConfig()
        super().__init__(name or self.config.model)
        self.rng = random.Random(self.config.seed)
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> ChatClient:
        if self._client is None:
            ensure_auth()
            self._client = ChatClient(model=self.config.model, timeout=self.config.timeout)
        return self._client

    def start_game(self, stone: Stone, view: GameView) -> None:
        super().start_game(stone, view)
        self.rng = random.Random(self.config.seed)

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        start = time.perf_counter()
        meta: dict = {"model": self.config.model, "mode": self.config.mode}

        if self.config.mode == MODE_CHOICE:
            candidates = candidate_points(view, self.config, self.rng)
            if not candidates:
                return MoveResponse(error="no legal moves to offer")
            if len(candidates) == 1:
                return MoveResponse(
                    move=candidates[0],
                    latency_s=time.perf_counter() - start,
                    meta={**meta, "forced": True, "candidates": 1},
                )
            allowed = set(option_descriptions(view, candidates))
            user = choice_user_prompt(view, self.config, candidates, feedback)
            meta["candidates"] = len(candidates)
            meta["menu"] = [to_notation(m, view.size) for m in candidates]
        else:
            allowed = None
            user = user_prompt(view, self.config.style, feedback)
            if self.config.no_think:
                user += "\n/no_think"

        move = error = None
        # attempt 1 as configured; attempt 2 only if the reply was cut off mid-thought
        for terse in (False, True):
            if self.config.mode == MODE_CHOICE:
                system = choice_system_prompt(view, self.config, terse=terse)
            else:
                system = system_prompt(
                    view.rules,
                    view.stone.label,
                    view.stone.symbol,
                    self.config.style,
                    self.config.prompt_version,
                )
                if terse:
                    system += "\n\n" + load_template(ANSWER_TERSE_FILE, BACKEND)
            if terse:
                meta["truncation_retry"] = True
            if self.config.log_prompts:
                meta["prompt"] = {"system": system, "user": user}

            try:
                result = self.client.complete(
                    system,
                    user,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
            except ChatError as exc:
                return MoveResponse(
                    error=f"chat request failed: {exc}",
                    latency_s=time.perf_counter() - start,
                    meta=meta,
                )

            text = result["text"]
            meta.update(
                {
                    "api_model": result["model"],
                    "usage": result["usage"],
                    "finish_reason": result["finish_reason"],
                    "merged_system": result["merged_system"],
                    "raw_len": len(text),
                }
            )
            if allowed is None:
                move, error, read_meta = read_free_text(text, view.size)
            else:
                move, error, read_meta = read_choice_text(text, view.size, allowed)
            meta.update(read_meta)

            truncated = result["finish_reason"] == "length"
            if truncated:
                meta["truncated"] = True
            if move is not None or not truncated or terse or not self.config.retry_on_truncation:
                break

        return MoveResponse(
            move=move,
            raw=text,
            error=error,
            latency_s=time.perf_counter() - start,
            meta=meta,
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def describe(self) -> dict:
        return {**super().describe(), **self.config.to_dict()}


def build_openai_player(spec: str = "", **overrides) -> Player:
    """Factory for the CLI: ``openai[:<model>][@<prompt version>][|free]``.

    The ``@version`` suffix beats a global ``--prompt-version``, so the two seats of
    a match can run different tactical wordings.
    """
    spec = spec.strip()
    mode = MODE_CHOICE
    if spec.endswith("|free"):
        spec, mode = spec[: -len("|free")], MODE_FREE
    if "@" in spec:
        spec, version = spec.rsplit("@", 1)
        overrides["prompt_version"] = version.strip()
    model = spec or DEFAULT_MODEL
    if model in ("openai", "default"):
        model = DEFAULT_MODEL
    return OpenAIPlayer(config=OpenAIConfig(model=model, mode=mode, **overrides))
