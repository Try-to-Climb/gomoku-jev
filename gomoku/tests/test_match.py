"""Referee behaviour, analysis annotations and metric aggregation."""

import json
import random
import unittest

from gomoku.analysis import (
    analyse_position,
    maximal_runs,
    open_four_moves,
    score_move,
    winning_moves,
)
from gomoku.board import Move, Stone
from gomoku.game import Game
from gomoku.match import play_match, play_series
from gomoku.metrics import format_summary, summarize
from gomoku.notation import from_notation, to_notation
from gomoku.players import HeuristicPlayer, MoveResponse, Player, RandomPlayer, ScriptedPlayer
from gomoku.prompts import PromptStyle, system_prompt, tactics_block, user_prompt
from gomoku.rules import IllegalMovePolicy, RuleSet, Status


def coords(*texts, size=15):
    return [from_notation(t, size) for t in texts]


class FlakyPlayer(Player):
    """Emits a scripted list of *raw answers*; used to exercise retry handling."""

    def __init__(self, answers, name="flaky"):
        super().__init__(name)
        self.answers = list(answers)
        self.calls = 0

    def propose(self, view, feedback=None):
        from gomoku.notation import parse_move

        raw = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        result = parse_move(raw, view.size)
        return MoveResponse(move=result.move, raw=raw, error=result.error)


class TestAnalysis(unittest.TestCase):
    def test_finds_its_own_winning_point(self):
        game = Game.replay(coords("D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9"))
        wins = winning_moves(game)  # black to move
        self.assertIn(from_notation("H8", 15), wins)
        self.assertIn(from_notation("C8", 15), wins)

    def test_finds_opponent_threats(self):
        # black D8 E8 F8 G8; white's stones are scattered on row 1 (no run)
        game = Game.replay(coords("D8", "A1", "E8", "C1", "F8", "E1", "G8"))
        analysis = analyse_position(game)  # white to move, black threatens
        self.assertEqual(analysis.stone, Stone.WHITE)
        self.assertEqual(analysis.own_wins, [])
        self.assertIn(from_notation("H8", 15), analysis.opponent_wins)
        self.assertIn(from_notation("C8", 15), analysis.opponent_wins)

    def test_missed_win_flag(self):
        game = Game.replay(coords("D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9"))
        analysis = score_move(analyse_position(game), from_notation("A1", 15))
        self.assertTrue(analysis.missed_win)
        self.assertFalse(analysis.took_win)

    def test_took_win_flag(self):
        game = Game.replay(coords("D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9"))
        analysis = score_move(analyse_position(game), from_notation("H8", 15))
        self.assertTrue(analysis.took_win)
        self.assertFalse(analysis.missed_win)

    def test_double_threat_is_unavoidable(self):
        game = Game.replay(coords("D8", "A1", "E8", "C1", "F8", "E1", "G8"))
        analysis = analyse_position(game)
        # black threatens both C8 and H8 -> no single move saves white
        self.assertGreater(len(analysis.opponent_wins), 1)
        score_move(analysis, from_notation("B1", 15))
        self.assertTrue(analysis.unavoidable_loss)
        self.assertFalse(analysis.missed_block)

    def test_single_threat_must_be_blocked(self):
        # black E8 F8 G8 H8 with the D8 end already blocked by white
        game = Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8"))
        analysis = analyse_position(game)  # white to move
        self.assertEqual(len(analysis.opponent_wins), 1)
        self.assertEqual(analysis.opponent_wins[0], from_notation("I8", 15))
        score_move(analysis, from_notation("I8", 15))
        self.assertTrue(analysis.blocked_threat)
        self.assertFalse(analysis.missed_block)

    def test_single_threat_missed(self):
        game = Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A3", "H8"))
        analysis = score_move(analyse_position(game), from_notation("A5", 15))
        self.assertTrue(analysis.missed_block)
        self.assertFalse(analysis.blocked_threat)


class TestHeuristicPlayer(unittest.TestCase):
    def test_takes_the_win(self):
        game = Game.replay(coords("D8", "D9", "E8", "E9", "F8", "F9", "G8", "G9"))
        bot = HeuristicPlayer(seed=1)
        response = bot.propose(game.view())
        self.assertIn(response.move, winning_moves(game))
        self.assertEqual(response.meta["policy"], "win")

    def test_blocks_the_threat(self):
        game = Game.replay(coords("E8", "D8", "F8", "A1", "G8", "A2", "H8"))
        bot = HeuristicPlayer(seed=1)
        response = bot.propose(game.view())
        self.assertEqual(response.move, from_notation("I8", 15))
        self.assertEqual(response.meta["policy"], "block")

    def test_beats_random_convincingly(self):
        records = play_series(
            HeuristicPlayer(name="bot", seed=7),
            RandomPlayer(name="rnd", seed=7),
            games=4,
            rules=RuleSet(size=9),
            rng=random.Random(7),
        )
        summary = summarize(records)
        self.assertEqual(summary["players"]["bot"]["wins"], 4)
        self.assertEqual(summary["players"]["rnd"]["wins"], 0)


class TestRefereeFlow(unittest.TestCase):
    def test_game_ends_with_a_winner(self):
        black = ScriptedPlayer(coords("D8", "E8", "F8", "G8", "H8"), name="b")
        white = ScriptedPlayer(coords("D9", "E9", "F9", "G9", "A1"), name="w")
        record = play_match(black, white, RuleSet())
        self.assertIs(record.status, Status.BLACK_WIN)
        self.assertEqual(record.winner_name(), "b")
        self.assertEqual(record.plies, 9)
        self.assertEqual(record.move_list()[-1], "H8")

    def test_retry_after_illegal_move(self):
        # white first answers with an occupied point, then a legal one
        black = ScriptedPlayer(coords("H8", "A1", "A2", "A3", "A4"), name="b")
        white = FlakyPlayer(["H8", "B2", "B3", "B4", "B5", "B6"], name="w")
        record = play_match(black, white, RuleSet(max_retries=2))
        white_moves = [m for m in record.moves if m.stone is Stone.WHITE]
        self.assertEqual(white_moves[0].move, "B2")
        self.assertEqual(len(white_moves[0].attempts), 2)
        self.assertEqual(white_moves[0].attempts[0].verdict, "illegal")
        self.assertEqual(white_moves[0].attempts[0].error, "occupied")

    def test_unparseable_answer_is_recorded(self):
        black = FlakyPlayer(["I have no idea", "H8"], name="b")
        white = ScriptedPlayer(coords("A1"), name="w")
        record = play_match(black, white, RuleSet(max_retries=1))
        first = record.moves[0]
        self.assertEqual(first.attempts[0].verdict, "no_move")
        self.assertEqual(first.move, "H8")

    def test_forfeit_after_exhausting_retries(self):
        black = FlakyPlayer(["nonsense"], name="b")
        white = ScriptedPlayer(coords("A1"), name="w")
        record = play_match(black, white, RuleSet(max_retries=1))
        self.assertIs(record.status, Status.WHITE_WIN)
        self.assertEqual(record.reason, "illegal_move_forfeit_by_black")
        self.assertEqual(len(record.moves[0].attempts), 2)
        self.assertIsNone(record.moves[0].move)
        summary = summarize([record])
        self.assertEqual(summary["players"]["b"]["forfeits"], 1)
        self.assertEqual(summary["players"]["b"]["parse_failure_rate"], 1.0)

    def test_random_fallback_keeps_the_game_going(self):
        black = FlakyPlayer(["nonsense"], name="b")
        white = HeuristicPlayer(name="w", seed=3)
        rules = RuleSet(size=9, max_retries=0, illegal_move=IllegalMovePolicy.RANDOM_FALLBACK)
        record = play_match(black, white, rules, rng=random.Random(3))
        self.assertTrue(record.status.is_over)
        self.assertNotIn("forfeit", record.reason)
        self.assertTrue(record.moves[0].fallback)
        self.assertEqual(summarize([record])["players"]["b"]["referee_fallbacks"], record.plies // 2 + record.plies % 2)

    def test_crashing_player_forfeits_without_raising(self):
        class Boom(Player):
            def propose(self, view, feedback=None):
                raise RuntimeError("kaboom")

        record = play_match(Boom("boom"), RandomPlayer("rnd", seed=1), RuleSet(size=9, max_retries=0))
        self.assertIs(record.status, Status.WHITE_WIN)
        self.assertIn("kaboom", record.moves[0].attempts[0].error)

    def test_record_is_json_serialisable(self):
        records = play_series(
            HeuristicPlayer(name="bot", seed=1),
            RandomPlayer(name="rnd", seed=1),
            games=2,
            rules=RuleSet(size=9),
            rng=random.Random(1),
        )
        payload = {"summary": summarize(records), "games": [r.to_dict() for r in records]}
        text = json.dumps(payload)
        self.assertIn("\"status\"", text)
        reloaded = json.loads(text)
        self.assertEqual(len(reloaded["games"]), 2)
        self.assertIn("analysis", reloaded["games"][0]["moves"][0])

    def test_colours_alternate_in_a_series(self):
        records = play_series(
            RandomPlayer(name="a", seed=1),
            RandomPlayer(name="b", seed=2),
            games=2,
            rules=RuleSet(size=9),
            rng=random.Random(1),
        )
        self.assertEqual(records[0].players["black"]["name"], "a")
        self.assertEqual(records[1].players["black"]["name"], "b")

    def test_summary_table_renders(self):
        records = play_series(
            HeuristicPlayer(name="bot", seed=1),
            RandomPlayer(name="rnd", seed=1),
            games=2,
            rules=RuleSet(size=9),
            rng=random.Random(1),
        )
        table = format_summary(summarize(records))
        self.assertIn("bot", table)
        self.assertIn("rnd", table)


class TestPrompts(unittest.TestCase):
    def test_system_prompt_mentions_rules_and_format(self):
        rules = RuleSet()
        text = system_prompt(rules, "black", "X", PromptStyle())
        self.assertIn("15x15", text)
        self.assertIn("H8", text)
        self.assertIn("Columns are letters A-O", text)
        self.assertIn("exactly one coordinate", text)

    def test_system_prompt_teaches_the_threat_ladder(self):
        text = system_prompt(RuleSet(), "black", "X", PromptStyle())
        for fragment in (
            "TACTICS",
            '"four"',
            '"open four"',
            '"open three"',
            "PRIORITIES, highest first",
            "DEFENCE COUNTS AS MUCH AS ATTACK",
            "It cannot be blocked",
        ):
            self.assertIn(fragment, text)

    def test_tactics_follow_the_win_length(self):
        text = tactics_block(RuleSet(size=9, win_length=4))
        self.assertIn("goal of 4 in a row", text)
        self.assertIn("3 of your stones in a line plus one empty point", text)

    def test_user_prompt_contains_board_and_feedback(self):
        game = Game.replay(coords("H8", "I9"))
        text = user_prompt(game.view(), PromptStyle(), feedback="H8 is already taken.")
        self.assertIn("It is move 3", text)
        self.assertIn("1.XH8 2.OI9", text)
        self.assertIn("already taken", text)
        self.assertIn("Black (X) stones: H8", text)

    def test_legal_move_listing_is_optional(self):
        game = Game(RuleSet(size=7))
        without = user_prompt(game.view(), PromptStyle())
        with_list = user_prompt(game.view(), PromptStyle(include_legal_moves=True))
        self.assertNotIn("Legal points", without)
        self.assertIn("Legal points", with_list)
        self.assertIn("A7", with_list)


if __name__ == "__main__":
    unittest.main()


class TestOpenFourAndLiveness(unittest.TestCase):
    """The threat level below "wins now": a four with two completing points."""

    def test_open_four_move_is_found(self):
        rules = RuleSet(size=9)
        # white D4 D5 D6 with D3 and D7 empty -> D7 or D3 makes an unstoppable four
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", "C5")],
            rules,
        )
        moves = open_four_moves(game, game.current_stone)
        self.assertEqual(
            sorted(to_notation(m, 9) for m in moves), ["D3", "D7"]
        )

    def test_no_open_four_when_one_end_is_blocked(self):
        rules = RuleSet(size=9)
        # black E5 F5 G5 with D5 white: only one completing point can ever appear
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "A1", "G5", "A2")], rules
        )
        moves = open_four_moves(game, game.current_stone)
        self.assertEqual([to_notation(m, 9) for m in moves], [])

    def test_dead_run_is_reported_dead(self):
        rules = RuleSet(size=9)
        # black E5 F5 boxed in by white D5 and G5 -> can never reach five
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "G5")], rules
        )
        runs = maximal_runs(game.board, Stone.BLACK, 5)
        row5 = [r for r in runs if r.direction == (0, 1)]
        self.assertEqual(len(row5), 1)
        self.assertEqual(row5[0].length, 2)
        self.assertFalse(row5[0].live)

    def test_live_run_is_reported_live(self):
        rules = RuleSet(size=9)
        game = Game.replay([from_notation(c, 9) for c in ("E5", "A1", "F5", "A2")], rules)
        runs = maximal_runs(game.board, Stone.BLACK, 5)
        row5 = [r for r in runs if r.direction == (0, 1)]
        self.assertTrue(row5[0].live)

    def test_edge_crowding_kills_a_run(self):
        rules = RuleSet(size=9)
        # black H5 I5 sits against the right edge: only 2 points remain in that direction
        game = Game.replay([from_notation(c, 9) for c in ("H5", "A1", "I5", "A2")], rules)
        runs = maximal_runs(game.board, Stone.BLACK, 5)
        row5 = [r for r in runs if r.direction == (0, 1)]
        self.assertEqual(row5[0].length, 2)
        self.assertTrue(row5[0].live)  # E5,F5,G5 are still free to the left
        game.play(from_notation("G5", 9))  # black G5 -> G,H,I plus F,E still open
        runs = maximal_runs(game.board, Stone.BLACK, 5)
        self.assertTrue([r for r in runs if r.direction == (0, 1)][0].live)

    def test_runs_are_not_double_counted(self):
        rules = RuleSet(size=9)
        game = Game.replay([from_notation(c, 9) for c in ("E5", "A1", "F5", "A2", "G5")], rules)
        runs = [r for r in maximal_runs(game.board, Stone.BLACK, 5) if r.direction == (0, 1)]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].length, 3)


class TestVisionCases(unittest.TestCase):
    """The line-liveness diagnostic: its ground truths must stay correct."""

    EXPECTED = {
        "blocked_both_ends_4": dict(cell_occupied=True, left_end_blocked=True,
                                    right_end_blocked=True, ends_empty=False,
                                    both_ends_white=True, either_end_white=True, window="4",
                                    count4=True, can_reach=False, cannot_reach=True),
        "blocked_one_end_4": dict(cell_occupied=False, left_end_blocked=True,
                                  right_end_blocked=False, ends_empty=False,
                                  both_ends_white=False, either_end_white=True,
                                  window="5_or_more", count4=True, can_reach=True,
                                  cannot_reach=False),
        "open_both_ends_3": dict(cell_occupied=False, left_end_blocked=False,
                                 right_end_blocked=False, ends_empty=True,
                                 both_ends_white=False, either_end_white=False,
                                 window="5_or_more", count4=False, can_reach=True,
                                 cannot_reach=False),
        "blocked_both_ends_2": dict(cell_occupied=True, left_end_blocked=True,
                                    right_end_blocked=True, ends_empty=False,
                                    both_ends_white=True, either_end_white=True,
                                    window="2_or_fewer", count4=False, can_reach=False,
                                    cannot_reach=True),
        "edge_and_stone_4": dict(cell_occupied=True, left_end_blocked=True,
                                 right_end_blocked=True, ends_empty=False,
                                 both_ends_white=False, either_end_white=True, window="4",
                                 count4=True, can_reach=False, cannot_reach=True),
    }

    def test_ground_truths(self):
        from gomoku.vision import build, cases

        for case in cases():
            with self.subTest(case.id):
                _, _, questions, truths = build(case)
                self.assertEqual(truths, self.EXPECTED[case.id])
                self.assertEqual(set(questions), set(self.EXPECTED[case.id]))
                for qid, question in questions.items():
                    self.assertTrue(question["instructions"].strip().endswith("?"), qid)

    def test_window_length_decides_liveness(self):
        """The choice question must be exactly the quantity that settles the inference."""
        from gomoku.vision import build, cases

        for case in cases():
            with self.subTest(case.id):
                _, run, _, truths = build(case)
                self.assertEqual(truths["can_reach"], truths["window"] == "5_or_more")
                self.assertEqual(truths["can_reach"], run.live)

    def test_truth_values_are_balanced(self):
        """A test where every answer is 'no' would not tell us anything."""
        from gomoku.vision import build, cases

        for qid in ("cell_occupied", "right_end_blocked", "ends_empty", "count4",
                    "can_reach", "cannot_reach"):
            values = [build(c)[3][qid] for c in cases()]
            with self.subTest(qid):
                self.assertIn(True, values)
                self.assertIn(False, values)


class TestInferenceDiagnostic(unittest.TestCase):
    """The abstract/colour/room questions must carry correct, balanced truths."""

    def test_abstract_truths_are_balanced_and_correct(self):
        from gomoku.infer import abstract_request

        state, questions, truths = abstract_request()
        self.assertIn("rules of the game in general", state)
        self.assertEqual(set(questions), set(truths))
        self.assertIn(True, truths.values())
        self.assertIn(False, truths.values())
        # the two that mirror a real board case
        self.assertFalse(truths["four_stones_no_gap"])
        self.assertTrue(truths["four_stones_one_gap"])

    def test_colour_questions_cover_all_three_states(self):
        from gomoku.infer import colour_request
        from gomoku.vision import cases

        case = next(c for c in cases() if c.id == "blocked_both_ends_4")
        _, questions, truths, room = colour_request(case)
        self.assertEqual(room, 0)
        self.assertEqual(truths["room_left"], "0")
        # one point asked three ways, exactly one of which is true
        self.assertEqual(
            [truths["white_is_white"], truths["white_is_empty"], truths["white_is_black"]],
            [True, False, False],
        )
        self.assertEqual(
            [truths["empty_is_empty"], truths["empty_is_white"], truths["empty_is_black"]],
            [True, False, False],
        )
        for question in questions.values():
            self.assertTrue(question["instructions"].strip().endswith("?"))

    def test_room_left_matches_the_window(self):
        """room_left must equal window minus the stones already there."""
        from gomoku.infer import colour_request
        from gomoku.vision import build, cases

        for case in cases():
            with self.subTest(case.id):
                _, run, _, vision_truths = build(case)
                _, _, truths, room = colour_request(case)
                window = vision_truths["window"]
                if window == "2_or_fewer":
                    self.assertEqual(room, 0)
                elif window != "5_or_more":
                    self.assertEqual(room, int(window) - run.length)


class TestFactsheet(unittest.TestCase):
    """The injected fact block: correct, and free of any recommendation."""

    def position(self):
        # black E5-H5 blocked at D5/I5 (dead); white D4-D6 open at D3/D7 (live);
        # black to move, white one move from an unstoppable open four
        return Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5")],
            RuleSet(size=9),
        ).view()

    def test_disabled_by_default(self):
        from gomoku import factsheet

        self.assertEqual(factsheet.render(self.position(), factsheet.NONE), "")

    def test_unknown_level_raises(self):
        from gomoku import factsheet

        with self.assertRaises(ValueError):
            factsheet.render(self.position(), "hints")

    def test_geometry_states_span_and_open_points(self):
        from gomoku import factsheet

        text = factsheet.render(self.position(), factsheet.GEOMETRY)
        self.assertIn("black E5 F5 G5 H5 (row): span 4, open points none", text)
        self.assertIn("white D4 D5 D6 (column): span 9, open points D3, D7", text)
        # geometry must not leak the verdict
        self.assertNotIn("DEAD", text)
        self.assertNotIn("open four", text)

    def test_status_adds_the_verdict(self):
        from gomoku import factsheet

        text = factsheet.render(self.position(), factsheet.STATUS)
        self.assertIn("black E5 F5 G5 H5 (row): DEAD", text)
        self.assertIn("white D4 D5 D6 (column): LIVE", text)
        self.assertNotIn("THREATS", text)

    def test_threats_name_the_open_four_points(self):
        from gomoku import factsheet

        text = factsheet.render(self.position(), factsheet.THREATS)
        self.assertIn("THREATS", text)
        self.assertIn("white can create an unstoppable open four", text)
        self.assertTrue("D7" in text and "D3" in text)

    def test_never_recommends_a_move(self):
        """Facts only: the text must not rank options or tell the model what to play."""
        from gomoku import factsheet

        banned = ("best", "should", "recommend", "play at", "choose", "you must")
        for level in (factsheet.GEOMETRY, factsheet.STATUS, factsheet.THREATS):
            text = factsheet.render(self.position(), level).lower()
            for word in banned:
                with self.subTest(level=level, word=word):
                    self.assertNotIn(word, text)

    def test_empty_board_has_a_fallback(self):
        from gomoku import factsheet

        text = factsheet.render(Game(RuleSet(size=9)).view(), factsheet.THREATS)
        self.assertIn("no line of two or more stones yet", text)
        self.assertIn("neither side can force a win", text)

    def test_reaches_the_state_only_when_enabled(self):
        from gomoku import factsheet
        from gomoku.prompts import PromptStyle, rules_and_state

        view = self.position()
        off = rules_and_state(view, PromptStyle(), None, "jev", factsheet.render(view, "none"))
        on = rules_and_state(view, PromptStyle(), None, "jev", factsheet.render(view, "status"))
        self.assertNotIn("LINE STATUS", off)
        self.assertIn("LINE STATUS", on)
        self.assertNotIn("$", on)  # no placeholder left unsubstituted


class TestOpenFourAnnotations(unittest.TestCase):
    """The threat level one move before a five, and its metrics."""

    def test_taking_an_open_four_is_recorded(self):
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5", "C5")],
            RuleSet(size=9),
        )
        analysis = analyse_position(game)  # white to move, D7/D3 make an open four
        self.assertEqual(
            sorted(to_notation(m, 9) for m in analysis.own_open_fours), ["D3", "D7"]
        )
        good = score_move(analyse_position(game), from_notation("D7", 9))
        self.assertTrue(good.took_open_four)
        self.assertFalse(good.missed_open_four)
        bad = score_move(analyse_position(game), from_notation("A1", 9))
        self.assertTrue(bad.missed_open_four)

    def test_allowing_an_open_four_is_recorded(self):
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "D5", "F5", "D6", "G5", "D4", "H5", "I5")],
            RuleSet(size=9),
        )
        analysis = analyse_position(game)  # black to move; white threatens D7/D3
        self.assertEqual(
            sorted(to_notation(m, 9) for m in analysis.opponent_open_fours), ["D3", "D7"]
        )
        after = Game.replay(list(game.history) + [from_notation("D7", 9)], RuleSet(size=9))
        blocked = score_move(analyse_position(game), from_notation("D7", 9), after)
        self.assertTrue(blocked.prevented_open_four)
        after_bad = Game.replay(list(game.history) + [from_notation("A1", 9)], RuleSet(size=9))
        missed = score_move(analyse_position(game), from_notation("A1", 9), after_bad)
        self.assertTrue(missed.allowed_open_four)

    def test_five_level_outranks_open_four_level(self):
        game = Game.replay(
            [from_notation(c, 9) for c in ("E5", "A1", "F5", "A2", "G5", "A3", "H5", "A4")],
            RuleSet(size=9),
        )
        analysis = analyse_position(game)  # black can finish at D5/I5
        self.assertTrue(analysis.own_wins)
        self.assertEqual(analysis.own_open_fours, [])  # moot: it can already win
        scored = score_move(analysis, from_notation("I5", 9))
        self.assertTrue(scored.took_win)
        self.assertFalse(scored.took_open_four)

    def test_metrics_expose_both_layers(self):
        record = play_match(
            HeuristicPlayer(name="bot", seed=3),
            RandomPlayer(name="rnd", seed=3),
            RuleSet(size=9),
            rng=random.Random(3),
        )
        stats = summarize([record])["players"]
        for name in ("bot", "rnd"):
            for key in ("open_four_chances", "open_four_conversion",
                        "open_four_defences", "open_four_prevention"):
                self.assertIn(key, stats[name])
        self.assertEqual(stats["bot"]["open_four_conversion"], 1.0)
        self.assertIn("of.make", format_summary(summarize([record])))


class TestOptionFacts(unittest.TestCase):
    """Per-candidate annotation: the consequence of each option, not a ranking."""

    def position(self):
        # black D6 E5 F4 diagonal three, both ends (C7/G3) empty; white to move
        return Game.replay(
            [from_notation(c, 9) for c in ("E5", "E6", "D6", "D5", "F4")], RuleSet(size=9)
        ).view()

    def test_plain_is_the_default_and_carries_no_facts(self):
        from gomoku.candidates import option_descriptions

        view = self.position()
        text = option_descriptions(view, view.legal_moves()[:5])
        for value in text.values():
            self.assertNotIn("open four", value)

    def test_unknown_level_raises(self):
        from gomoku.candidates import option_descriptions

        view = self.position()
        with self.assertRaises(ValueError):
            option_descriptions(view, view.legal_moves()[:2], "hints")

    def test_only_the_blocking_points_are_marked_safe(self):
        from gomoku.analysis import open_four_moves
        from gomoku.candidates import option_descriptions

        view = self.position()
        moves = view.legal_moves()
        text = option_descriptions(view, moves, "open_four")
        safe = {k for k, v in text.items() if "cannot create" in v}
        expected = {to_notation(m, 9) for m in open_four_moves(view.to_game(), view.opponent)}
        self.assertEqual(safe, expected)
        self.assertEqual(safe, {"C7", "G3"})

    def test_annotation_does_not_rank_or_recommend(self):
        from gomoku.candidates import option_descriptions

        view = self.position()
        banned = ("best", "should", "recommend", "prefer", "must")
        for value in option_descriptions(view, view.legal_moves(), "open_four").values():
            for word in banned:
                self.assertNotIn(word, value.lower())

    def test_the_board_is_left_untouched(self):
        from gomoku.candidates import option_descriptions

        view = self.position()
        before = view.board.to_rows()
        option_descriptions(view, view.legal_moves(), "open_four")
        self.assertEqual(view.board.to_rows(), before)

    def test_question_and_config_carry_it(self):
        from gomoku.llm_player import JevConfig, move_question

        view = self.position()
        question = move_question(view, view.legal_moves()[:6], "p1-threat-ladder", "open_four")
        self.assertTrue(any("open four" in v for v in question["criteria"].values()))
        self.assertEqual(JevConfig().to_dict()["option_facts"], "none")
        self.assertEqual(JevConfig(option_facts="open_four").to_dict()["option_facts"], "open_four")


class TestGradedOptionFacts(unittest.TestCase):
    """The graded annotation: a number per option, never a verdict."""

    def position(self):
        return Game.replay(
            [from_notation(c, 9) for c in ("E5", "E6", "D6", "D5", "F4")], RuleSet(size=9)
        ).view()

    def test_window_stats_counts_usable_windows(self):
        from gomoku.analysis import window_stats

        view = self.position()
        best, ways = window_stats(view.board, Stone.BLACK, 5)
        self.assertEqual(best, 3)   # D6 E5 F4 inside a clean five-window
        self.assertEqual(ways, 3)   # three such windows along that diagonal

    def test_blocking_reduces_the_count(self):
        from gomoku.analysis import window_stats

        view = self.position()
        board = view.board
        for coord, expected in (("C7", 1), ("G3", 1), ("B8", 2), ("F5", 3)):
            with self.subTest(coord):
                board.place(from_notation(coord, 9), Stone.WHITE)
                try:
                    _, ways = window_stats(board, Stone.BLACK, 5)
                finally:
                    board.remove(from_notation(coord, 9))
                self.assertEqual(ways, expected)

    #: what must never appear: a recommendation, or a verdict on the option.
    #: A factual superlative about the opponent ("the opponent's best line") is fine --
    #: the rule is "no telling the model what to play", not "no superlatives".
    BANNED = ("best move", "best point", "best choice", "you should", "must play",
              "recommend", "prefer", "cannot create", "unstoppable", "is safe")

    def test_graded_levels_never_recommend_or_judge(self):
        from gomoku.candidates import option_descriptions

        view = self.position()
        for level in ("windows", "ways"):
            text = " ".join(option_descriptions(view, view.legal_moves(), level).values()).lower()
            with self.subTest(level):
                for phrase in self.BANNED:
                    self.assertNotIn(phrase, text)

    def test_graded_levels_actually_differ_between_options(self):
        """A constant annotation would carry no information at all."""
        from gomoku.candidates import option_descriptions

        view = self.position()
        for level in ("windows", "ways"):
            values = set(option_descriptions(view, view.legal_moves(), level).values())
            with self.subTest(level):
                self.assertGreater(len(values), 3)

    def test_ways_is_one_sentence_with_one_number(self):
        """E14: the same number in a two-clause sentence was ignored; keep this one plain."""
        from gomoku.candidates import option_descriptions

        view = self.position()
        text = option_descriptions(view, [from_notation("C7", 9)], "ways")["C7"]
        body = text.split(". ", 1)[1]
        self.assertEqual(body.count(","), 0)
        self.assertEqual(sum(ch.isdigit() for ch in body.replace(" 5 ", " five ")), 1)


class TestThreatOptionFacts(unittest.TestCase):
    """The per-option annotation that always reports the most urgent threat.

    ``open_four`` alone goes blind once the opponent already has a four: its answer
    is then "no open four" for every option -- true, useless, and it cost a game
    (see EXPERIMENTS.md E15).
    """

    def five_level(self):
        """White has a diagonal four C4-D5-E6-F7; only B3 completes it. Black to move."""
        from gomoku.notation import from_notation as fn

        return Game.replay(
            [fn(c, 9) for c in ("E5", "D5", "F5", "G5", "C5", "E6", "D6", "F7",
                                "G8", "E7", "G7", "F6", "D8", "C4")],
            RuleSet(size=9),
        ).view()

    def open_four_level(self):
        """Black D6 E5 F4 with both ends empty; white to move."""
        from gomoku.notation import from_notation as fn

        return Game.replay(
            [fn(c, 9) for c in ("E5", "E6", "D6", "D5", "F4")], RuleSet(size=9)
        ).view()

    def test_five_level_is_reported(self):
        from gomoku.candidates import option_descriptions

        view = self.five_level()
        text = option_descriptions(view, view.legal_moves(), "threat")
        self.assertIn("completes 5 at B3", text["G6"])
        self.assertIn("cannot win within their next two turns", text["B3"])

    def test_open_four_alone_is_blind_at_the_five_level(self):
        """The regression this level exists for: the old annotation said the same
        thing for every option once the opponent already had a four."""
        from gomoku.candidates import option_descriptions

        view = self.five_level()
        old = option_descriptions(view, view.legal_moves(), "open_four")
        self.assertEqual(len(set(v.split(". ", 1)[1] for v in old.values())), 1)
        new = option_descriptions(view, view.legal_moves(), "threat")
        self.assertEqual(len(set(v.split(". ", 1)[1] for v in new.values())), 2)

    def test_five_outranks_open_four(self):
        from gomoku.candidates import option_descriptions

        view = self.five_level()
        for value in option_descriptions(view, view.legal_moves(), "threat").values():
            if "completes 5" in value:
                self.assertNotIn("open four", value)

    def test_open_four_level_still_reported_when_there_is_no_five(self):
        from gomoku.candidates import option_descriptions

        view = self.open_four_level()
        text = option_descriptions(view, view.legal_moves(), "threat")
        safe = {k for k, v in text.items() if "cannot win" in v}
        self.assertEqual(safe, {"C7", "G3"})
        self.assertIn("unstoppable open four", text["F5"])

    def test_one_short_sentence_per_option(self):
        """E14: a second clause defeats it. Keep each annotation to one plain sentence."""
        from gomoku.candidates import option_descriptions

        for view in (self.five_level(), self.open_four_level()):
            for value in option_descriptions(view, view.legal_moves(), "threat").values():
                body = value.split(". ", 1)[1]
                self.assertEqual(body.count(","), 0, body)
                self.assertEqual(body.count("."), 1, body)

    def test_never_recommends(self):
        from gomoku.candidates import option_descriptions

        for view in (self.five_level(), self.open_four_level()):
            text = " ".join(option_descriptions(view, view.legal_moves(), "threat").values())
            for phrase in ("best move", "you should", "must play", "recommend", "prefer"):
                self.assertNotIn(phrase, text.lower())
