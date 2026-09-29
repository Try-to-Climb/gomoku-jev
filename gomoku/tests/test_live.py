"""The live view: HTTP contract between the server and the page.

These tests bind a loopback socket, which is not a model API call, so the
suite-wide network guard (requests/httpx) is untouched.
"""

import json
import random
import unittest
import urllib.error
import urllib.request

from gomoku.live import PAGE, LiveBoard, _verdict
from gomoku.analysis import MoveAnalysis
from gomoku.board import Stone
from gomoku.match import play_match
from gomoku.players import HeuristicPlayer, RandomPlayer
from gomoku.rules import RuleSet

#: every field the page reads out of state.json
PAGE_FIELDS = ("board", "moves", "thinking", "summary", "last_move", "status",
               "reason", "players", "game_no", "version")
MOVE_FIELDS = ("ply", "stone", "symbol", "player", "move", "latency_s", "attempts")
ATTEMPT_FIELDS = ("verdict", "move", "raw", "error", "latency_s", "meta")


class TestLiveServer(unittest.TestCase):
    def setUp(self):
        self.rules = RuleSet(size=9)
        self.live = LiveBoard(self.rules, port=0, open_browser=False)

    def tearDown(self):
        self.live.close()

    def get(self, path=""):
        with urllib.request.urlopen(self.live.url + path, timeout=5) as response:
            return response.status, response.read()

    def state(self):
        return json.loads(self.get("state.json")[1])

    def play(self):
        black, white = HeuristicPlayer(name="bot", seed=3), RandomPlayer(name="rnd", seed=3)
        self.live.start_game(0, black, white)
        record = play_match(
            black, white, self.rules, on_move=self.live.add_move,
            on_turn=self.live.set_thinking, rng=random.Random(3),
        )
        self.live.finish(record)
        return record

    def test_page_is_served(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertIn(b'id="board"', body)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_state_has_every_field_the_page_reads(self):
        state = self.state()
        for field in PAGE_FIELDS:
            self.assertIn(field, state)
        self.assertEqual(len(state["board"]), self.rules.size)

    def test_move_payload_contract(self):
        self.play()
        state = self.state()
        self.assertGreater(len(state["moves"]), 4)
        for move in state["moves"]:
            for field in MOVE_FIELDS:
                self.assertIn(field, move)
            for attempt in move["attempts"]:
                for field in ATTEMPT_FIELDS:
                    self.assertIn(field, attempt)

    def test_version_increases_with_events(self):
        before = self.state()["version"]
        self.play()
        self.assertGreater(self.state()["version"], before)

    def test_result_and_metrics_appear(self):
        record = self.play()
        state = self.state()
        self.assertEqual(state["status"], record.status.value)
        self.assertEqual(state["winner"], record.winner_name())
        stats = next(iter(state["summary"]["players"].values()))
        self.assertIn("open_four_conversion", stats)

    def test_prompts_are_not_exposed(self):
        """The view shows raw replies, not the (long) prompts we sent."""
        self.play()
        for move in self.state()["moves"]:
            for attempt in move["attempts"]:
                self.assertNotIn("prompt", attempt["meta"])

    def test_thinking_is_published_then_cleared(self):
        black, white = HeuristicPlayer(name="bot", seed=1), RandomPlayer(name="rnd", seed=1)
        self.live.start_game(0, black, white)
        game = __import__("gomoku.game", fromlist=["Game"]).Game(self.rules)
        self.live.set_thinking(Stone.BLACK, black, game)
        self.assertEqual(self.state()["thinking"]["player"], "bot")
        record = play_match(black, white, self.rules, on_move=self.live.add_move,
                            rng=random.Random(1))
        self.live.finish(record)
        self.assertIsNone(self.state()["thinking"])


class TestVerdictLabels(unittest.TestCase):
    def test_every_label_is_known_to_the_page(self):
        html = PAGE.read_text(encoding="utf-8")
        flags = ("took_win", "missed_win", "blocked_threat", "missed_block",
                 "took_open_four", "missed_open_four", "prevented_open_four",
                 "allowed_open_four", "unavoidable_loss")
        for flag in flags:
            analysis = MoveAnalysis(stone=Stone.BLACK)
            setattr(analysis, flag, True)
            label = _verdict(analysis)
            self.assertIsNotNone(label, flag)
            with self.subTest(flag):
                self.assertIn(label, html, f"{label} has no label in live.html")

    def test_no_verdict_when_nothing_notable(self):
        self.assertIsNone(_verdict(MoveAnalysis(stone=Stone.BLACK)))
