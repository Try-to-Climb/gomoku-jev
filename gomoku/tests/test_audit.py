"""The audit document generator, run with offline players."""

import contextlib
import io
import pathlib
import tempfile
import unittest

from gomoku.audit import main


def run_audit(out: pathlib.Path, *extra: str) -> int:
    """Run the generator with its console chatter suppressed."""
    argv = ["--black", "heuristic", "--white", "random", "--size", "9", "--seed", "3",
            "--out", str(out), *extra]
    with contextlib.redirect_stdout(io.StringIO()):
        return main(argv)


class TestAuditDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name) / "audit.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_document_and_json_are_written(self):
        self.assertEqual(run_audit(self.out), 0)
        text = self.out.read_text(encoding="utf-8")
        for fragment in (
            "# Gomoku match audit",
            "## Move by move",
            "### Move 1 ",
            "**Position before this move**",
            "**Objective facts from the engine**",
            "**Position after the move**",
            "## Result",
            "## Metrics",
        ):
            self.assertIn(fragment, text)
        # one section per ply, and the board is rendered twice per ply
        plies = text.count("### Move ")
        self.assertGreater(plies, 4)
        self.assertEqual(text.count("**Position after the move**"), plies)
        self.assertTrue(self.out.with_suffix(".json").exists())

    def test_the_document_is_english_only(self):
        """The published artefact must not mix languages."""
        run_audit(self.out)
        text = self.out.read_text(encoding="utf-8")
        cjk = [c for c in text if "\u4e00" <= c <= "\u9fff"]
        self.assertEqual(cjk, [], f"unexpected CJK in the audit document: {set(cjk)}")

    def test_tactical_flags_are_surfaced(self):
        run_audit(self.out)
        text = self.out.read_text(encoding="utf-8")
        # the heuristic bot always converts a win, so the last move must be tagged
        self.assertIn("took the win", text)


if __name__ == "__main__":
    unittest.main()
