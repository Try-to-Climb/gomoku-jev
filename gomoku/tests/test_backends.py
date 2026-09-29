"""The backend registry: spec parsing, aliases, and third-party registration.

The promise this file pins down is that adding a backend takes one file and one
:func:`register` call -- no edits to the CLI, the probe, the ablation runner or
the diagnostics. The test therefore registers a backend that does not exist
anywhere in the package and drives it through the referee.
"""

import unittest

from gomoku import backends
from gomoku.backends import Backend, build_player, names, register, split_spec, split_version
from gomoku.match import play_match
from gomoku.players import MoveResponse, Player
from gomoku.rules import RuleSet


class CornerPlayer(Player):
    """A stand-in third-party backend: always takes the first legal point."""

    def __init__(self, name="corner", seed=None, note=None):
        super().__init__(name)
        self.seed = seed
        self.note = note

    def propose(self, view, feedback=None):
        moves = view.legal_moves()
        return MoveResponse(move=moves[0] if moves else None)

    def describe(self):
        return {**super().describe(), "seed": self.seed, "note": self.note}


class RegistryTestCase(unittest.TestCase):
    """Restores the registry, which is module-level state shared by all tests."""

    def setUp(self):
        self._backends = dict(backends._REGISTRY)
        self._aliases = dict(backends._ALIASES)

    def tearDown(self):
        backends._REGISTRY.clear()
        backends._REGISTRY.update(self._backends)
        backends._ALIASES.clear()
        backends._ALIASES.update(self._aliases)


class TestLookup(RegistryTestCase):
    def test_builtins_are_registered(self):
        self.assertEqual(names(), ["heuristic", "human", "jev", "openai", "random"])

    def test_model_backends_are_distinguished_from_local_ones(self):
        self.assertEqual(names(models_only=True), ["jev", "openai"])

    def test_battery_capable_backends(self):
        """Only model backends can answer a fan-out question set."""
        self.assertEqual(names(with_battery=True), ["jev", "openai"])

    def test_aliases(self):
        self.assertEqual(backends.get("bot").name, "heuristic")
        self.assertEqual(backends.get("llm").name, "jev")

    def test_lookup_is_case_insensitive_and_trims(self):
        self.assertEqual(backends.get("  JeV ").name, "jev")

    def test_unknown_name_lists_the_known_ones(self):
        with self.assertRaises(KeyError) as ctx:
            backends.get("gpt5")
        self.assertIn("heuristic", str(ctx.exception))

    def test_spec_help_marks_which_backends_take_a_model(self):
        text = backends.spec_help()
        self.assertIn("jev[:<model>][@<prompt version>]", text)
        self.assertIn("random", text)
        self.assertNotIn("random[:<model>]", text)


class TestSpecParsing(RegistryTestCase):
    def test_plain_kind(self):
        self.assertEqual(split_spec("heuristic"), ("heuristic", ""))

    def test_kind_and_model(self):
        self.assertEqual(split_spec("jev:jev-1.13.0"), ("jev", "jev-1.13.0"))

    def test_version_rides_on_the_kind_when_there_is_no_model(self):
        """``jev@p0-one-move`` has no colon, so the suffix must be moved back."""
        self.assertEqual(split_spec("jev@p0-one-move"), ("jev", "@p0-one-move"))

    def test_model_and_version(self):
        self.assertEqual(
            split_spec("openai:Qwen/Qwen3-32B@p0-one-move"),
            ("openai", "Qwen/Qwen3-32B@p0-one-move"),
        )

    def test_split_version(self):
        self.assertEqual(split_version("model@v"), ("model", {"prompt_version": "v"}))
        self.assertEqual(split_version("model"), ("model", {}))

    def test_split_version_keeps_at_signs_inside_a_model_id(self):
        """Only the last @ is the version separator."""
        self.assertEqual(
            split_version("vendor/model@2024@p1"), ("vendor/model@2024", {"prompt_version": "p1"})
        )


class TestBuildPlayer(RegistryTestCase):
    def test_local_player_takes_the_name_after_the_colon(self):
        player = build_player("heuristic:mybot", seed=4)
        self.assertEqual(player.name, "mybot")
        self.assertEqual(player.describe()["seed"], 4)

    def test_local_players_ignore_model_overrides(self):
        """--candidates and friends are meaningless for a bot and must not crash."""
        player = build_player(
            "random", seed=1, overrides={"candidates": "all", "prompt_version": "p0-one-move"}
        )
        self.assertEqual(player.name, "random")

    def test_alias_builds_the_same_player(self):
        self.assertEqual(type(build_player("bot")).__name__, "HeuristicPlayer")

    def test_unknown_spec_exits_with_the_spec_list(self):
        with self.assertRaises(SystemExit) as ctx:
            build_player("nosuchthing")
        self.assertIn("nosuchthing", str(ctx.exception))
        self.assertIn("jev[:<model>]", str(ctx.exception))

    def test_model_overrides_reach_the_config(self):
        player = build_player(
            "jev:jev-1.13.0@p0-one-move", seed=9, overrides={"max_candidates": 12}
        )
        described = player.describe()
        self.assertEqual(described["model"], "jev-1.13.0")
        self.assertEqual(described["prompt_version"], "p0-one-move")
        self.assertEqual(described["max_candidates"], 12)

    def test_spec_version_beats_a_global_override(self):
        """``--black jev@p0-one-move`` wins over ``--prompt-version p1-threat-ladder``."""
        player = build_player("jev@p0-one-move", overrides={"prompt_version": "p1-threat-ladder"})
        self.assertEqual(player.describe()["prompt_version"], "p0-one-move")

    def test_free_mode_suffix_survives_a_version_suffix(self):
        player = build_player("openai:Qwen/Qwen3-32B@p0-one-move|free")
        described = player.describe()
        self.assertEqual(described["mode"], "free")
        self.assertEqual(described["model"], "Qwen/Qwen3-32B")
        self.assertEqual(described["prompt_version"], "p0-one-move")


class TestThirdPartyRegistration(RegistryTestCase):
    def register_corner(self, **kwargs):
        return register(
            Backend(
                "corner",
                "test-only backend that plays the first legal point",
                lambda arg, seed, overrides: CornerPlayer(
                    name=arg or "corner", seed=seed, note=overrides.get("note")
                ),
                **kwargs,
            )
        )

    def test_a_new_backend_appears_everywhere_at_once(self):
        self.register_corner()
        self.assertIn("corner", names())
        self.assertIn("corner", backends.spec_help())
        self.assertIn("corner", dict(backends.describe()))

    def test_a_new_backend_plays_through_the_referee(self):
        self.register_corner()
        record = play_match(
            build_player("corner:first", seed=1),
            build_player("heuristic", seed=1),
            RuleSet(size=9, max_plies=20),
        )
        self.assertTrue(record.status.is_over)
        self.assertEqual(record.players["black"]["name"], "first")

    def test_overrides_reach_a_third_party_factory(self):
        self.register_corner()
        player = build_player("corner", overrides={"note": "hello"})
        self.assertEqual(player.describe()["note"], "hello")

    def test_declaring_it_a_model_backend_puts_it_in_backend_choices(self):
        self.register_corner(is_model=True)
        self.assertIn("corner", names(models_only=True))

    def test_a_backend_without_a_battery_says_so(self):
        self.register_corner()
        with self.assertRaises(SystemExit) as ctx:
            backends.battery_for("corner")
        self.assertIn("question battery", str(ctx.exception))
        self.assertIn("jev", str(ctx.exception))

    def test_duplicate_registration_is_refused(self):
        """A typo must not silently shadow a working backend."""
        self.register_corner()
        with self.assertRaises(ValueError):
            self.register_corner()

    def test_duplicate_registration_is_allowed_when_explicit(self):
        self.register_corner()
        register(
            Backend("corner", "replaced", lambda arg, seed, overrides: CornerPlayer()),
            replace=True,
        )
        self.assertEqual(dict(backends.describe())["corner"], "replaced")

    def test_registering_does_not_disturb_the_built_ins(self):
        self.register_corner()
        self.assertEqual(backends.get("jev").name, "jev")
        self.assertEqual(type(build_player("heuristic")).__name__, "HeuristicPlayer")


if __name__ == "__main__":
    unittest.main()
