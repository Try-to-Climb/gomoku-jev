"""Prompt ablation over the fixed probe positions.

    python3 -m gomoku.ablate --backend jev --repeats 5
    python3 -m gomoku.ablate --backend openai --repeats 3 --only make_open_four,stop_open_four

Each cell of the table is one (prompt version, position) pair sampled ``repeats``
times, so a change in wording is judged on a hit rate rather than a single lucky
answer -- both backends drift between identical requests.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import sys
from collections import defaultdict

from . import backends
from .notation import to_notation
from .probe import scenarios
from .prompts import PROMPT_VERSION, tactics_versions

#: default output directory, relative to the working directory (never inside the
#: installed package, which may be read-only)
RESULTS = pathlib.Path("results")


def build_player(backend: str, version: str, args, facts: str = "none"):
    """One arm of the ablation: a backend pinned to one tactical wording.

    Goes through the registry, so a newly registered backend is ablatable with no
    change here. ``args.model`` empty means "that backend's default model".
    """
    spec = f"{backend}:{args.model}" if args.model else backend
    return backends.build_player(
        spec,
        seed=args.seed,
        overrides={
            "candidates": args.candidates,
            "max_candidates": args.max_candidates,
            "prompt_version": version,
            "facts": facts,
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare prompt versions on fixed positions.")
    ap.add_argument("--backend", choices=backends.names(models_only=True), default="jev")
    ap.add_argument("--model", default=None)
    ap.add_argument("--versions", default=None,
                    help="comma-separated prompt versions to compare "
                         "(default: every file in templates/tactics/)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--facts-levels", default="none",
                    help="comma-separated fact-injection levels to compare: "
                         "none,geometry,status,threats")
    ap.add_argument("--templates", default=None, metavar="DIR",
                    help="load prompt text from this directory instead of gomoku/templates")
    ap.add_argument("--only", default=None, help="comma-separated scenario ids")
    ap.add_argument("--candidates", choices=["near", "all"], default="near")
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    if args.templates:
        from .prompts import set_template_dir

        set_template_dir(args.templates)

    versions = (
        [v.strip() for v in args.versions.split(",") if v.strip()]
        if args.versions
        else tactics_versions()
    )
    wanted = {s.strip() for s in args.only.split(",")} if args.only else None
    cases = [s for s in scenarios() if s["expect"] and (wanted is None or s["id"] in wanted)]
    if not cases:
        raise SystemExit("no scenarios selected (only scored positions are usable here)")

    runs: list[dict] = []
    hits: dict[tuple[str, str], list[int]] = defaultdict(list)

    levels = [x.strip() for x in args.facts_levels.split(",") if x.strip()]
    arms = [(v, f) for v in versions for f in levels]
    for version, facts in arms:
        player = build_player(args.backend, version, args, facts)
        arm = f"{version}+facts={facts}"
        print(f"\n===== {args.backend} / {arm} =====", flush=True)
        for case in cases:
            view = case["game"].view()
            for rep in range(args.repeats):
                response = player.propose(view)
                chosen = to_notation(response.move, view.size) if response.move else None
                ok = bool(chosen and chosen in case["expect"])
                hits[(arm, case["id"])].append(int(ok))
                runs.append(
                    {
                        "backend": args.backend,
                        "version": version,
                        "facts": facts,
                        "case": case["id"],
                        "repeat": rep,
                        "expect": case["expect"],
                        "chose": chosen,
                        "ok": ok,
                        "error": response.error,
                        "confidence": response.meta.get("confidence"),
                        "latency_s": round(response.latency_s, 3),
                        "usage": response.meta.get("usage"),
                    }
                )
                mark = "✓" if ok else "✗"
                print(
                    f"  {case['id']:18} rep{rep} {mark} {chosen or response.error} "
                    f"(期望 {'/'.join(case['expect'])}, conf {response.meta.get('confidence')})",
                    flush=True,
                )
        player.close()

    width = max(len(c["id"]) for c in cases) + 2
    print(f"\n命中率 ({args.backend}, 每格 {args.repeats} 次)")
    arm_names = [f"{v}+facts={f}" for v, f in arms]
    header = f"{'position':{width}}" + "".join(f"{a[-22:]:>24}" for a in arm_names)
    print(header)
    print("-" * len(header))
    for case in cases:
        row = f"{case['id']:{width}}"
        for arm in arm_names:
            got = hits[(arm, case["id"])]
            row += f"{sum(got)}/{len(got)}".rjust(24)
        print(row)
    row = f"{'TOTAL':{width}}"
    for arm in arm_names:
        got = [x for case in cases for x in hits[(arm, case["id"])]]
        row += f"{sum(got)}/{len(got)}".rjust(24)
    print(row)

    out = args.out or RESULTS / f"ablation_{args.backend}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {
            "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "backend": args.backend,
            "default_version": PROMPT_VERSION,
            "repeats": args.repeats,
            "runs": runs,
        },
        indent=2,
        ensure_ascii=False,
    ))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
