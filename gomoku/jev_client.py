"""Thin client for jev (TypeSafe System One), the decision-engine API.

Same contract as ``examples/jev_hello.py``:

    POST https://api.typesafe.ai/v1/systemone
    {"state": <content>, "model": "jev-latest", "questions": {<id>: <question>}}
    -> {"model": <version>, "answers": {<id>: <answer>}, "usage": {...}}

The key comes from the ``TYPESAFE_API_KEY`` environment variable, or from a file
named by ``TYPESAFE_API_KEY_FILE``. There is deliberately no in-repository
fallback path: a checked-out working tree must never be a place where a key can
sit. The key itself is never printed -- see :func:`key_fingerprint`.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import time
from dataclasses import dataclass, field

import requests

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
#: where the key may come from, in order of precedence
API_KEY_VAR = "TYPESAFE_API_KEY"
API_KEY_FILE_VAR = "TYPESAFE_API_KEY_FILE"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class JevError(RuntimeError):
    """Any failure that stops us getting an answer out of jev."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def load_api_key(explicit: str | None = None) -> str:
    """Resolve the API key. Returns it; callers must not log it.

    Order: the ``explicit`` argument, then ``$TYPESAFE_API_KEY``, then the file
    named by ``$TYPESAFE_API_KEY_FILE`` (which may be either a bare key or a
    ``TYPESAFE_API_KEY=...`` assignment line, so an existing shell env file works).
    """
    if explicit:
        return explicit
    key = os.environ.get(API_KEY_VAR)
    if key and key.strip():
        return key.strip()

    path_text = os.environ.get(API_KEY_FILE_VAR)
    if path_text:
        path = pathlib.Path(path_text).expanduser()
        if not path.is_file():
            raise JevError(f"{API_KEY_FILE_VAR} points at {path}, which is not a file")
        content = path.read_text(encoding="utf-8")
        match = re.search(rf'{API_KEY_VAR}\s*=\s*"?([^"\s]+)"?', content)
        if match:
            return match.group(1)
        stripped = content.strip()
        # a file holding nothing but the key is the common case for a secrets mount
        if stripped and "\n" not in stripped:
            return stripped
        raise JevError(f"no {API_KEY_VAR} value found in {path}")

    raise JevError(
        f"no jev API key: set {API_KEY_VAR}, or point {API_KEY_FILE_VAR} at a file "
        "outside the repository that contains it"
    )


def key_fingerprint(key: str) -> str:
    """Safe-to-print identity of a key: its length and a short hash.

    A hash rather than the last four characters, so that pasting console output
    into an issue cannot contribute any part of the secret itself.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"len={len(key)} sha256:{digest}"


@dataclass
class JevResponse:
    http_status: int
    answers: dict
    model: str | None = None
    usage: dict | None = None
    latency_s: float = 0.0
    attempts: int = 1
    raw: dict = field(default_factory=dict)

    def answer(self, question_id: str) -> dict:
        try:
            return self.answers[question_id]
        except KeyError as exc:
            raise JevError(f"jev returned no answer for {question_id!r}") from exc


class JevClient:
    """One HTTP session, reused across moves."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        url: str = API_URL,
        timeout: float = 120.0,
        max_retries: int = 2,
        backoff_s: float = 1.5,
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._key = load_api_key(api_key)
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        )

    @property
    def key_fingerprint(self) -> str:
        return key_fingerprint(self._key)

    def ask(self, state: str, questions: dict, *, model: str | None = None) -> JevResponse:
        """Send one request. Retries transient failures, then raises :class:`JevError`."""
        body = {"state": state, "model": model or self.model, "questions": questions}
        last: JevError | None = None
        for attempt in range(1, self.max_retries + 2):
            start = time.perf_counter()
            try:
                r = self._session.post(self.url, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last = JevError(f"{type(exc).__name__}: {exc}")
            else:
                elapsed = time.perf_counter() - start
                if r.status_code == 200:
                    try:
                        payload = r.json()
                    except ValueError as exc:
                        raise JevError(
                            f"jev returned non-JSON body: {exc}", status=r.status_code, body=r.text[:500]
                        ) from exc
                    answers = payload.get("answers")
                    if not isinstance(answers, dict):
                        raise JevError(
                            "jev response has no 'answers' object",
                            status=r.status_code,
                            body=r.text[:500],
                        )
                    return JevResponse(
                        http_status=r.status_code,
                        answers=answers,
                        model=payload.get("model"),
                        usage=payload.get("usage"),
                        latency_s=elapsed,
                        attempts=attempt,
                        raw=payload,
                    )
                last = JevError(
                    f"jev HTTP {r.status_code}", status=r.status_code, body=r.text[:500]
                )
                if r.status_code not in RETRY_STATUS:
                    raise last
            if attempt <= self.max_retries:
                time.sleep(self.backoff_s * attempt)
        raise last or JevError("jev request failed for an unknown reason")

    def ping(self) -> JevResponse:
        """Cheapest possible call that proves credentials and connectivity."""
        return self.ask(
            "The service is reachable.",
            {
                "connectivity": {
                    "type": "noul",
                    "instructions": "Does this state assert that something is reachable?",
                }
            },
        )

    def close(self) -> None:
        self._session.close()
