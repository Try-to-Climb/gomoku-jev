"""The chat-model player, exercised without touching the network.

Live verification is ``python3 -m gomoku.probe --backend openai``.
"""

import json
import os
import random
import unittest
from unittest import mock

from gomoku.candidates import candidate_points, option_descriptions
from gomoku.game import Game
from gomoku.match import play_match
from gomoku.metrics import summarize
from gomoku.notation import from_notation
from gomoku.openai_player import (
    API_KEY_VAR,
    BASE_URL_VAR,
    DEFAULT_MODEL,
    MODE_FREE,
    ChatAuthError,
    ChatError,
    OpenAIConfig,
    OpenAIPlayer,
    build_openai_player,
    choice_system_prompt,
    choice_user_prompt,
    ensure_auth,
    read_choice_text,
    read_free_text,
)
from gomoku.players import HeuristicPlayer
from gomoku.rules import RuleSet


def coords(*texts, size=15):
    return [from_notation(t, size) for t in texts]


class FakeChat:
    """Stands in for :class:`ChatClient`, recording the prompts it received.

    ``reply`` may be a string, a callable taking the user turn, or a list of
    ``(text, finish_reason)`` pairs consumed one per call.
    """

    def __init__(self, reply="", error=None, usage=None):
        self.reply = reply
        self.error = error
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5}
        self.calls = []
        self.merge_system = False

    def complete(self, system, user, *, temperature, max_tokens):
        self.calls.append(
            {"system": system, "user": user, "temperature": temperature, "max_tokens": max_tokens}
        )
        if self.error:
            raise self.error
        finish_reason = "stop"
        if isinstance(self.reply, list):
            text, finish_reason = self.reply[min(len(self.calls) - 1, len(self.reply) - 1)]
        elif callable(self.reply):
            text = self.reply(user)
        else:
            text = self.reply
        return {
            "text": text,
            "usage": self.usage,
            "model": "fake-model",
            "finish_reason": finish_reason,
            "merged_system": self.merge_system,
        }

    def close(self):
        pass


BLOCK_POSITION = ("E8", "D8", "F8", "A1", "G8", "A3", "H8")  # white must play I8


class TestAuth(unittest.TestCase):
    """Credential resolution: an API key, or a keyless self-hosted endpoint."""

    def test_api_key_is_enough(self):
        with mock.patch.dict(os.environ, {API_KEY_VAR: "sk-x"}, clear=True):
            self.assertEqual(ensure_auth(), "api_key")

    def test_base_url_alone_is_enough(self):
        """A local server (Ollama, vLLM, llama.cpp) needs no key."""
        with mock.patch.dict(
            os.environ, {BASE_URL_VAR: "http://localhost:11434/v1"}, clear=True
        ):
            self.assertEqual(ensure_auth(), "base_url")

    def test_api_key_wins_over_base_url(self):
        with mock.patch.dict(
            os.environ, {API_KEY_VAR: "sk-x", BASE_URL_VAR: "http://x/v1"}, clear=True
        ):
            self.assertEqual(ensure_auth(), "api_key")

    def test_missing_configuration_is_actionable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ChatAuthError) as ctx:
                ensure_auth()
        message = str(ctx.exception)
        self.assertIn(API_KEY_VAR, message)
        self.assertIn(BASE_URL_VAR, message)

    def test_auth_error_is_a_chat_error(self):
        """So a caller can catch one exception type for the whole backend."""
        self.assertTrue(issubclass(ChatAuthError, ChatError))


class TestPrompts(unittest.TestCase):
    def setUp(self):
        self.game = Game.replay(coords(*BLOCK_POSITION))
        self.view = self.game.view()
        self.config = OpenAIConfig(seed=1, max_candidates=8)
        self.candidates = candidate_points(self.view, self.config, random.Random(1))

    def test_system_prompt_states_rules_and_json_schema(self):
        text = choice_system_prompt(self.view, self.config)
        for fragment in (
            "15x15",
            "Columns are letters A-O",
            '"choice"',
            '"probabilities"',
            "confidence =",
        ):
            self.assertIn(fragment, text)

    def test_both_backends_get_byte_identical_tactics(self):
        """The comparison must never turn into a prompt comparison."""
        from gomoku.llm_player import move_question
        from gomoku.prompts import tactics_block

        tactics = tactics_block(self.view.rules)
        jev_instructions = move_question(self.view, self.candidates)["instructions"]
        chat_system = choice_system_prompt(self.view, self.config)
        self.assertIn(tactics, jev_instructions)
        self.assertIn(tactics, chat_system)

    def test_prompt_version_is_recorded(self):
        from gomoku.prompts import PROMPT_VERSION

        self.assertEqual(
            OpenAIPlayer(config=self.config, client=FakeChat()).describe()["prompt_version"],
            PROMPT_VERSION,
        )

    def test_distribution_can_be_switched_off(self):
        text = choice_system_prompt(self.view, OpenAIConfig(require_distribution=False))
        self.assertIn('"choice"', text)
        self.assertNotIn('"probabilities"', text)

    def test_user_prompt_lists_the_same_menu(self):
        text = choice_user_prompt(self.view, self.config, self.candidates, None)
        self.assertIn("CANDIDATE POINTS", text)
        for coord in option_descriptions(self.view, self.candidates):
            self.assertIn(coord, text)

    def test_feedback_and_no_think(self):
        config = OpenAIConfig(seed=1, no_think=True)
        text = choice_user_prompt(self.view, config, self.candidates, "I8 is already taken.")
        self.assertIn("I8 is already taken.", text)
        self.assertTrue(text.rstrip().endswith("/no_think"))


class TestReaders(unittest.TestCase):
    def test_json_answer(self):
        text = '{"choice": "I8", "probabilities": {"I8": 0.9, "A1": 0.1}, "confidence": 0.8}'
        move, error, meta = read_choice_text(text, 15, {"I8", "A1"})
        self.assertEqual(move, from_notation("I8", 15))
        self.assertIsNone(error)
        self.assertEqual(meta["parsed_from"], "json")
        self.assertEqual(meta["confidence"], 0.8)

    def test_fenced_json_with_reasoning(self):
        text = '<think>A1 is pointless</think>\n```json\n{"choice": "I8"}\n```'
        move, error, meta = read_choice_text(text, 15, {"I8", "A1"})
        self.assertEqual(move, from_notation("I8", 15))
        self.assertIsNone(error)
        self.assertEqual(meta["parsed_from"], "json")

    def test_bare_coordinate_fallback(self):
        move, error, meta = read_choice_text("I play I8.", 15, {"I8", "A1"})
        self.assertEqual(move, from_notation("I8", 15))
        self.assertIsNone(error)
        self.assertEqual(meta["parsed_from"], "letter")

    def test_off_menu_pick_is_rejected(self):
        move, error, _ = read_choice_text('{"choice": "B2"}', 15, {"I8", "A1"})
        self.assertIsNone(move)
        self.assertIn("not one of the offered", error)

    def test_off_menu_bare_coordinate_is_rejected(self):
        move, error, _ = read_choice_text("I play B2.", 15, {"I8", "A1"})
        self.assertIsNone(move)
        self.assertIn("not one of the offered", error)

    def test_unusable_reply(self):
        move, error, _ = read_choice_text("I am not sure yet", 15, {"I8"})
        self.assertIsNone(move)
        self.assertTrue(error)

    def test_free_mode_reader(self):
        move, error, meta = read_free_text("MOVE: H8", 15)
        self.assertEqual(move, from_notation("H8", 15))
        self.assertIsNone(error)
        self.assertEqual(meta["matched"], "H8")


class TestOpenAIPlayer(unittest.TestCase):
    def test_choice_mode_returns_the_move_with_metadata(self):
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply='{"choice": "I8", "confidence": 0.77}')
        player = OpenAIPlayer(config=OpenAIConfig(seed=1), client=chat)
        response = player.propose(game.view())
        self.assertEqual(response.move, from_notation("I8", 15))
        self.assertIsNone(response.error)
        self.assertEqual(response.meta["mode"], "choice")
        self.assertEqual(response.meta["api_model"], "fake-model")
        self.assertEqual(response.meta["usage"], {"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(response.meta["confidence"], 0.77)
        self.assertEqual(chat.calls[0]["temperature"], 0.0)

    def test_free_mode_skips_the_menu(self):
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply="I8")
        player = OpenAIPlayer(config=OpenAIConfig(mode=MODE_FREE, seed=1), client=chat)
        response = player.propose(game.view())
        self.assertEqual(response.move, from_notation("I8", 15))
        self.assertNotIn("CANDIDATE POINTS", chat.calls[0]["user"])
        self.assertNotIn("candidates", response.meta)

    def test_free_mode_can_produce_an_illegal_move(self):
        """In free mode the referee, not the menu, is what catches bad answers."""
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply="H8")  # already occupied
        player = OpenAIPlayer(name="chat", config=OpenAIConfig(mode=MODE_FREE, seed=1), client=chat)
        record = play_match(
            HeuristicPlayer(name="bot", seed=1),
            player,
            RuleSet(max_retries=1, max_plies=2),
        )
        white_move = [m for m in record.moves if m.player == "chat"][0]
        self.assertEqual(white_move.attempts[0].verdict, "illegal")
        self.assertEqual(white_move.attempts[0].error, "occupied")

    def test_truncated_reply_triggers_a_terse_retry(self):
        """A reasoning model can burn the whole budget thinking and return nothing."""
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply=[("", "length"), ('{"choice": "I8"}', "stop")])
        player = OpenAIPlayer(config=OpenAIConfig(seed=1), client=chat)
        response = player.propose(game.view())
        self.assertEqual(response.move, from_notation("I8", 15))
        self.assertIsNone(response.error)
        self.assertTrue(response.meta["truncated"])
        self.assertTrue(response.meta["truncation_retry"])
        self.assertEqual(len(chat.calls), 2)
        # the retry drops the distribution requirement and bans step-by-step thinking
        self.assertNotIn('"probabilities"', chat.calls[1]["system"])
        self.assertIn("Do not reason", chat.calls[1]["system"])

    def test_truncation_retry_can_be_disabled(self):
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply=[("", "length"), ('{"choice": "I8"}', "stop")])
        player = OpenAIPlayer(config=OpenAIConfig(seed=1, retry_on_truncation=False), client=chat)
        response = player.propose(game.view())
        self.assertIsNone(response.move)
        self.assertEqual(len(chat.calls), 1)
        self.assertTrue(response.meta["truncated"])

    def test_a_good_first_answer_costs_one_call(self):
        game = Game.replay(coords(*BLOCK_POSITION))
        chat = FakeChat(reply='{"choice": "I8"}')
        player = OpenAIPlayer(config=OpenAIConfig(seed=1), client=chat)
        player.propose(game.view())
        self.assertEqual(len(chat.calls), 1)

    def test_api_failure_becomes_a_move_error(self):
        chat = FakeChat(error=ChatError("APIConnectionError: connection refused"))
        player = OpenAIPlayer(config=OpenAIConfig(seed=1), client=chat)
        response = player.propose(Game(RuleSet()).view())
        self.assertIsNone(response.move)
        self.assertIn("connection refused", response.error)

    def test_plays_a_whole_match_through_the_referee(self):
        def reply(user):
            menu = user.split("CANDIDATE POINTS (choose exactly one):\n")[1].split("\n")[0]
            first = sorted(menu.split(", "))[0]
            return json.dumps({"choice": first, "confidence": 0.5})

        player = OpenAIPlayer(name="chat", config=OpenAIConfig(seed=2), client=FakeChat(reply=reply))
        record = play_match(
            player,
            HeuristicPlayer(name="bot", seed=2),
            RuleSet(size=9, max_plies=30),
            rng=random.Random(2),
        )
        self.assertTrue(record.status.is_over)
        stats = summarize([record])["players"]["chat"]
        self.assertEqual(stats["illegal_move_rate"], 0.0)
        self.assertEqual(stats["parse_failure_rate"], 0.0)
        self.assertGreater(stats["moves"], 0)

    def test_describe_reports_the_configuration(self):
        player = OpenAIPlayer(config=OpenAIConfig(seed=5), client=FakeChat())
        described = player.describe()
        self.assertEqual(described["name"], DEFAULT_MODEL)
        self.assertEqual(described["model"], DEFAULT_MODEL)
        self.assertEqual(described["mode"], "choice")
        self.assertEqual(described["max_candidates"], 30)

    def test_factory_spec(self):
        self.assertEqual(build_openai_player("").config.model, DEFAULT_MODEL)
        self.assertEqual(build_openai_player("openai").config.model, DEFAULT_MODEL)
        self.assertEqual(build_openai_player("Qwen/Qwen3-32B").config.model, "Qwen/Qwen3-32B")
        versioned = build_openai_player("Qwen/Qwen3-32B@p0-one-move")
        self.assertEqual(versioned.config.prompt_version, "p0-one-move")
        self.assertEqual(versioned.config.model, "Qwen/Qwen3-32B")
        free = build_openai_player("Qwen/Qwen3-32B|free")
        self.assertEqual(free.config.mode, MODE_FREE)
        self.assertEqual(free.config.model, "Qwen/Qwen3-32B")


if __name__ == "__main__":
    unittest.main()
