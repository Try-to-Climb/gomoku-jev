"""The file-backed prompt templates: four parts, backend-first lookup."""

import os
import pathlib
import random
import tempfile
import unittest
from unittest import mock

from gomoku import prompts
from gomoku.candidates import candidate_points
from gomoku.game import Game
from gomoku.llm_player import JevConfig, move_question
from gomoku.prompts import (
    DEFAULT_TEMPLATE_DIR,
    PROMPT_VERSION,
    SHARED,
    PromptStyle,
    load_template,
    render,
    resolve,
    rules_and_state,
    rules_block,
    set_template_dir,
    state_block,
    system_prompt,
    tactics_block,
    tactics_versions,
    template_dir,
)
from gomoku.rules import RuleSet

#: part 1 + 3 (part 2 lives in shared/tactics/)
SHARED_FILES = ["rules.txt", "state.txt", "facts.txt", "options.txt"]

#: tactics wordings that exist only for one backend (not a shadow of a shared file,
#: so the comparison is still explicit: jev is deliberately on a different ladder)
BACKEND_ONLY_TACTICS = {"jev": ["p2-defence-first"]}

#: part 4, per backend
BACKEND_FILES = {
    "jev": ["instructions.txt"],
    "openai": ["system.txt", "system_free.txt", "user.txt", "answer_full.txt", "answer_terse.txt"],
}

#: backends may shadow a shared file, but that ends apples-to-apples comparison,
#: so it has to be a deliberate act: anything not listed here fails the test below
ALLOWED_OVERRIDES: set[tuple[str, str]] = set()


class TestLayout(unittest.TestCase):
    def tearDown(self):
        set_template_dir(None)

    def test_shared_files_exist(self):
        for name in SHARED_FILES:
            with self.subTest(name):
                path = DEFAULT_TEMPLATE_DIR / SHARED / name
                self.assertTrue(path.is_file(), path)
                self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_backend_files_exist(self):
        for backend, names in BACKEND_FILES.items():
            for name in names:
                with self.subTest(backend=backend, name=name):
                    path = DEFAULT_TEMPLATE_DIR / backend / name
                    self.assertTrue(path.is_file(), path)
                    self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_no_stray_files(self):
        """Templates are the four documented parts and nothing else."""
        self.assertEqual(
            sorted(p.name for p in (DEFAULT_TEMPLATE_DIR / SHARED).glob("*.txt")),
            sorted(SHARED_FILES),
        )
        for backend, names in BACKEND_FILES.items():
            with self.subTest(backend):
                self.assertEqual(
                    sorted(p.name for p in (DEFAULT_TEMPLATE_DIR / backend).glob("*.txt")),
                    sorted(names),
                )

    def test_no_backend_silently_shadows_a_shared_part(self):
        """Parts 1-3 must stay identical for every backend unless we say otherwise."""
        shared = {p.relative_to(DEFAULT_TEMPLATE_DIR / SHARED).as_posix()
                  for p in (DEFAULT_TEMPLATE_DIR / SHARED).rglob("*.txt")}
        for backend in BACKEND_FILES:
            folder = DEFAULT_TEMPLATE_DIR / backend
            for path in folder.rglob("*.txt"):
                name = path.relative_to(folder).as_posix()
                if name in shared and (backend, name) not in ALLOWED_OVERRIDES:
                    self.fail(
                        f"{backend}/{name} shadows shared/{name}; that makes the "
                        "jev-vs-chat comparison a prompt comparison. Add it to "
                        "ALLOWED_OVERRIDES and say so in the results if intended."
                    )

    def test_documented_in_the_folder_readme(self):
        readme = (DEFAULT_TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")
        for name in SHARED_FILES:
            self.assertIn(name, readme, f"shared/{name} is undocumented")
        for backend, names in BACKEND_FILES.items():
            for name in names:
                self.assertIn(name, readme, f"{backend}/{name} is undocumented")

    def test_versions_are_discovered_from_disk(self):
        versions = tactics_versions()
        self.assertIn(PROMPT_VERSION, versions)
        self.assertIn("p0-one-move", versions)

    def test_backend_only_tactics_are_visible_to_that_backend_alone(self):
        for backend, names in BACKEND_ONLY_TACTICS.items():
            for name in names:
                with self.subTest(backend=backend, name=name):
                    self.assertIn(name, tactics_versions(backend))
                    self.assertNotIn(name, tactics_versions())  # not shared
                    other = next(b for b in BACKEND_FILES if b != backend)
                    self.assertNotIn(name, tactics_versions(other))

    def test_trailing_newline_is_dropped(self):
        self.assertFalse(load_template("rules.txt").endswith("\n"))


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / SHARED / "tactics").mkdir(parents=True)
        (self.dir / SHARED / "rules.txt").write_text("SHARED RULES $size\n", encoding="utf-8")
        (self.dir / SHARED / "state.txt").write_text("SHARED STATE $board\n", encoding="utf-8")
        (self.dir / SHARED / "tactics" / "v1.txt").write_text("SHARED TACTICS $n\n", encoding="utf-8")
        (self.dir / "jev").mkdir()
        set_template_dir(self.dir)

    def tearDown(self):
        set_template_dir(None)
        self.tmp.cleanup()

    def test_falls_back_to_shared(self):
        self.assertEqual(resolve("rules.txt", "jev"), self.dir / SHARED / "rules.txt")
        self.assertEqual(load_template("rules.txt", "jev"), "SHARED RULES $size")

    def test_backend_file_wins(self):
        (self.dir / "jev" / "rules.txt").write_text("JEV RULES $size\n", encoding="utf-8")
        self.assertEqual(resolve("rules.txt", "jev"), self.dir / "jev" / "rules.txt")
        prompts._cache.clear()
        self.assertEqual(render("rules.txt", "jev", size=9), "JEV RULES 9")
        # the other backend still gets the shared one
        self.assertEqual(render("rules.txt", "openai", size=9), "SHARED RULES 9")

    def test_backend_specific_tactics_version(self):
        (self.dir / "jev" / "tactics").mkdir()
        (self.dir / "jev" / "tactics" / "vjev.txt").write_text("ONLY JEV $n\n", encoding="utf-8")
        self.assertEqual(tactics_versions(), ["v1"])
        self.assertEqual(tactics_versions("jev"), ["v1", "vjev"])
        self.assertEqual(tactics_block(RuleSet(size=9), "vjev", "jev"), "ONLY JEV 5")
        with self.assertRaises(ValueError) as ctx:
            tactics_block(RuleSet(size=9), "vjev", "openai")
        self.assertIn("unknown prompt version", str(ctx.exception))

    def test_missing_file_names_both_places(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            load_template("nope.txt", "jev")
        message = str(ctx.exception)
        self.assertIn("jev", message)
        self.assertIn(SHARED, message)

    def test_env_var(self):
        set_template_dir(None)
        with mock.patch.dict(os.environ, {"GOMOKU_TEMPLATES": str(self.dir)}):
            prompts._cache.clear()
            self.assertEqual(template_dir(), self.dir)
            self.assertEqual(load_template("rules.txt"), "SHARED RULES $size")
        prompts._cache.clear()


class TestRendering(unittest.TestCase):
    def tearDown(self):
        set_template_dir(None)

    def test_unknown_placeholder_names_the_file(self):
        with self.assertRaises(KeyError) as ctx:
            render("rules.txt", None, rules="x")
        self.assertIn("rules.txt", str(ctx.exception))

    def test_unknown_version_lists_what_is_available(self):
        with self.assertRaises(ValueError) as ctx:
            tactics_block(RuleSet(), "does-not-exist")
        self.assertIn("p1-threat-ladder", str(ctx.exception))

    def test_nothing_is_left_unsubstituted(self):
        rules = RuleSet(size=9)
        view = Game(rules).view()
        candidates = candidate_points(view, JevConfig(seed=1), random.Random(1))
        texts = [
            rules_block(rules),
            tactics_block(rules),
            state_block(view, PromptStyle()),
            rules_and_state(view, PromptStyle(), None, "jev"),
            system_prompt(rules, "black", "X", PromptStyle()),
            move_question(view, candidates)["instructions"],
        ]
        for text in texts:
            self.assertNotIn("$", text)


class TestEntryPointsImport(unittest.TestCase):
    """Every CLI must at least import and parse --help (catches stale imports)."""

    def test_help_parses(self):
        import contextlib
        import importlib
        import io

        for module in ("gomoku.cli", "gomoku.probe", "gomoku.audit", "gomoku.ablate"):
            with self.subTest(module):
                main = importlib.import_module(module).main
                buffer = io.StringIO()
                with self.assertRaises(SystemExit) as ctx:
                    with contextlib.redirect_stdout(buffer):
                        main(["--help"])
                self.assertEqual(ctx.exception.code, 0)
                self.assertIn("--templates", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
