"""Aggregate match records into per-player scores.

Two families of numbers:

* *compliance* -- can the model answer at all? parse failures, illegal points,
  forfeits, latency.
* *play quality* -- does it see one move ahead? missed wins, missed blocks,
  win rate against a fixed baseline.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .board import Stone
from .match import MatchRecord
from .rules import Status


@dataclass
class PlayerStats:
    name: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    as_black: int = 0
    as_white: int = 0
    plies: int = 0
    #: accepted moves
    moves: int = 0
    attempts: int = 0
    parse_failures: int = 0
    illegal_attempts: int = 0
    forfeits: int = 0
    fallbacks: int = 0
    total_latency_s: float = 0.0
    win_chances: int = 0
    wins_taken: int = 0
    block_chances: int = 0
    blocks_made: int = 0
    #: one level earlier: making / preventing a four with two completing points
    open_four_chances: int = 0
    open_fours_taken: int = 0
    open_four_defences: int = 0
    open_fours_prevented: int = 0
    lost_positions: int = 0

    def _rate(self, num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "win_rate": self._rate(self.wins, self.games),
            "score": self._rate(self.wins * 2 + self.draws, self.games * 2),
            "as_black": self.as_black,
            "as_white": self.as_white,
            "moves": self.moves,
            "attempts_per_move": self._rate(self.attempts, self.moves) if self.moves else None,
            "avg_latency_s": round(self.total_latency_s / self.moves, 3) if self.moves else None,
            "parse_failure_rate": self._rate(self.parse_failures, self.attempts),
            "illegal_move_rate": self._rate(self.illegal_attempts, self.attempts),
            "forfeits": self.forfeits,
            "referee_fallbacks": self.fallbacks,
            "win_chances": self.win_chances,
            "win_conversion": self._rate(self.wins_taken, self.win_chances),
            "block_chances": self.block_chances,
            "block_rate": self._rate(self.blocks_made, self.block_chances),
            "open_four_chances": self.open_four_chances,
            "open_four_conversion": self._rate(self.open_fours_taken, self.open_four_chances),
            "open_four_defences": self.open_four_defences,
            "open_four_prevention": self._rate(
                self.open_fours_prevented, self.open_four_defences
            ),
            "unavoidable_losses": self.lost_positions,
            "avg_game_plies": self._rate(self.plies, self.games),
        }


def summarize(records: list[MatchRecord]) -> dict:
    """Per-player stats plus a few match-level totals."""
    stats: dict[str, PlayerStats] = {}

    def get(name: str) -> PlayerStats:
        if name not in stats:
            stats[name] = PlayerStats(name=name)
        return stats[name]

    status_counts: dict[str, int] = defaultdict(int)
    reasons: dict[str, int] = defaultdict(int)

    for record in records:
        status_counts[record.status.value] += 1
        reasons[record.reason] += 1
        seats = {
            Stone.BLACK: record.players["black"]["name"],
            Stone.WHITE: record.players["white"]["name"],
        }
        for stone, name in seats.items():
            s = get(name)
            s.games += 1
            s.plies += record.plies
            if stone is Stone.BLACK:
                s.as_black += 1
            else:
                s.as_white += 1
            if record.status is Status.DRAW:
                s.draws += 1
            elif record.winner is stone:
                s.wins += 1
            elif record.status.is_over:
                s.losses += 1
            if record.reason.startswith("illegal_move_forfeit_by_") and record.reason.endswith(
                stone.label
            ):
                s.forfeits += 1

        for move in record.moves:
            s = get(seats[move.stone])
            s.attempts += len(move.attempts)
            s.total_latency_s += move.latency_s
            if move.fallback:
                s.fallbacks += 1
            for attempt in move.attempts:
                if attempt.verdict == "no_move":
                    s.parse_failures += 1
                elif attempt.verdict == "illegal":
                    s.illegal_attempts += 1
                else:
                    s.moves += 1
            a = move.analysis
            if a is not None:
                if a.own_wins:
                    s.win_chances += 1
                    if a.took_win:
                        s.wins_taken += 1
                elif a.opponent_wins:
                    if a.unavoidable_loss:
                        s.lost_positions += 1
                    else:
                        s.block_chances += 1
                        if a.blocked_threat:
                            s.blocks_made += 1
                elif a.own_open_fours:
                    s.open_four_chances += 1
                    if a.took_open_four:
                        s.open_fours_taken += 1
                elif a.opponent_open_fours:
                    s.open_four_defences += 1
                    if a.prevented_open_four:
                        s.open_fours_prevented += 1

    return {
        "games": len(records),
        "status_counts": dict(status_counts),
        "end_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "players": {name: s.to_dict() for name, s in stats.items()},
    }


def format_summary(summary: dict) -> str:
    """Compact table for the console."""
    header = (
        f"{'player':24} {'G':>3} {'W':>3} {'D':>3} {'L':>3} {'win%':>6} "
        f"{'ill%':>6} {'parse%':>7} {'winconv':>8} {'block%':>7} "
        f"{'of.make':>8} {'of.stop':>8} {'lat s':>7}"
    )
    lines = [header, "-" * len(header)]
    for p in summary["players"].values():
        def pct(x):
            return f"{100 * x:5.1f}" if isinstance(x, (int, float)) else "    --"

        latency = p["avg_latency_s"]
        latency_text = f"{latency:.2f}" if latency is not None else "--"
        lines.append(
            f"{p['name'][:24]:24} {p['games']:3d} {p['wins']:3d} {p['draws']:3d} {p['losses']:3d} "
            f"{pct(p['win_rate']):>6} {pct(p['illegal_move_rate']):>6} "
            f"{pct(p['parse_failure_rate']):>7} {pct(p['win_conversion']):>8} "
            f"{pct(p['block_rate']):>7} {pct(p['open_four_conversion']):>8} "
            f"{pct(p['open_four_prevention']):>8} {latency_text:>7}"
        )
    return "\n".join(lines)
