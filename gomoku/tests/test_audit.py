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
            "# 五子棋对局审计记录",
            "## 逐手记录",
            "### 第 1 手",
            "**该手之前的局面**",
            "**引擎算出的客观事实**",
            "**落子之后**",
            "## 结果",
            "## 指标汇总",
        ):
            self.assertIn(fragment, text)
        # one section per ply, and the board is rendered twice per ply
        plies = text.count("### 第 ")
        self.assertGreater(plies, 4)
        self.assertEqual(text.count("**落子之后**"), plies)
        self.assertTrue(self.out.with_suffix(".json").exists())

    def test_tactical_flags_are_surfaced(self):
        run_audit(self.out)
        text = self.out.read_text(encoding="utf-8")
        # the heuristic bot always converts a win, so the last move must be tagged
        self.assertIn("抓住了一步杀", text)


if __name__ == "__main__":
    unittest.main()
