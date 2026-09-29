"""Rules, win detection and legality."""

import unittest

from gomoku.board import Board, Move, Stone
from gomoku.game import Game, IllegalMove
from gomoku.notation import from_notation
from gomoku.rules import (
    OCCUPIED,
    OPENING_CENTER_REQUIRED,
    OPENING_PRO_DISTANCE,
    OUT_OF_BOUNDS,
    Opening,
    OverlineRule,
    RuleSet,
    Status,
)


def play(game: Game, *coords: str) -> Game:
    for coord in coords:
        game.play(from_notation(coord, game.rules.size))
    return game


class TestTurnOrder(unittest.TestCase):
    def test_black_starts_and_alternates(self):
        game = Game()
        self.assertIs(game.current_stone, Stone.BLACK)
        game.play(Move(7, 7))
        self.assertIs(game.current_stone, Stone.WHITE)
        self.assertIs(game.board[Move(7, 7)], Stone.BLACK)
        game.play(Move(7, 8))
        self.assertIs(game.board[Move(7, 8)], Stone.WHITE)
        self.assertIs(game.current_stone, Stone.BLACK)

    def test_history_and_notation(self):
        game = play(Game(), "H8", "I9")
        self.assertEqual(game.notation(), ["H8", "I9"])
        self.assertEqual(game.last_move, from_notation("I9", 15))


class TestLegality(unittest.TestCase):
    def test_occupied(self):
        game = Game()
        game.play(Move(7, 7))
        self.assertEqual(game.check(Move(7, 7)), OCCUPIED)
        with self.assertRaises(IllegalMove):
            game.play(Move(7, 7))

    def test_out_of_bounds(self):
        game = Game()
        for bad in (Move(-1, 0), Move(0, -1), Move(15, 0), Move(0, 15)):
            self.assertEqual(game.check(bad), OUT_OF_BOUNDS)

    def test_legal_move_count_shrinks(self):
        game = Game(RuleSet(size=7))
        self.assertEqual(len(game.legal_moves()), 49)
        game.play(Move(3, 3))
        self.assertEqual(len(game.legal_moves()), 48)

    def test_no_moves_after_game_over(self):
        game = play(Game(), "A15", "A14", "B15", "B14", "C15", "C14", "D15", "D14", "E15")
        self.assertIs(game.status, Status.BLACK_WIN)
        self.assertEqual(game.legal_moves(), [])
        with self.assertRaises(IllegalMove):
            game.play(Move(5, 5))


class TestWinDetection(unittest.TestCase):
    def _win_in(self, moves, expect=Status.BLACK_WIN):
        game = Game()
        for coord in moves:
            self.assertFalse(game.is_over, f"game ended early before {coord}")
            game.play(from_notation(coord, 15))
        self.assertIs(game.status, expect)
        return game

    def test_horizontal(self):
        self._win_in(["D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9", "H8"])

    def test_vertical(self):
        self._win_in(["H4", "I4", "H5", "I5", "H6", "I6", "H7", "I7", "H8"])

    def test_diagonal_up(self):
        self._win_in(["D4", "A1", "E5", "A2", "F6", "A3", "G7", "A4", "H8"])

    def test_diagonal_down(self):
        self._win_in(["D8", "A1", "E7", "A2", "F6", "A3", "G5", "A4", "H4"])

    def test_white_can_win(self):
        game = Game()
        moves = ["A1", "D8", "A2", "E8", "A3", "F8", "A4", "G8", "B1", "H8"]
        for coord in moves:
            game.play(from_notation(coord, 15))
        self.assertIs(game.status, Status.WHITE_WIN)
        self.assertIs(game.winner, Stone.WHITE)

    def test_four_is_not_a_win(self):
        game = play(Game(), "D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9")
        self.assertIs(game.status, Status.IN_PROGRESS)

    def test_gap_breaks_the_line(self):
        # D8 E8 F8 G8 with I8 instead of H8 -> only four contiguous
        game = play(Game(), "D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9", "I8")
        self.assertIs(game.status, Status.IN_PROGRESS)

    def test_edge_win(self):
        self._win_in(["A15", "B14", "A14", "C14", "A13", "D14", "A12", "E14", "A11"])

    def test_custom_win_length(self):
        rules = RuleSet(size=7, win_length=3)
        game = play(Game(rules), "A1", "A7", "B1", "B7", "C1")
        self.assertIs(game.status, Status.BLACK_WIN)


class TestOverline(unittest.TestCase):
    def _overline_game(self, rules):
        """Black builds D8 E8 F8 H8 I8 then fills G8 -> a run of six."""
        game = Game(rules)
        for coord in ["D8", "D1", "E8", "E1", "F8", "F1", "H8", "H1", "I8", "I1"]:
            game.play(from_notation(coord, rules.size))
        self.assertIs(game.status, Status.IN_PROGRESS)
        game.play(from_notation("G8", rules.size))
        return game

    def test_freestyle_overline_wins(self):
        game = self._overline_game(RuleSet(overline=OverlineRule.WIN))
        self.assertIs(game.status, Status.BLACK_WIN)
        self.assertEqual(game.outcome.reason, "6_in_a_row")

    def test_standard_overline_is_inert(self):
        game = self._overline_game(RuleSet(overline=OverlineRule.IGNORE))
        self.assertIs(game.status, Status.IN_PROGRESS)

    def test_renju_overline_loses_for_black(self):
        game = self._overline_game(RuleSet(overline=OverlineRule.FORBIDDEN))
        self.assertIs(game.status, Status.WHITE_WIN)
        self.assertEqual(game.outcome.reason, "forbidden_overline_by_black")

    def test_renju_overline_wins_for_white(self):
        rules = RuleSet(overline=OverlineRule.FORBIDDEN)
        game = Game(rules)
        seq = ["A1", "D8", "A2", "E8", "A3", "F8", "A4", "H8", "A6", "I8", "A7"]
        for coord in seq:
            game.play(from_notation(coord, 15))
        self.assertIs(game.status, Status.IN_PROGRESS)
        game.play(from_notation("G8", 15))  # white completes six
        self.assertIs(game.status, Status.WHITE_WIN)
        self.assertEqual(game.outcome.reason, "6_in_a_row")

    def test_exact_five_still_wins_under_ignore(self):
        game = play(
            Game(RuleSet(overline=OverlineRule.IGNORE)),
            "D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9", "H8",
        )
        self.assertIs(game.status, Status.BLACK_WIN)


class TestDraw(unittest.TestCase):
    #: 5x5 layout with 13 black / 12 white where every row, column and both
    #: diagonals are mixed, so the board fills up with no five in a row.
    DRAWN = [
        "WBWBB",
        "WWBWW",
        "BBWBB",
        "WWBWW",
        "BBWBB",
    ]

    def test_full_board_is_a_draw(self):
        rules = RuleSet(size=5, win_length=5)
        black = [
            Move(r, c)
            for r, row in enumerate(self.DRAWN)
            for c, ch in enumerate(row)
            if ch == "B"
        ]
        white = [
            Move(r, c)
            for r, row in enumerate(self.DRAWN)
            for c, ch in enumerate(row)
            if ch == "W"
        ]
        self.assertEqual((len(black), len(white)), (13, 12))

        game = Game(rules)
        for i in range(25):
            pool = black if i % 2 == 0 else white
            self.assertFalse(game.is_over, f"game ended early at ply {i}")
            game.play(pool.pop(0))
        self.assertTrue(game.board.is_full)
        self.assertIs(game.status, Status.DRAW)
        self.assertEqual(game.outcome.reason, "board_full")

    def test_max_plies_draw(self):
        game = Game(RuleSet(size=7, max_plies=4))
        play(game, "A1", "B1", "A2", "B2")
        self.assertIs(game.status, Status.DRAW)
        self.assertEqual(game.outcome.reason, "max_plies_reached")


class TestOpenings(unittest.TestCase):
    def test_center_first(self):
        rules = RuleSet(opening=Opening.CENTER_FIRST)
        game = Game(rules)
        self.assertEqual(game.check(Move(0, 0)), OPENING_CENTER_REQUIRED)
        self.assertEqual(game.legal_moves(), [rules.center])
        game.play(rules.center)
        self.assertIsNone(game.check(Move(0, 0)))

    def test_pro_second_black_move_distance(self):
        rules = RuleSet(opening=Opening.PRO)
        game = Game(rules)
        game.play(rules.center)          # ply 0: black centre
        game.play(Move(7, 8))            # ply 1: white free
        near = Move(rules.center.row + 2, rules.center.col)
        far = Move(rules.center.row + 3, rules.center.col)
        self.assertEqual(game.check(near), OPENING_PRO_DISTANCE)
        self.assertIsNone(game.check(far))
        game.play(far)
        # constraint lifts afterwards
        self.assertIsNone(game.check(Move(rules.center.row + 1, rules.center.col)))

    def test_even_board_rejects_center_openings(self):
        with self.assertRaises(ValueError):
            RuleSet(size=14, opening=Opening.CENTER_FIRST)


class TestRuleSetValidation(unittest.TestCase):
    def test_bad_values(self):
        for kwargs in (
            {"size": 4},
            {"size": 30},
            {"win_length": 2},
            {"size": 6, "win_length": 7},
            {"max_retries": -1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    RuleSet(**kwargs)

    def test_round_trip(self):
        rules = RuleSet(
            size=13,
            win_length=5,
            overline=OverlineRule.FORBIDDEN,
            opening=Opening.PRO,
            max_retries=1,
        )
        self.assertEqual(RuleSet.from_dict(rules.to_dict()), rules)


class TestBoardGeometry(unittest.TestCase):
    def test_line_info_counts_open_ends(self):
        board = Board(15)
        for col in (5, 6, 7):
            board.place(Move(7, col), Stone.BLACK)
        length, open_ends = board.line_info(Move(7, 6), Stone.BLACK, (0, 1))
        self.assertEqual((length, open_ends), (3, 2))
        board.place(Move(7, 4), Stone.WHITE)
        length, open_ends = board.line_info(Move(7, 6), Stone.BLACK, (0, 1))
        self.assertEqual((length, open_ends), (3, 1))

    def test_place_rejects_occupied_and_offboard(self):
        board = Board(9)
        board.place(Move(0, 0), Stone.BLACK)
        with self.assertRaises(ValueError):
            board.place(Move(0, 0), Stone.WHITE)
        with self.assertRaises(ValueError):
            board.place(Move(9, 0), Stone.WHITE)

    def test_remove_restores_empty_count(self):
        board = Board(9)
        board.place(Move(1, 1), Stone.BLACK)
        self.assertEqual(board.empty_count, 80)
        self.assertIs(board.remove(Move(1, 1)), Stone.BLACK)
        self.assertEqual(board.empty_count, 81)

    def test_copy_is_independent(self):
        board = Board(9)
        board.place(Move(1, 1), Stone.BLACK)
        clone = board.copy()
        clone.place(Move(2, 2), Stone.WHITE)
        self.assertIs(board.get(2, 2), Stone.EMPTY)
        self.assertEqual(board.empty_count, 80)


if __name__ == "__main__":
    unittest.main()
