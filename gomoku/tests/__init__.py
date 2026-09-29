"""Test package.

Unit tests must never reach a model API: they are the fast, deterministic layer,
and a benchmark run costs real time and tokens. Importing this package installs a
guard that turns any outbound HTTP call inside the suite into a loud failure.

Live checks are separate, explicit commands: ``gomoku.probe``, ``gomoku.ablate``,
``gomoku.perceive``, ``gomoku.vision``, ``gomoku.audit``.

Set ``GOMOKU_ALLOW_NETWORK=1`` to lift the guard for a deliberately live test.
"""

import os

if os.environ.get("GOMOKU_ALLOW_NETWORK") != "1":

    class NetworkUsedInTests(AssertionError):
        """Raised when a unit test tries to talk to a model API."""

    def _deny(*args, **kwargs):
        raise NetworkUsedInTests(
            "a unit test tried to make an HTTP request; use a fake client "
            "(see FakeClient/FakeChat) or set GOMOKU_ALLOW_NETWORK=1 if the call is intended"
        )

    try:
        import requests

        requests.Session.request = _deny
        requests.Session.send = _deny
        requests.request = _deny
        requests.post = _deny
    except ImportError:  # pragma: no cover
        pass

    try:
        import httpx

        httpx.Client.send = _deny
        httpx.Client.request = _deny
    except ImportError:  # pragma: no cover
        pass
