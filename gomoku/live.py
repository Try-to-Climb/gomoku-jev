"""Live view: a local web page that shows the board and every raw model reply.

Stdlib only. A background ``ThreadingHTTPServer`` serves two things:

* ``/``           -- the page (``gomoku/live.html``)
* ``/state.json`` -- the whole match state, polled by the page

The match itself keeps running in the main thread, so the view never slows a game
down; if the browser is closed the game carries on regardless.

Wired through the referee's two callbacks: ``on_turn`` (a player is being asked,
show "thinking") and ``on_move`` (a move was recorded, show it with its raw
reply, token cost, confidence and the engine's verdict).
"""

from __future__ import annotations

import json
import pathlib
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .board import Stone
from .match import MatchRecord, MoveRecord
from .metrics import summarize
from .notation import to_notation
from .rules import RuleSet

PAGE = pathlib.Path(__file__).resolve().parent / "live.html"


class LiveBoard:
    """Shared state plus the HTTP server that publishes it."""

    def __init__(
        self,
        rules: RuleSet,
        port: int = 8765,
        open_browser: bool = True,
        host: str = "127.0.0.1",
        snapshot: pathlib.Path | None = None,
        serve: bool = True,
    ) -> None:
        self.rules = rules
        #: when set, every update is also written as a self-contained HTML file,
        #: which needs no network at all -- the way to watch from a Windows
        #: browser when a corporate proxy sits in front of WSL's localhost
        self.snapshot = pathlib.Path(snapshot) if snapshot else None
        self._lock = threading.Lock()
        self._state: dict = {
            "version": 0,
            "rules": rules.to_dict(),
            "players": {},
            "game_no": 0,
            "status": "waiting",
            "reason": "",
            "turn": None,
            "thinking": None,
            "board": [["" for _ in range(rules.size)] for _ in range(rules.size)],
            "last_move": None,
            "moves": [],
            "summary": None,
            "finished_games": [],
        }
        self._server = None
        self.port = None
        self.url = None
        if serve:
            handler = self._make_handler()
            try:
                self._server = ThreadingHTTPServer((host, port), handler)
            except OSError:
                # a previous run may still be holding the port (--live-hold); the game
                # matters more than the port number, so take any free one
                self._server = ThreadingHTTPServer((host, 0), handler)
                print(f"port {port} busy, serving on {self._server.server_address[1]} instead")
            self.port = self._server.server_address[1]
            self.url = f"http://{host}:{self.port}/"
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        self._write_snapshot()
        if open_browser and self.url:
            try:
                webbrowser.open(self.url)
            except Exception:  # pragma: no cover - headless / WSL without a browser
                pass

    # ------------------------------------------------------------------ server
    def _make_handler(self):
        board = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - stdlib naming
                if self.path.startswith("/state.json"):
                    body = board.snapshot_json().encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif self.path in ("/", "/index.html"):
                    try:
                        body = PAGE.read_bytes()
                    except FileNotFoundError:  # pragma: no cover
                        body = b"<h1>live.html is missing</h1>"
                    self._send(body, "text/html; charset=utf-8")
                else:
                    self.send_error(404)

            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep the console clean
                pass

        return Handler

    def snapshot_json(self) -> str:
        with self._lock:
            return json.dumps(self._state, ensure_ascii=False)

    def _bump(self) -> None:
        self._state["version"] += 1
        self._write_snapshot()

    def _write_snapshot(self) -> None:
        """Mirror the state into a standalone HTML file (no server involved)."""
        if self.snapshot is None:
            return
        try:
            page = PAGE.read_text(encoding="utf-8")
        except FileNotFoundError:  # pragma: no cover
            return
        state = json.dumps(self._state, ensure_ascii=False)
        page = page.replace("const EMBEDDED = null; // __EMBEDDED__",
                            f"const EMBEDDED = {state};")
        if self._state["status"] in ("in_progress", "waiting"):
            page = page.replace("<head>", '<head>\n<meta http-equiv="refresh" content="2">', 1)
        tmp = self.snapshot.with_suffix(".tmp")
        tmp.write_text(page, encoding="utf-8")
        tmp.replace(self.snapshot)

    # ------------------------------------------------------------- game events
    def start_game(self, game_no: int, black, white) -> None:
        with self._lock:
            if self._state["moves"]:
                self._state["finished_games"].append(
                    {
                        "players": self._state["players"],
                        "status": self._state["status"],
                        "reason": self._state["reason"],
                        "plies": len(self._state["moves"]),
                    }
                )
            size = self.rules.size
            self._state.update(
                {
                    "game_no": game_no + 1,
                    "players": {"black": black.describe(), "white": white.describe()},
                    "status": "in_progress",
                    "reason": "",
                    "turn": "black",
                    "thinking": None,
                    "board": [["" for _ in range(size)] for _ in range(size)],
                    "last_move": None,
                    "moves": [],
                    "summary": None,
                }
            )
            self._bump()

    def set_thinking(self, stone: Stone, player, game) -> None:
        with self._lock:
            self._state["turn"] = stone.label
            self._state["thinking"] = {"stone": stone.label, "player": player.name}
            self._bump()

    def add_move(self, record: MoveRecord, game) -> None:
        size = self.rules.size
        with self._lock:
            self._state["board"] = [
                [game.board.get(r, c).symbol if game.board.get(r, c) is not Stone.EMPTY else ""
                 for c in range(size)]
                for r in range(size)
            ]
            self._state["last_move"] = (
                to_notation(game.last_move, size) if game.last_move else None
            )
            self._state["moves"].append(self._move_payload(record))
            self._state["thinking"] = None
            self._state["status"] = game.status.value
            self._state["reason"] = game.outcome.reason
            self._state["turn"] = None if game.is_over else game.current_stone.label
            self._bump()

    def _move_payload(self, record: MoveRecord) -> dict:
        size = self.rules.size
        attempts = []
        for attempt in record.attempts:
            meta = dict(attempt.meta or {})
            meta.pop("prompt", None)  # kept out of the view: too long, already in the JSON log
            attempts.append(
                {
                    "verdict": attempt.verdict,
                    "move": attempt.move,
                    "raw": attempt.raw,
                    "error": attempt.error,
                    "latency_s": round(attempt.latency_s, 2),
                    "meta": meta,
                }
            )
        payload = {
            "ply": record.ply,
            "stone": record.stone.label,
            "symbol": record.stone.symbol,
            "player": record.player,
            "move": record.move,
            "latency_s": round(record.latency_s, 2),
            "fallback": record.fallback,
            "attempts": attempts,
        }
        if record.analysis is not None:
            a = record.analysis
            payload["analysis"] = a.to_dict(size)
            payload["verdict"] = _verdict(a)
        return payload

    def finish(self, match: MatchRecord) -> None:
        with self._lock:
            self._state["status"] = match.status.value
            self._state["reason"] = match.reason
            self._state["turn"] = None
            self._state["thinking"] = None
            self._state["winner"] = match.winner_name()
            self._state["summary"] = summarize([match])
            self._bump()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def hold_open(seconds: float) -> None:
    """Keep a finished game on screen: for a fixed time, or until Enter.

    The server thread is a daemon, so without this the page goes blank the moment
    the last move is scored.
    """
    import sys
    import time

    if seconds > 0:
        print(f"\nkeeping the live view up for {seconds:.0f}s...", flush=True)
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            pass
        return
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\nthe live view is still serving; press Enter to stop...")
    except (EOFError, KeyboardInterrupt):
        pass


def _verdict(analysis) -> str | None:
    """One short label per move, the same taxonomy the audit document uses."""
    if analysis.took_win:
        return "took_win"
    if analysis.missed_win:
        return "missed_win"
    if analysis.blocked_threat:
        return "blocked_five"
    if analysis.missed_block:
        return "missed_five_block"
    if analysis.took_open_four:
        return "took_open_four"
    if analysis.missed_open_four:
        return "missed_open_four"
    if analysis.prevented_open_four:
        return "prevented_open_four"
    if analysis.allowed_open_four:
        return "allowed_open_four"
    if analysis.unavoidable_loss:
        return "unavoidable_loss"
    return None
