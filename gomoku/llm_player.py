"""jev as a gomoku player.

jev answers *questions about a state*, it does not chat. So one move is one
``choice`` question: the state is the position, the options come from
:mod:`gomoku.candidates`, and the answer carries a probability distribution plus
a confidence -- exactly the signal worth benchmarking against the objective
facts computed in :mod:`gomoku.analysis`.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field

from .board import Move, Stone
from . import factsheet
from .candidates import CandidateConfig, candidate_points, option_descriptions, read_choice
from .game import GameView
from .jev_client import DEFAULT_MODEL, JevClient, JevError, JevResponse
from .players import MoveResponse, Player
from .prompts import PROMPT_VERSION, PromptStyle, render, rules_and_state, tactics_block

QUESTION_ID = "move"

#: templates/jev/ holds this backend's part-4 glue
BACKEND = "jev"


@dataclass
class JevConfig(CandidateConfig):
    model: str = DEFAULT_MODEL
    timeout: float = 120.0
    max_retries: int = 2
    #: which tactical wording to send, i.e. a file in templates/tactics/
    prompt_version: str = PROMPT_VERSION
    #: engine-computed facts injected into the state: none|span|status|geometry|threats
    #: (see gomoku.factsheet -- this changes what is being measured, so it is logged)
    facts: str = factsheet.NONE
    #: engine-computed facts attached to each candidate option: none|open_four
    option_facts: str = "none"
    #: keep the exact request text in the move record (for audit logs)
    log_prompts: bool = False
    style: PromptStyle = field(default_factory=PromptStyle)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "facts": self.facts,
            "option_facts": self.option_facts,
            **self.candidate_dict(),
        }


def move_question(
    view: GameView,
    candidates: list[Move],
    version: str = PROMPT_VERSION,
    option_facts: str = "none",
) -> dict:
    """A jev ``choice`` question whose options are the candidate points."""
    return {
        "type": "choice",
        "instructions": render(
            "instructions.txt",
            BACKEND,
            colour=view.stone.label,
            symbol=view.stone.symbol,
            tactics=tactics_block(view.rules, version, BACKEND),
        ),
        "criteria": option_descriptions(view, candidates, option_facts),
    }


class JevPlayer(Player):
    """Plays gomoku by asking jev one ``choice`` question per move."""

    def __init__(
        self,
        name: str | None = None,
        config: JevConfig | None = None,
        client: JevClient | None = None,
    ) -> None:
        self.config = config or JevConfig()
        super().__init__(name or f"jev:{self.config.model}")
        self.rng = random.Random(self.config.seed)
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> JevClient:
        if self._client is None:
            self._client = JevClient(
                model=self.config.model,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
            )
        return self._client

    def start_game(self, stone: Stone, view: GameView) -> None:
        super().start_game(stone, view)
        self.rng = random.Random(self.config.seed)

    def propose(self, view: GameView, feedback: str | None = None) -> MoveResponse:
        start = time.perf_counter()
        candidates = candidate_points(view, self.config, self.rng)
        if not candidates:
            return MoveResponse(error="no legal moves to offer")
        if len(candidates) == 1:
            # jev needs at least two options; a forced move needs no model call
            return MoveResponse(
                move=candidates[0],
                latency_s=time.perf_counter() - start,
                meta={"forced": True, "candidates": 1},
            )

        question = move_question(
            view, candidates, self.config.prompt_version, self.config.option_facts
        )
        state = rules_and_state(
            view,
            self.config.style,
            feedback,
            BACKEND,
            factsheet.render(view, self.config.facts),
        )
        meta: dict = {
            "candidates": len(candidates),
            "model": self.config.model,
            "menu": list(question["criteria"]),
        }
        if self.config.log_prompts:
            meta["prompt"] = {"state": state, "question": question}

        try:
            response: JevResponse = self.client.ask(state, {QUESTION_ID: question})
        except JevError as exc:
            meta["http_status"] = exc.status
            if exc.body:
                meta["body"] = exc.body[:200]
            return MoveResponse(
                error=f"jev request failed: {exc}",
                latency_s=time.perf_counter() - start,
                meta=meta,
            )

        meta.update(
            {
                "jev_model": response.model,
                "usage": response.usage,
                "api_latency_s": round(response.latency_s, 3),
                "api_attempts": response.attempts,
            }
        )
        answer = response.answers.get(QUESTION_ID)
        move, error, answer_meta = read_choice(answer or {}, view.size, set(question["criteria"]))
        meta.update(answer_meta)
        return MoveResponse(
            move=move,
            raw=json.dumps(answer, ensure_ascii=False) if answer is not None else None,
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


def build_llm_player(spec: str = "", **overrides) -> Player:
    """Factory used by the registry: ``jev``, ``jev:<model>``, or ``<model>@<version>``.

    The ``@version`` suffix beats a global ``--prompt-version``, so the two seats of
    a match can run different tactical wordings.
    """
    from .backends import split_version

    spec, from_spec = split_version(spec.strip())
    overrides.update(from_spec)
    model = spec.strip() or DEFAULT_MODEL
    if model in ("jev", "default"):
        model = DEFAULT_MODEL
    return JevPlayer(config=JevConfig(model=model, **overrides))
