"""The referee: runs a game between two players and records everything.

Every attempt a player makes is kept, including the rejected ones, because for
an LLM benchmark the failures are the interesting part: unparseable answers,
occupied points, coordinates off the board.
"""

from __future__ import annotations

import datetime as _dt
import random
import time
import uuid
from dataclasses import dataclass, field

from .analysis import MoveAnalysis, analyse_position, score_move
from .board import Move, Stone
from .game import Game
from .notation import to_notation
from .players import Player
from .rules import IllegalMovePolicy, Outcome, RuleSet, Status, win_status


@dataclass
class Attempt:
    """One call to ``player.propose``."""

    #: "accepted" | "no_move" | "illegal"
    verdict: str
    move: str | None = None
    raw: str | None = None
    error: str | None = None
    latency_s: float = 0.0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {"verdict": self.verdict, "latency_s": round(self.latency_s, 3)}
        if self.move:
            out["move"] = self.move
        if self.raw is not None:
            out["raw"] = self.raw
        if self.error:
            out["error"] = self.error
        if self.meta:
            out["meta"] = self.meta
        return out


@dataclass
class MoveRecord:
    ply: int
    stone: Stone
    player: str
    move: str | None
    attempts: list[Attempt] = field(default_factory=list)
    #: set when the referee had to substitute a move (RANDOM_FALLBACK policy)
    fallback: bool = False
    analysis: MoveAnalysis | None = None

    @property
    def latency_s(self) -> float:
        return sum(a.latency_s for a in self.attempts)

    @property
    def rejected(self) -> list[Attempt]:
        return [a for a in self.attempts if a.verdict != "accepted"]

    def to_dict(self, size: int) -> dict:
        out = {
            "ply": self.ply,
            "stone": self.stone.label,
            "player": self.player,
            "move": self.move,
            "latency_s": round(self.latency_s, 3),
            "attempts": [a.to_dict() for a in self.attempts],
        }
        if self.fallback:
            out["fallback"] = True
        if self.analysis is not None:
            out["analysis"] = self.analysis.to_dict(size)
        return out


@dataclass
class MatchRecord:
    game_id: str
    rules: RuleSet
    players: dict  # {"black": {...}, "white": {...}}
    moves: list[MoveRecord] = field(default_factory=list)
    status: Status = Status.IN_PROGRESS
    winner: Stone | None = None
    reason: str = ""
    started_at: str = ""
    duration_s: float = 0.0
    error: str | None = None

    @property
    def plies(self) -> int:
        return len(self.moves)

    def winner_name(self) -> str | None:
        if self.winner is None:
            return None
        return self.players[self.winner.label]["name"]

    def move_list(self) -> list[str]:
        return [m.move for m in self.moves if m.move]

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "rules": self.rules.to_dict(),
            "players": self.players,
            "status": self.status.value,
            "winner": self.winner.label if self.winner else None,
            "winner_name": self.winner_name(),
            "reason": self.reason,
            "plies": self.plies,
            "moves": [m.to_dict(self.rules.size) for m in self.moves],
            **({"error": self.error} if self.error else {}),
        }


def feedback_for(verdict: str, move: str | None, detail: str | None, size: int) -> str:
    """The correction text handed back to a player before it retries."""
    if verdict == "no_move":
        return (
            f"Your previous answer could not be used ({detail}). "
            "Reply with a single coordinate such as H8 (column letter then row number) "
            "and nothing else."
        )
    reasons = {
        "occupied": f"{move} is already taken. Choose an empty point.",
        "out_of_bounds": f"{move} is not on the board (columns A-{chr(ord('A') + size - 1)}, rows 1-{size}).",
        "opening_center_required": f"{move} is not allowed: the first stone must be the centre point.",
        "opening_pro_distance": (
            f"{move} is not allowed: black's second stone must be at least three "
            "intersections away from the centre."
        ),
        "game_over": "The game is already over.",
    }
    return "Illegal move. " + reasons.get(detail or "", f"{move} is not legal ({detail}).") + (
        " Answer with one legal coordinate only."
    )


def play_match(
    black: Player,
    white: Player,
    rules: RuleSet | None = None,
    *,
    annotate: bool = True,
    on_move=None,
    on_turn=None,
    game_id: str | None = None,
    rng: random.Random | None = None,
) -> MatchRecord:
    """Play one game. Returns the full record; never raises on player failure.

    ``on_turn(stone, player, game)`` fires before a player is asked, ``on_move``
    after the move is recorded -- a live view needs both.
    """
    rules = rules or RuleSet()
    rng = rng or random.Random()
    game = Game(rules)
    seats = {Stone.BLACK: black, Stone.WHITE: white}

    record = MatchRecord(
        game_id=game_id or uuid.uuid4().hex[:12],
        rules=rules,
        players={"black": black.describe(), "white": white.describe()},
        started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )
    started = time.perf_counter()

    for stone, player in seats.items():
        player.start_game(stone, game.view(stone))

    while not game.is_over:
        stone = game.current_stone
        player = seats[stone]
        view = game.view(stone)
        if on_turn is not None:
            on_turn(stone, player, game)
        analysis = analyse_position(game) if annotate else None

        move_record = MoveRecord(ply=game.ply + 1, stone=stone, player=player.name, move=None)
        feedback: str | None = None
        chosen: Move | None = None

        for _ in range(rules.max_retries + 1):
            t0 = time.perf_counter()
            try:
                response = player.propose(view, feedback)
            except Exception as exc:  # noqa: BLE001 - a crashing player just loses the point
                response = None
                elapsed = time.perf_counter() - t0
                detail = f"{type(exc).__name__}: {exc}"
                move_record.attempts.append(
                    Attempt(verdict="no_move", error=detail, latency_s=elapsed)
                )
                feedback = feedback_for("no_move", None, detail, rules.size)
                continue

            elapsed = time.perf_counter() - t0
            if response.move is None:
                detail = response.error or "no move returned"
                move_record.attempts.append(
                    Attempt(
                        verdict="no_move",
                        raw=response.raw,
                        error=detail,
                        latency_s=elapsed,
                        meta=dict(response.meta),
                    )
                )
                feedback = feedback_for("no_move", None, detail, rules.size)
                continue

            move = Move(*response.move)
            in_bounds = game.board.in_bounds(move.row, move.col)
            notation = to_notation(move, rules.size) if in_bounds else f"({move.row},{move.col})"
            reason = game.check(move)
            if reason is not None:
                move_record.attempts.append(
                    Attempt(
                        verdict="illegal",
                        move=notation,
                        raw=response.raw,
                        error=reason,
                        latency_s=elapsed,
                        meta=dict(response.meta),
                    )
                )
                feedback = feedback_for("illegal", notation, reason, rules.size)
                continue

            move_record.attempts.append(
                Attempt(
                    verdict="accepted",
                    move=notation,
                    raw=response.raw,
                    latency_s=elapsed,
                    meta=dict(response.meta),
                )
            )
            chosen = move
            break

        if chosen is None:
            if rules.illegal_move is IllegalMovePolicy.RANDOM_FALLBACK:
                legal = game.legal_moves()
                if legal:
                    chosen = rng.choice(legal)
                    move_record.fallback = True
                else:  # pragma: no cover - a full board ends the game earlier
                    game.declare_draw("no_legal_moves")
            if chosen is None and not game.is_over:
                winner = stone.opponent
                game.outcome = Outcome(
                    win_status(winner), winner, f"illegal_move_forfeit_by_{stone.label}"
                )

        if chosen is not None:
            move_record.move = to_notation(chosen, rules.size)
            game.play(chosen)
            if analysis is not None:
                score_move(analysis, chosen, game)

        move_record.analysis = analysis
        record.moves.append(move_record)
        if on_move is not None:
            on_move(move_record, game)

    record.status = game.status
    record.winner = game.winner
    record.reason = game.outcome.reason
    record.duration_s = time.perf_counter() - started

    final = game.view()
    for player in seats.values():
        player.end_game(final)
    return record


def play_series(
    player_a: Player,
    player_b: Player,
    games: int = 2,
    rules: RuleSet | None = None,
    *,
    swap_colors: bool = True,
    annotate: bool = True,
    on_move=None,
    on_turn=None,
    on_game=None,
    on_game_start=None,
    rng: random.Random | None = None,
) -> list[MatchRecord]:
    """Play ``games`` games, alternating colours so first-move advantage cancels."""
    records = []
    for i in range(games):
        if swap_colors and i % 2:
            black, white = player_b, player_a
        else:
            black, white = player_a, player_b
        if on_game_start is not None:
            on_game_start(i, black, white)
        record = play_match(
            black,
            white,
            rules,
            annotate=annotate,
            on_move=on_move,
            on_turn=on_turn,
            rng=rng,
        )
        records.append(record)
        if on_game is not None:
            on_game(record)
    return records
