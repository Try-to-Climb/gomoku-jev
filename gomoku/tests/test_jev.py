"""jev integration, exercised without touching the network.

The only thing these tests cannot cover is the HTTP call itself; that is what
``python3 -m gomoku.probe`` is for.
"""

import json
import os
import pathlib
import random
import tempfile
import unittest
from unittest import mock

from gomoku.analysis import winning_moves
from gomoku.candidates import candidate_points, read_choice
from gomoku.game import Game
from gomoku.jev_client import JevError, JevResponse, key_fingerprint, load_api_key
from gomoku.llm_player import (
    QUESTION_ID,
    JevConfig,
    JevPlayer,
    build_llm_player,
    move_question,
)
from gomoku.match import play_match
from gomoku.metrics import summarize
from gomoku.notation import from_notation, to_notation
from gomoku.players import HeuristicPlayer
from gomoku.prompts import PromptStyle, rules_and_state
from gomoku.rules import RuleSet, Status


def coords(*texts, size=15):
    return [from_notation(t, size) for t in texts]


class FakeClient:
    """Stands in for :class:`JevClient`, recording what it was asked."""

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.calls = []
        self.closed = False

    def ask(self, state, questions, model=None):
        self.calls.append({"state": state, "questions": questions})
        if self.error:
            raise self.error
        answer = self.answer(questions) if callable(self.answer) else self.answer
        return JevResponse(
            http_status=200,
            answers={QUESTION_ID: answer},
            model="jev-test",
            usage={"input_tokens": 1, "output_tokens": 2},
            latency_s=0.01,
        )

    def close(self):
        self.closed = True


def choice_answer(coord, probabilities=None, confidence=0.9):
    return {
        "type": "choice",
        "choice": coord,
        "confidence": confidence,
        "probabilities": probabilities or {coord: 1.0},
    }


class TestApiKey(unittest.TestCase):
    def test_explicit_key_wins(self):
        self.assertEqual(load_api_key("abc123"), "abc123")

    def test_env_key(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "  from-env  "}):
            self.assertEqual(load_api_key(), "from-env")

    def test_key_file_accepts_an_assignment_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "jev.env"
            path.write_text('export TYPESAFE_API_KEY="tok-from-file"\n')
            with mock.patch.dict(
                os.environ, {"TYPESAFE_API_KEY_FILE": str(path)}, clear=True
            ):
                self.assertEqual(load_api_key(), "tok-from-file")

    def test_key_file_accepts_a_bare_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "secret"
            path.write_text("tok-bare\n")
            with mock.patch.dict(
                os.environ, {"TYPESAFE_API_KEY_FILE": str(path)}, clear=True
            ):
                self.assertEqual(load_api_key(), "tok-bare")

    def test_missing_key_file_is_reported(self):
        with mock.patch.dict(
            os.environ, {"TYPESAFE_API_KEY_FILE": "/nonexistent/jev.env"}, clear=True
        ):
            with self.assertRaises(JevError) as ctx:
                load_api_key()
        self.assertIn("not a file", str(ctx.exception))

    def test_missing_key_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(JevError) as ctx:
                load_api_key()
        self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))

    def test_no_in_repository_key_path_exists(self):
        """A working tree must never be somewhere a key can sit."""
        import gomoku.jev_client as jev_client

        source = pathlib.Path(jev_client.__file__).read_text(encoding="utf-8")
        self.assertNotIn("apikei", source)
        self.assertFalse(hasattr(jev_client, "KEY_FILE"))

    def test_fingerprint_does_not_leak_the_key(self):
        key = "sk-supersecretvalue-1234"
        fp = key_fingerprint(key)
        self.assertNotIn("supersecret", fp)
        self.assertNotIn("1234", fp)
        self.assertIn("len=24", fp)
        # stable for the same key, different for another one
        self.assertEqual(fp, key_fingerprint(key))
        self.assertNotEqual(fp, key_fingerprint(key + "x"))


class TestCandidateSelection(unittest.TestCase):
    def test_empty_board_is_capped(self):
        view = Game(RuleSet()).view()
        config = JevConfig(max_candidates=12, seed=1)
        points = candidate_points(view, config, random.Random(1))
        self.assertEqual(len(points), 12)
        self.assertEqual(len(set(points)), 12)

    def test_stays_near_the_action(self):
        game = Game.replay(coords("H8", "I9"))
        points = candidate_points(game.view(), JevConfig(radius=1, seed=1), random.Random(1))
        for move in points:
            self.assertLessEqual(min(abs(move.row - 7), abs(move.row - 6)), 1)

    def test_win_and_block_points_are_always_offered(self):
        # white to move: black threatens I8, white itself has no win
        game = Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8"))
        view = game.view()
        config = JevConfig(max_candidates=5, radius=1, seed=3)
        points = candidate_points(view, config, random.Random(3))
        must_block = winning_moves(game, view.opponent)[0]
        self.assertIn(must_block, points)

    def test_shuffle_changes_order_not_content(self):
        view = Game.replay(coords("H8", "I9")).view()
        config = JevConfig(seed=1)
        a = candidate_points(view, config, random.Random(1))
        b = candidate_points(view, config, random.Random(2))
        self.assertEqual(set(a), set(b))
        self.assertNotEqual(a, b)

    def test_all_mode_offers_every_legal_point(self):
        view = Game(RuleSet(size=7)).view()
        config = JevConfig(candidates="all", max_candidates=100, seed=1)
        self.assertEqual(len(candidate_points(view, config, random.Random(1))), 49)

    def test_no_candidates_when_game_is_over(self):
        game = Game.replay(coords("D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9", "H8"))
        self.assertIs(game.status, Status.BLACK_WIN)
        self.assertEqual(candidate_points(game.view(), JevConfig(), random.Random(1)), [])


class TestMoveQuestion(unittest.TestCase):
    def test_question_shape_matches_jev_choice_schema(self):
        view = Game.replay(coords("H8")).view()
        points = candidate_points(view, JevConfig(max_candidates=6, seed=1), random.Random(1))
        question = move_question(view, points)
        self.assertEqual(question["type"], "choice")
        self.assertIn("instructions", question)
        self.assertEqual(len(question["criteria"]), len(points))
        for coord, text in question["criteria"].items():
            self.assertEqual(coord, coord.upper())
            self.assertIn("column", text)
        self.assertNotIn("H8", question["criteria"])  # occupied points are never offered
        # the whole request body must be JSON-serialisable
        json.dumps({"state": "x", "model": "jev-latest", "questions": {QUESTION_ID: question}})

    def test_rules_and_state_is_self_contained(self):
        view = Game.replay(coords("H8", "I9")).view()
        text = rules_and_state(view, PromptStyle(), "H8 is taken.", "jev")
        for fragment in ("RULES", "Columns are letters A-O", "It is move 3", "1.XH8 2.OI9", "H8 is taken."):
            self.assertIn(fragment, text)


class TestReadChoice(unittest.TestCase):
    def test_plain_choice(self):
        move, error, meta = read_choice(choice_answer("H8"), 15, {"H8", "A1"})
        self.assertEqual(move, from_notation("H8", 15))
        self.assertIsNone(error)
        self.assertEqual(meta["confidence"], 0.9)
        self.assertEqual(meta["top_probabilities"], {"H8": 1.0})

    def test_lowercase_choice_is_accepted(self):
        move, error, _ = read_choice(choice_answer("h8"), 15, {"H8"})
        self.assertIsNone(error)
        self.assertEqual(move, from_notation("H8", 15))

    def test_argmax_fallback_when_choice_is_missing(self):
        answer = {"type": "choice", "probabilities": {"A1": 0.2, "H8": 0.8}}
        move, error, meta = read_choice(answer, 15, {"A1", "H8"})
        self.assertIsNone(error)
        self.assertEqual(move, from_notation("H8", 15))
        self.assertEqual(meta["choice_from"], "argmax")

    def test_option_outside_the_menu_is_rejected(self):
        move, error, _ = read_choice(choice_answer("B2"), 15, {"H8", "A1"})
        self.assertIsNone(move)
        self.assertIn("not one of the offered", error)

    def test_unusable_answer(self):
        for answer in ({}, {"type": "choice"}, {"type": "noul", "noul": 0.5}, "H8"):
            move, error, _ = read_choice(answer, 15, {"H8"})
            self.assertIsNone(move, answer)
            self.assertTrue(error)


class TestJevPlayer(unittest.TestCase):
    def test_propose_returns_the_chosen_move_with_metadata(self):
        game = Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8"))
        client = FakeClient(answer=lambda q: choice_answer("I8"))
        player = JevPlayer(config=JevConfig(seed=1), client=client)
        response = player.propose(game.view())
        self.assertEqual(response.move, from_notation("I8", 15))
        self.assertIsNone(response.error)
        self.assertEqual(response.meta["jev_model"], "jev-test")
        self.assertEqual(response.meta["usage"], {"input_tokens": 1, "output_tokens": 2})
        self.assertIn("candidates", response.meta)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(list(client.calls[0]["questions"]), [QUESTION_ID])

    def test_feedback_reaches_the_state(self):
        client = FakeClient(answer=lambda q: choice_answer(next(iter(q[QUESTION_ID]["criteria"]))))
        player = JevPlayer(config=JevConfig(seed=1), client=client)
        player.propose(Game.replay(coords("H8")).view(), feedback="H8 is already taken.")
        self.assertIn("H8 is already taken.", client.calls[0]["state"])

    def test_api_failure_becomes_a_move_error(self):
        client = FakeClient(error=JevError("jev HTTP 503", status=503, body="upstream"))
        player = JevPlayer(config=JevConfig(seed=1), client=client)
        response = player.propose(Game(RuleSet()).view())
        self.assertIsNone(response.move)
        self.assertIn("503", response.error)
        self.assertEqual(response.meta["http_status"], 503)

    def test_forced_move_skips_the_api(self):
        rules = RuleSet(size=5, win_length=5)
        game = Game(rules)
        # fill the board leaving exactly one empty point, avoiding any five
        layout = ["WBWBB", "WWBWW", "BBWBB", "WWBWW", "BBWB."]
        black = [(r, c) for r, row in enumerate(layout) for c, ch in enumerate(row) if ch == "B"]
        white = [(r, c) for r, row in enumerate(layout) for c, ch in enumerate(row) if ch == "W"]
        for i in range(len(black) + len(white)):
            pool = black if i % 2 == 0 else white
            game.play(pool.pop(0))
        client = FakeClient(error=JevError("must not be called"))
        player = JevPlayer(config=JevConfig(seed=1), client=client)
        response = player.propose(game.view())
        self.assertTrue(response.meta["forced"])
        self.assertEqual(client.calls, [])

    def test_plays_a_whole_match_through_the_referee(self):
        """A stubbed jev that always blocks/wins still has to satisfy the referee."""

        def answer(questions):
            criteria = questions[QUESTION_ID]["criteria"]
            return choice_answer(sorted(criteria)[0], {k: 1 / len(criteria) for k in criteria})

        player = JevPlayer(name="jev-stub", config=JevConfig(seed=2), client=FakeClient(answer=answer))
        record = play_match(
            player,
            HeuristicPlayer(name="bot", seed=2),
            RuleSet(size=9, max_plies=30),
            rng=random.Random(2),
        )
        self.assertTrue(record.status.is_over)
        stats = summarize([record])["players"]["jev-stub"]
        self.assertEqual(stats["illegal_move_rate"], 0.0)
        self.assertEqual(stats["parse_failure_rate"], 0.0)
        self.assertGreater(stats["moves"], 0)
        # metadata survives into the JSON record
        first = record.to_dict()["moves"][0]["attempts"][0]
        self.assertEqual(first["verdict"], "accepted")
        self.assertIn("confidence", first["meta"])

    def test_describe_reports_the_configuration(self):
        player = JevPlayer(config=JevConfig(model="jev-latest", seed=5), client=FakeClient())
        described = player.describe()
        self.assertEqual(described["name"], "jev:jev-latest")
        self.assertEqual(described["model"], "jev-latest")
        self.assertEqual(described["candidates"], "near")

    def test_close_releases_only_owned_clients(self):
        client = FakeClient()
        JevPlayer(config=JevConfig(), client=client).close()
        self.assertFalse(client.closed)  # injected clients are the caller's business

    def test_factory_spec_version_suffix(self):
        """@version lets the two seats of a match run different tactics."""
        self.assertEqual(
            build_llm_player("@p2-defence-first").config.prompt_version, "p2-defence-first"
        )
        self.assertEqual(build_llm_player("@p2-defence-first").config.model, "jev-latest")
        # the suffix beats a global default handed in by the CLI
        player = build_llm_player("jev@p2-defence-first", prompt_version="p1-threat-ladder")
        self.assertEqual(player.config.prompt_version, "p2-defence-first")

    def test_factory_spec(self):
        self.assertEqual(build_llm_player("").config.model, "jev-latest")
        self.assertEqual(build_llm_player("jev").config.model, "jev-latest")
        self.assertEqual(build_llm_player("jev-1.13.0").config.model, "jev-1.13.0")


if __name__ == "__main__":
    unittest.main()
