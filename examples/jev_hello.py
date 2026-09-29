#!/usr/bin/env python3
"""The smallest possible jev call: one state, one question, one answer.

This is the whole contract the rest of the package is built on. A move in
gomoku is exactly this shape -- the state is the board, the options are the
legal points, and the answer's probability distribution is what makes the
engine's pick comparable against ground truth.

    export TYPESAFE_API_KEY=...
    python3 examples/jev_hello.py

The key is resolved by gomoku.jev_client, so $TYPESAFE_API_KEY_FILE works too,
and the key itself is never printed -- only a length and a short hash.
"""

import json
import pathlib
import sys
import time

# Run from a bare checkout as well as from an installed package: a script puts its
# own directory on sys.path, not the working directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gomoku.jev_client import JevClient, JevError  # noqa: E402

QUESTION = {
    "pick": {
        "type": "choice",
        "instructions": "Which option best fits the state?",
        "criteria": {
            "eat": "Eat a full meal.",
            "wait": "Do not eat yet.",
            "snack": "Eat something small.",
        },
    }
}


def main() -> int:
    try:
        client = JevClient()
    except JevError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 1

    print(f"key: {client.key_fingerprint}")
    start = time.perf_counter()
    try:
        response = client.ask("I am hungry and dinner is in ten minutes.", QUESTION)
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    answer = response.answer("pick")
    print(f"HTTP {response.http_status}  {time.perf_counter() - start:.2f}s  model {response.model}")
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    print(f"usage: {response.usage}")
    # the pick is always the argmax of the distribution; the confidence is derived
    # from how peaked that distribution is -- see docs/EXPERIMENTS.md
    print(f"\nchose {answer.get('choice')!r} with confidence {answer.get('confidence')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
