"""Gomoku engine for benchmarking LLM play.

The engine is deliberately player-agnostic: :mod:`gomoku.match` only knows how
to ask a :class:`~gomoku.players.Player` for a coordinate, so a model behind an
HTTP API, a local bot and a human all plug into the same referee and produce the
same machine-readable record.
"""

from .analysis import MoveAnalysis, analyse_position, score_move, winning_moves
from .board import Board, Move, Stone
from .game import Game, GameView, IllegalMove
from .match import Attempt, MatchRecord, MoveRecord, play_match, play_series
from .metrics import format_summary, summarize
from .notation import from_notation, parse_move, to_notation
from .players import (
    CallablePlayer,
    ConsolePlayer,
    HeuristicPlayer,
    MoveResponse,
    Player,
    RandomPlayer,
    ScriptedPlayer,
)
from .render import render_board
from .rules import (
    IllegalMovePolicy,
    Opening,
    Outcome,
    OverlineRule,
    RuleSet,
    Status,
    describe,
)

__all__ = [
    "Attempt",
    "Board",
    "CallablePlayer",
    "ConsolePlayer",
    "Game",
    "GameView",
    "HeuristicPlayer",
    "IllegalMove",
    "IllegalMovePolicy",
    "MatchRecord",
    "Move",
    "MoveAnalysis",
    "MoveRecord",
    "MoveResponse",
    "Opening",
    "Outcome",
    "OverlineRule",
    "Player",
    "RandomPlayer",
    "RuleSet",
    "ScriptedPlayer",
    "Status",
    "Stone",
    "analyse_position",
    "describe",
    "format_summary",
    "from_notation",
    "parse_move",
    "play_match",
    "play_series",
    "render_board",
    "score_move",
    "summarize",
    "to_notation",
    "winning_moves",
]
