"""Play one game and write a human-auditable, move-by-move Markdown log.

    python3 -m gomoku.audit --black jev --white openai --size 9

For every ply the document records, in order: the position before the move, the
objective tactical facts the engine computed *before* asking (so you can judge
the answer against something other than hindsight), the exact option menu the
model was given, its raw reply with confidence and token cost, the resulting
position, and the referee's verdict. Rejected attempts are kept too.

The file is flushed after every move, so you can read along while the game runs.
The matching JSON record (same basename, ``.json``) has the full prompts.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import time
import sys

from .board import Stone
from .cli import build_player
from .game import Game
from .metrics import format_summary, summarize
from .notation import to_notation
from .render import render_board
from .rules import IllegalMovePolicy, Opening, OverlineRule, RuleSet, describe

RESULTS = pathlib.Path(__file__).resolve().parent / "results"

STONE_CN = {Stone.BLACK: "黑 ✕ (X)", Stone.WHITE: "白 ○ (O)"}
STATUS_CN = {
    "in_progress": "进行中",
    "black_win": "黑胜",
    "white_win": "白胜",
    "draw": "和棋",
}


class AuditWriter:
    """Streams the Markdown document as the game is played."""

    def __init__(self, path: pathlib.Path, rules: RuleSet, players: dict) -> None:
        self.path = path
        self.rules = rules
        self.players = players
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w", encoding="utf-8")
        self._header()

    def _w(self, text: str = "") -> None:
        self.fh.write(text + "\n")
        self.fh.flush()

    def _header(self) -> None:
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self._w("# 五子棋对局审计记录")
        self._w()
        self._w(f"- 生成时间: {now}")
        self._w(f"- 黑方 ✕: **{self.players['black']['name']}**")
        self._w(f"- 白方 ○: **{self.players['white']['name']}**")
        self._w(f"- 棋盘: {self.rules.size}×{self.rules.size}，{self.rules.win_length} 连胜")
        self._w(f"- 规则: {describe(self.rules)}")
        self._w()
        self._w("选手配置:")
        self._w()
        self._w("```json")
        self._w(json.dumps(self.players, indent=2, ensure_ascii=False))
        self._w("```")
        self._w()
        self._w("怎么读这份记录: 每一手先给「该手之前」的局面和引擎算出的客观事实"
                "（自己/对手的一步成五点），这些是在向模型提问**之前**算好的，"
                "模型看不到；然后是给模型的候选菜单、模型的原始回复、落子后的局面和判定。")
        self._w()
        self._w("---")
        self._w()
        self._w("## 逐手记录")

    # ------------------------------------------------------------------ moves
    def move(self, record, game: Game) -> None:
        size = self.rules.size
        before = Game.replay(game.history[:-1], self.rules) if record.move else game
        stone = record.stone
        move_text = record.move or "（未能落子）"

        tag = ""
        a = record.analysis
        if a is not None:
            if a.took_win:
                tag = " — ✅ 抓住了一步杀"
            elif a.missed_win:
                tag = " — ❌ 有一步杀却没走"
            elif a.blocked_threat:
                tag = " — ✅ 堵住了对手的成五点"
            elif a.missed_block:
                tag = " — ❌ 该堵没堵"
            elif a.unavoidable_loss:
                tag = " — ⚠️ 对手已双杀点，无解"

        self._w()
        self._w(f"### 第 {record.ply} 手 · {STONE_CN[stone]} · {record.player} → **{move_text}**{tag}")
        self._w()

        self._w("**该手之前的局面**")
        self._w()
        self._w("```")
        self._w(render_board(before.board, before.last_move))
        self._w("```")
        self._w()

        if a is not None:
            own = [to_notation(m, size) for m in a.own_wins]
            opp = [to_notation(m, size) for m in a.opponent_wins]
            self._w("**引擎算出的客观事实**（提问前算好，模型看不到）")
            self._w()
            self._w(f"- 自己走一步就成五的点: {', '.join(own) if own else '无'}")
            self._w(f"- 对手走一步就成五的点: {', '.join(opp) if opp else '无'}"
                    + ("  ← 必须堵" if len(opp) == 1 and not own else "")
                    + ("  ← 两个点，堵不住了" if len(opp) > 1 and not own else ""))
            self._w(f"- 合法空点总数: {len(before.legal_moves())}")
            self._w()

        for i, attempt in enumerate(record.attempts, 1):
            self._attempt(i, attempt, len(record.attempts))

        if record.fallback:
            self._w("> ⚠️ 重试用尽，裁判代为随机落子（`random_fallback` 策略）。")
            self._w()

        self._w("**落子之后**")
        self._w()
        self._w("```")
        self._w(render_board(game.board, game.last_move))
        self._w("```")
        self._w()
        self._w(f"判定: **{STATUS_CN.get(game.status.value, game.status.value)}**"
                + (f"（{game.outcome.reason}）" if game.outcome.reason else ""))
        self._w()
        self._w("---")

    def _attempt(self, index: int, attempt, total: int) -> None:
        meta = attempt.meta or {}
        verdict = {
            "accepted": "采纳",
            "illegal": "非法落子，已拒绝",
            "no_move": "无法解析出落子，已拒绝",
        }.get(attempt.verdict, attempt.verdict)
        label = "**模型回答**" if total == 1 else f"**第 {index} 次尝试 — {verdict}**"
        self._w(label)
        self._w()

        menu = meta.get("menu")
        if menu:
            self._w(f"- 给出的候选（{len(menu)} 个，顺序已打乱）: `{', '.join(menu)}`")
        if attempt.move:
            self._w(f"- 选择: **{attempt.move}**")
        if attempt.error:
            self._w(f"- 问题: `{attempt.error}`")
        if "confidence" in meta:
            self._w(f"- confidence: {meta['confidence']}")
        if meta.get("top_probabilities"):
            probs = "，".join(f"{k} {v}" for k, v in meta["top_probabilities"].items())
            self._w(f"- 概率最高的几个: {probs}")
        usage = meta.get("usage") or {}
        bits = [f"耗时 {attempt.latency_s:.2f}s"]
        if usage:
            bits.append(f"tokens in/out {usage.get('input_tokens')}/{usage.get('output_tokens')}")
        if meta.get("finish_reason"):
            bits.append(f"finish_reason={meta['finish_reason']}")
        if meta.get("truncation_retry"):
            bits.append("已因截断重试")
        if meta.get("forced"):
            bits.append("只剩一个合法点，未调用 API")
        self._w(f"- {'，'.join(bits)}")
        if attempt.raw:
            raw = attempt.raw if len(attempt.raw) <= 1200 else attempt.raw[:1200] + " …（截断）"
            self._w()
            self._w("原始回复:")
            self._w()
            self._w("```json")
            self._w(raw)
            self._w("```")
        self._w()

    # ------------------------------------------------------------------ footer
    def finish(self, match) -> None:
        self._w()
        self._w("## 结果")
        self._w()
        winner = match.winner_name() or "无（和棋）"
        self._w(f"- 终局: **{STATUS_CN.get(match.status.value, match.status.value)}**"
                f"（{match.reason}）")
        self._w(f"- 胜者: **{winner}**")
        self._w(f"- 手数: {match.plies}，总耗时 {match.duration_s:.1f}s")
        self._w(f"- 棋谱: `{' '.join(match.move_list())}`")
        self._w()
        self._w("## 指标汇总")
        self._w()
        self._w("```")
        self._w(format_summary(summarize([match])))
        self._w("```")
        self._w()
        self._w("指标口径: `winconv` = 有一步杀时抓住的比例；`block%` = 对手只有一个成五点时堵住的比例"
                "（对手有两个点时算 `unavoidable_loss`，不计入分母）；`ill%` / `parse%` 分母是尝试次数。")
        self._w()
        self._w("## 附：发给双方的完整提示词")
        self._w()
        self._w("每方取其第一手的真实请求原文（后续每手只有局面和候选在变）。")
        for seat in ("black", "white"):
            name = self.players[seat]["name"]
            prompt = None
            for record in match.moves:
                if record.player == name:
                    for attempt in record.attempts:
                        prompt = (attempt.meta or {}).get("prompt")
                        if prompt:
                            break
                if prompt:
                    break
            if not prompt:
                continue
            self._w()
            self._w(f"<details><summary>{seat} · {name}</summary>")
            self._w()
            for key, value in prompt.items():
                self._w(f"**{key}**")
                self._w()
                self._w("```" + ("json" if not isinstance(value, str) else ""))
                self._w(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False))
                self._w("```")
                self._w()
            self._w("</details>")
        self._w()
        self.fh.close()


def _per_seat(value: str) -> dict[str, str]:
    """``"status"`` -> both seats; ``"white=status,black=none"`` -> one seat each."""
    if "=" not in value:
        return {"black": value, "white": value}
    out = {"black": "none", "white": "none"}
    for part in value.split(","):
        seat, _, level = part.partition("=")
        seat = seat.strip().lower()
        if seat not in out:
            raise SystemExit(f"unknown seat {seat!r} (black | white)")
        out[seat] = level.strip()
    return out


def _hold_live(seconds: float) -> None:
    """Keep the live view up after the game: for a fixed time, or until Enter."""
    if seconds > 0:
        print(f"\nlive view 保持 {seconds:.0f} 秒后关闭…", flush=True)
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            pass
        return
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\nlive view 仍在运行，按回车关闭…")
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Play one game and write an audit document.")
    ap.add_argument("--black", default="jev")
    ap.add_argument("--white", default="openai")
    ap.add_argument("--size", type=int, default=9)
    ap.add_argument("--win-length", type=int, default=5)
    ap.add_argument("--overline", choices=[o.value for o in OverlineRule], default=OverlineRule.WIN.value)
    ap.add_argument("--opening", choices=[o.value for o in Opening], default=Opening.FREE.value)
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--illegal", choices=[p.value for p in IllegalMovePolicy],
                    default=IllegalMovePolicy.FORFEIT.value)
    ap.add_argument("--max-plies", type=int, default=None)
    ap.add_argument("--candidates", choices=["near", "all"], default="near")
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--templates", default=None, metavar="DIR",
                    help="load prompt text from this directory instead of gomoku/templates")
    ap.add_argument("--facts", default="none",
                    help="inject engine-computed line facts into the state: "
                         "none|span|status|geometry|threats, optionally per seat, "
                         "e.g. --facts white=status (changes what is measured; recorded)")
    ap.add_argument("--option-facts", default="none",
                    help="annotate each candidate option: none|ways|windows|open_four|threat|full, "
                         "optionally per seat, e.g. --option-facts black=open_four")
    ap.add_argument("--prompt-version", default=None,
                    help="tactical wording: p0-one-move (control) or p1-threat-ladder")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--live", action="store_true",
                    help="serve a live board + raw-reply view on http://127.0.0.1:<port>")
    ap.add_argument("--live-port", type=int, default=8765)
    ap.add_argument("--live-file", type=pathlib.Path, default=None, metavar="PATH",
                    help="also mirror the live view into a self-contained HTML file "
                         "(open it directly; needs no network)")
    ap.add_argument("--live-host", default="127.0.0.1",
                    help="bind address for --live; 0.0.0.0 exposes the view to your network")
    ap.add_argument("--live-hold", type=float, default=0.0, metavar="SECONDS",
                    help="keep serving this long after the last game (0 = until Enter)")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser for --live")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    rules = RuleSet(
        size=args.size,
        win_length=args.win_length,
        overline=OverlineRule(args.overline),
        opening=Opening(args.opening),
        max_retries=args.max_retries,
        illegal_move=IllegalMovePolicy(args.illegal),
        max_plies=args.max_plies,
    )
    overrides = {
        "candidates": args.candidates,
        "max_candidates": args.max_candidates,
        "log_prompts": True,
    }
    if args.prompt_version:
        overrides["prompt_version"] = args.prompt_version
    facts = _per_seat(args.facts)
    option_facts = _per_seat(args.option_facts)
    black = build_player(
        args.black, args.seed,
        {**overrides, "facts": facts["black"], "option_facts": option_facts["black"]},
    )
    white = build_player(
        args.white, None if args.seed is None else args.seed + 1,
        {**overrides, "facts": facts["white"], "option_facts": option_facts["white"]},
    )
    if black.name == white.name:
        black.name, white.name = f"{black.name}#1", f"{white.name}#2"

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or RESULTS / f"audit-{stamp}.md"
    writer = AuditWriter(out, rules, {"black": black.describe(), "white": white.describe()})
    print(f"审计文档: {out}")

    from .match import play_match

    live = None
    if args.live:
        from .live import LiveBoard

        live = LiveBoard(
            rules,
            port=args.live_port,
            open_browser=not args.no_open,
            host=args.live_host,
            snapshot=args.live_file,
        )
        live.start_game(0, black, white)
        if live.url:
            print(f"live view: {live.url}")
        if live.snapshot:
            print(f"live file: {live.snapshot}")

    def on_move(record, game):
        writer.move(record, game)
        if live is not None:
            live.add_move(record, game)
        rejected = len(record.rejected)
        note = f" ({rejected} 次被拒)" if rejected else ""
        print(f"{record.ply:3d}. {record.stone.symbol} {record.move}{note}", flush=True)

    match = play_match(
        black,
        white,
        rules,
        on_move=on_move,
        on_turn=(live.set_thinking if live else None),
        rng=random.Random(args.seed),
    )
    writer.finish(match)
    if live is not None:
        live.finish(match)

    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(match.to_dict(), indent=2, ensure_ascii=False))

    print(f"\n{match.status.value} ({match.reason}) winner={match.winner_name()} plies={match.plies}")
    print(format_summary(summarize([match])))
    print(f"\n写入 {out}\n写入 {json_path}")

    for player in (black, white):
        player.close()
    if live is not None:
        _hold_live(args.live_hold)
        live.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
