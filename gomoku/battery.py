"""Ask the same question battery of either backend.

jev answers a battery natively (one request, many questions). A chat model has to
be told the answer shape, so this forces it into jev's response schema -- the only
way the two are comparable: same state, same questions, same answer format, one
request each.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .jev_client import DEFAULT_MODEL as JEV_MODEL
from .jev_client import JevClient, JevError
from .notation import json_objects

SYSTEM = """\
You answer factual questions about a gomoku position. You never generate prose.

You receive a STATE (the rules and a board) and a set of QUESTIONS keyed by id.
Answer every question independently, using the state.

Answer shapes by question type:
  "noul"   -> {"type": "noul", "noul": <probability between 0.0 and 1.0 that the \
answer to the question is yes>}
  "choice" -> {"type": "choice", "choice": <exactly one of the keys of that \
question's "criteria">}

Output raw JSON and nothing else -- no markdown fences, no commentary:
{"answers": {<question id>: <answer>, ...}}

Use exactly the question ids you were given: do not rename, drop or invent any.\
"""

USER = """\
STATE
{state}

QUESTIONS
{questions}\
"""


@dataclass
class BatteryResponse:
    answers: dict
    usage: dict | None = None
    latency_s: float = 0.0
    model: str | None = None
    raw: str | None = None
    meta: dict = field(default_factory=dict)


class JevBattery:
    """jev answers a battery natively."""

    name = "jev"

    def __init__(self, model: str | None = None, **_) -> None:
        self.model = model or JEV_MODEL
        self.client = JevClient(model=self.model)

    def describe(self) -> str:
        return f"jev / {self.model} (key {self.client.key_fingerprint})"

    def ask(self, state: str, questions: dict) -> BatteryResponse:
        response = self.client.ask(state, questions)
        return BatteryResponse(
            answers=response.answers,
            usage=response.usage,
            latency_s=response.latency_s,
            model=response.model,
        )

    def close(self) -> None:
        self.client.close()


class OpenAIBattery:
    """A chat model, forced into jev's answer schema."""

    name = "openai"

    def __init__(self, model: str | None = None, timeout: float = 900.0,
                 max_tokens: int = 16384) -> None:
        from .openai_player import DEFAULT_MODEL, ChatClient, ensure_auth

        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.auth = ensure_auth()
        self.chat = ChatClient(model=self.model, timeout=timeout)

    def describe(self) -> str:
        return f"openai / {self.model} (auth {self.auth})"

    def ask(self, state: str, questions: dict) -> BatteryResponse:
        user = USER.format(state=state, questions=json.dumps(questions, indent=2, ensure_ascii=False))
        start = time.perf_counter()
        result = self.chat.complete(
            SYSTEM, user, temperature=0.0, max_tokens=self.max_tokens
        )
        elapsed = time.perf_counter() - start
        text = result["text"]
        answers: dict = {}
        for obj in json_objects(text):
            if isinstance(obj, dict):
                if isinstance(obj.get("answers"), dict):
                    answers = obj["answers"]
                    break
                # some models drop the wrapper and return the ids directly
                if set(obj) & set(questions):
                    answers = obj
                    break
        return BatteryResponse(
            answers=answers,
            usage=result["usage"],
            latency_s=elapsed,
            model=result["model"],
            raw=None if answers else text[:800],
            meta={"finish_reason": result["finish_reason"], "raw_len": len(text)},
        )

    def close(self) -> None:
        self.chat.close()


#: Backends are resolved through :mod:`gomoku.backends`; this module only holds the
#: two clients. ``backends.battery_for("jev")`` is the entry point.
__all__ = ["BatteryResponse", "OpenAIBattery", "JevBattery", "JevError"]
