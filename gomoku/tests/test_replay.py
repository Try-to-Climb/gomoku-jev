"""Replaying a recorded game into the standalone viewer.

The invariant that matters here is the one the page's scrubber relies on:
replaying ``moves[].move`` with alternating colours must reproduce the final
board exactly. If that ever drifts, stepping back through a game would show
positions that were never played.
"""

import json
import random
import re
import unittest

from gomoku import live, replay
from gomoku.analysis import MoveAnalysis
from gomoku.board import Stone
from gomoku.match import play_match, play_series
from gomoku.metrics import summarize
from gomoku.players import HeuristicPlayer, RandomPlayer
from gomoku.rules import RuleSet
from gomoku.tests.test_live import ATTEMPT_FIELDS, MOVE_FIELDS, PAGE_FIELDS

RULES = RuleSet(size=9)


def one_match() -> dict:
    record = play_match(
        HeuristicPlayer(name="bot", seed=5),
        RandomPlayer(name="rnd", seed=5),
        RULES,
        rng=random.Random(5),
    )
    return record.to_dict()


def a_series() -> dict:
    records = play_series(
        HeuristicPlayer(name="bot", seed=6),
        RandomPlayer(name="rnd", seed=6),
        games=2,
        rules=RULES,
        rng=random.Random(6),
    )
    return {"summary": summarize(records), "games": [r.to_dict() for r in records]}


class TestLoadGames(unittest.TestCase):
    def write(self, tmpdir, payload):
        import pathlib

        path = pathlib.Path(tmpdir) / "record.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_single_match_shape(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            games = replay.load_games(self.write(tmp, one_match()))
        self.assertEqual(len(games), 1)

    def test_series_shape(self):
        """``cli --out`` writes {"summary": ..., "games": [...]}."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            games = replay.load_games(self.write(tmp, a_series()))
        self.assertEqual(len(games), 2)

    def test_something_else_is_refused_clearly(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                replay.load_games(self.write(tmp, {"hello": "world"}))
        self.assertIn("not a gomoku match record", str(ctx.exception))


class TestBuildState(unittest.TestCase):
    def setUp(self):
        self.record = one_match()
        self.state = replay.build_state(self.record)

    def test_state_has_every_field_the_page_reads(self):
        for field in PAGE_FIELDS:
            self.assertIn(field, self.state)

    def test_move_payload_matches_the_live_contract(self):
        self.assertEqual(len(self.state["moves"]), len(self.record["moves"]))
        for move in self.state["moves"]:
            for field in MOVE_FIELDS:
                self.assertIn(field, move)
            for attempt in move["attempts"]:
                for field in ATTEMPT_FIELDS:
                    self.assertIn(field, attempt)

    def test_replaying_the_moves_reproduces_the_final_board(self):
        """The scrubber's core invariant: move list and board never disagree."""
        size = RULES.size
        grid = [["" for _ in range(size)] for _ in range(size)]
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        last = None
        for move in self.state["moves"]:
            if not move["move"]:
                continue
            col = letters.index(move["move"][0])
            row = size - int(move["move"][1:])
            grid[row][col] = move["symbol"]
            last = move["move"]
        self.assertEqual(grid, self.state["board"])
        self.assertEqual(last, self.state["last_move"])

    def test_colours_alternate_as_the_page_assumes(self):
        symbols = [m["symbol"] for m in self.state["moves"]]
        self.assertEqual(symbols, ["X" if i % 2 == 0 else "O" for i in range(len(symbols))])

    def test_outcome_and_metrics_are_carried_over(self):
        self.assertEqual(self.state["status"], self.record["status"])
        self.assertEqual(self.state["winner"], self.record["winner_name"])
        self.assertIsNotNone(self.state["summary"])
        stats = next(iter(self.state["summary"]["players"].values()))
        self.assertIn("open_four_conversion", stats)

    def test_metrics_are_recomputed_not_trusted(self):
        """Older records carry no summary; the viewer must still show metrics."""
        self.assertIn("summary", self.state)
        names = set(self.state["summary"]["players"])
        self.assertEqual(names, {"bot", "rnd"})

    def test_an_unscorable_record_still_opens(self):
        broken = dict(self.record, status="not-a-status")
        state = replay.build_state(broken)
        self.assertIsNone(state["summary"])
        self.assertEqual(len(state["moves"]), len(self.record["moves"]))

    def test_prompts_are_not_embedded_in_the_page(self):
        record = one_match()
        record["moves"][0]["attempts"][0].setdefault("meta", {})["prompt"] = {"system": "x" * 5000}
        state = replay.build_state(record)
        self.assertNotIn("prompt", state["moves"][0]["attempts"][0]["meta"])

    def test_a_move_that_never_happened_does_not_break_the_replay(self):
        record = one_match()
        record["moves"][0]["move"] = None
        state = replay.build_state(record)
        self.assertIsNone(state["moves"][0]["move"])
        self.assertEqual(len(state["moves"]), len(record["moves"]))


class TestVerdictAgreement(unittest.TestCase):
    """Two implementations of one taxonomy: live (objects) and replay (dicts)."""

    FLAGS = (
        "took_win", "missed_win", "blocked_threat", "missed_block",
        "took_open_four", "missed_open_four", "prevented_open_four",
        "allowed_open_four", "unavoidable_loss",
    )

    def test_each_flag_maps_to_the_same_label_in_both(self):
        for flag in self.FLAGS:
            analysis = MoveAnalysis(stone=Stone.BLACK)
            setattr(analysis, flag, True)
            with self.subTest(flag):
                self.assertEqual(
                    replay._verdict(analysis.to_dict(9)), live._verdict(analysis), flag
                )

    def test_nothing_notable_yields_no_label_in_both(self):
        analysis = MoveAnalysis(stone=Stone.BLACK)
        self.assertIsNone(replay._verdict(analysis.to_dict(9)))
        self.assertIsNone(live._verdict(analysis))


class TestRenderPage(unittest.TestCase):
    def setUp(self):
        self.state = replay.build_state(one_match())
        self.page = replay.render_page(self.state)

    def test_the_marker_is_replaced_by_real_state(self):
        self.assertNotIn(replay.EMBED_MARKER, self.page)
        match = re.search(r"const EMBEDDED = (\{.*?\});\n", self.page, re.S)
        self.assertIsNotNone(match)
        embedded = json.loads(match.group(1))
        self.assertEqual(len(embedded["moves"]), len(self.state["moves"]))

    def test_the_page_needs_no_server(self):
        """A file:// open must not depend on state.json being fetchable."""
        self.assertIn("if (EMBEDDED)", self.page)
        self.assertIn('id="board"', self.page)

    def test_the_scrub_controls_are_present(self):
        for element in ('id="scrub"', 'id="prev"', 'id="next"', 'id="golive"'):
            self.assertIn(element, self.page)

    def test_the_marker_still_exists_in_the_source_page(self):
        """render_page and live._write_snapshot both depend on this exact line."""
        self.assertIn(replay.EMBED_MARKER, live.PAGE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
