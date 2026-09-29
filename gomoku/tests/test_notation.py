"""Coordinate notation and the tolerant LLM-reply parser."""

import unittest

from gomoku.board import Board, Move, Stone
from gomoku.notation import NotationError, from_notation, parse_move, to_notation
from gomoku.render import render_board, render_history, render_stone_lists


class TestNotation(unittest.TestCase):
    def test_center_of_15x15(self):
        self.assertEqual(to_notation(Move(7, 7), 15), "H8")
        self.assertEqual(from_notation("H8", 15), Move(7, 7))

    def test_corners(self):
        self.assertEqual(to_notation(Move(0, 0), 15), "A15")
        self.assertEqual(to_notation(Move(14, 14), 15), "O1")
        self.assertEqual(from_notation("A15", 15), Move(0, 0))
        self.assertEqual(from_notation("O1", 15), Move(14, 14))

    def test_round_trip_every_point(self):
        for size in (9, 15):
            for row in range(size):
                for col in range(size):
                    move = Move(row, col)
                    self.assertEqual(from_notation(to_notation(move, size), size), move)

    def test_case_and_spacing(self):
        for text in ("h8", "H8", " h 8 ", "H,8"):
            self.assertEqual(from_notation(text, 15), Move(7, 7))

    def test_rejects_off_board(self):
        for text in ("P1", "A16", "A0", "Z9"):
            with self.assertRaises(NotationError, msg=text):
                from_notation(text, 15)

    def test_rejects_nonsense(self):
        for text in ("", "middle", "8", "8H"):
            with self.assertRaises(NotationError, msg=text):
                from_notation(text, 15)


class TestParseMove(unittest.TestCase):
    def assert_parses(self, text, expected="H8", size=15):
        result = parse_move(text, size)
        self.assertIsNone(result.error, f"{text!r} -> {result.error}")
        self.assertEqual(result.move, from_notation(expected, size), f"from {text!r}")

    def test_bare_coordinate(self):
        self.assert_parses("H8")

    def test_lowercase_and_punctuation(self):
        self.assert_parses("h8.")
        self.assert_parses("My move: H8!")

    def test_takes_the_last_coordinate(self):
        self.assert_parses("I could play D4 or E5, but I will play H8")

    def test_json_move_field(self):
        self.assert_parses('{"move": "H8", "why": "centre"}')

    def test_json_row_col(self):
        self.assert_parses('{"row": 8, "col": 8}')

    def test_json_move_pair(self):
        self.assert_parses('{"move": [8, "H"]}')

    def test_code_fence(self):
        self.assert_parses("```json\n{\"move\": \"H8\"}\n```")

    def test_thinking_block_is_ignored(self):
        self.assert_parses("<think>D4 looks fine, no, G7</think>\nMOVE: H8")

    def test_number_pair(self):
        self.assert_parses("(8, 8)")
        self.assert_parses("row 8, column 8")

    def test_fullwidth_digits(self):
        self.assert_parses("Ｈ８")

    def test_empty_and_garbage(self):
        for text in ("", "   ", None, "I resign", "let me think about it"):
            result = parse_move(text, 15)
            self.assertIsNone(result.move, f"{text!r}")
            self.assertTrue(result.error)

    def test_off_board_is_reported_not_silently_clamped(self):
        result = parse_move("P16", 15)
        self.assertIsNone(result.move)
        self.assertIn("outside", result.error)

    def test_source_is_recorded(self):
        self.assertEqual(parse_move('{"move": "H8"}', 15).source, "json")
        self.assertEqual(parse_move("H8", 15).source, "letter")
        self.assertEqual(parse_move("8,8", 15).source, "pair")


class TestRender(unittest.TestCase):
    def test_grid_shape_and_labels(self):
        board = Board(15)
        board.place(Move(7, 7), Stone.BLACK)
        text = render_board(board)
        lines = text.splitlines()
        self.assertEqual(len(lines), 17)  # header + 15 rows + footer
        self.assertTrue(lines[0].strip().startswith("A"))
        self.assertTrue(lines[0].strip().endswith("O"))
        # row 8 is the 8th grid line from the bottom, i.e. index 8 counting the header
        row8 = lines[8]
        self.assertTrue(row8.startswith(" 8"))
        self.assertIn("X", row8)

    def test_last_move_is_lowercase(self):
        board = Board(9)
        board.place(Move(4, 4), Stone.BLACK)
        board.place(Move(4, 5), Stone.WHITE)
        text = render_board(board, last_move=Move(4, 5))
        self.assertIn("X o", text)

    def test_stone_lists(self):
        board = Board(15)
        board.place(Move(7, 7), Stone.BLACK)
        board.place(Move(0, 0), Stone.WHITE)
        text = render_stone_lists(board)
        self.assertIn("Black (X) stones: H8", text)
        self.assertIn("White (O) stones: A15", text)

    def test_history(self):
        self.assertEqual(render_history([], 15), "No moves played yet.")
        self.assertEqual(
            render_history([Move(7, 7), Move(7, 8)], 15), "1.XH8 2.OI8"
        )


if __name__ == "__main__":
    unittest.main()
