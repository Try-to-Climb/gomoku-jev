# Recorded runs

Every file here was produced by a command in this repository against a live API.
Nothing is synthetic and nothing has been edited after the fact, with one
exception noted at the bottom.

The experiment numbers (E1–E16) refer to
[../docs/EXPERIMENTS.md](../docs/EXPERIMENTS.md), which explains what each one was
testing and why.

## Viewing a game

Any match record can be turned into a standalone page -- board, move list, every
raw reply, token cost, confidence, and the engine's verdict per move:

```bash
python3 -m gomoku.replay results/audit-optionfacts-threat.json --open
python3 -m gomoku.replay results/jev_vs_gptoss.json --list     # multi-game records
```

The page needs no server and no network. Use the slider or the arrow keys to step
through the game.

## Matches

| file | what it is |
| --- | --- |
| `jev_vs_bot.json` | jev (black) vs the built-in heuristic bot, 9x9. jev loses in 12 plies |
| `jev_vs_gptoss.json`, `jev_vs_gptoss_2.json` | E2: the first four games, jev vs `openai/gpt-oss-120b` |
| `audit-jev-vs-gptoss.{md,json}` | E3: the first move-by-move audit, 14 plies |
| `audit-p1-jev-vs-gptoss.{md,json}` | E5: same pairing under `p1-threat-ladder` |
| `audit-p2-g1-jev-black.{md,json}`, `audit-p2-g2-gptoss-black.{md,json}` | E5: the defence-first wording (`p2-defence-first`), both colours |
| `audit-live-demo.{md,json}`, `audit-live-demo2.{md,json}` | bare-condition games used while building the live view, 12 plies each |
| `audit-live-0929.{md,json}` | the 9-ply loss that E11 dissects; the source of `postmortem_audit-live-0929_ply6.json` |
| `audit-optionfacts-openfour.{md,json}` | E15: `--option-facts open_four`. Survives to ply 16, then loses on the layer the annotation could not see |
| `audit-optionfacts-threat.{md,json}` | **E16**: `--option-facts threat`, 48 plies, 14/14 defensive tasks converted. Read the caveats in FINDINGS.md before quoting this one |
| `salvaged-qwen3-game1.json` | 43 plies of jev (`--option-facts threat`) vs `Qwen/Qwen3-32B`, the only game against a third model. See the note below |

## Probes and ablations

| file | what it is |
| --- | --- |
| `jev_probe.json` | E1: connectivity plus the fixed positions, jev |
| `jev_probe_p0.json`, `jev_probe_p1.json` | E4: the same probes under each tactical wording |
| `gptoss_probe.json`, `gptoss_probe_p0.json`, `gptoss_probe_p1.json` | the same, for the comparison model |
| `ablation_jev.json`, `ablation_gptoss.json` | E4: prompt-wording ablation, repeated sampling |
| `ablation_jev_facts.json`, `..._facts2.json`, `..._facts3.json` | E10: fact injection, three rounds (`none` / `span` / `status` / `geometry`) |

## Diagnostics

| file | what it is |
| --- | --- |
| `perception_jev.json` | E6: fan-out -- the move question and scorable yes/no questions in one request |
| `vision_jev.json`, `vision_jev_v2.json` | E7: one judgement decomposed into its atomic steps, asked in both polarities |
| `vision_gptoss.json` | E9: the same battery, comparison model |
| `infer_jev.json`, `infer_gptoss.json` | E8/E9: board-free rule arithmetic, colour discrimination, counting |
| `postmortem_audit-live-0929_ply6.json`, `postmortem_ply6_*.json` | E11–E14: one losing move re-interrogated under each condition (`p3`, tactics moved into `state`, `ways`, `windows`, per-option facts) |
| `postmortem_ply15*.json` | E15: the move where the `open_four` annotation degenerated to one identical sentence for all 30 options, and the same move after the fix |

## Notes

- Records carry `facts`, `option_facts` and `prompt_version` per player. Runs made
  under different settings are not comparable and are never pooled; the numbers in
  the docs are always reported per configuration.
- `audit-*.json` also contains the **full prompt text** of each player's first
  move, which is why those files are large.
- The `audit-*.md` documents were written by an earlier version of
  `gomoku.audit`, whose output language was Chinese. They are primary records, so
  they have been left exactly as produced rather than retranslated. The `.json`
  sibling of each is the language-neutral record, and `gomoku.replay` renders it
  in English.
- `salvaged-qwen3-game1.json` is the one file not written directly by a tool: the
  run was interrupted, so it is the live view's own state snapshot recovered from
  the server. It is in the page-state shape rather than the match-record shape,
  which `gomoku.replay` happens to accept. It carries no final verdict.
- One file is deliberately absent: `live.html`, the snapshot `--live-file`
  produces, is regenerated on every run and is git-ignored.
