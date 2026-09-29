# Prompt templates

Every word sent to a model lives here. No prompt text is hard-coded in Python, so
changing a prompt means changing a file.

A prompt has four parts:

| part | what it is | where | sharing |
| --- | --- | --- | --- |
| 1 rules | how gomoku works, how coordinates are written | `shared/rules.txt` | shared |
| 2 tactics | threat vocabulary and priorities -- **the experimental axis** | `shared/tactics/<version>.txt` | shared by default, overridable per backend |
| 3 board state | whose turn, the grid, both stone lists, the move list | `shared/state.txt` | content must be identical; the **format** may be overridden per backend |
| 4 backend glue | the genuinely API-specific bits: layout and answer format | `<backend>/*.txt` | separate by nature, kept as small as possible |

## Lookup

`<backend>/<filename>` if it exists, otherwise `shared/<filename>`.

So giving one model its own part 2 or part 3 needs **no code change** -- drop a
file with the same name into that backend's directory:

```bash
# a different state format for jev only (say, rows numbered top-down)
cp shared/state.txt jev/state.txt && $EDITOR jev/state.txt

# a tactical wording only jev sees
mkdir -p jev/tactics && cp shared/tactics/p1-threat-ladder.txt jev/tactics/p1-jev-only.txt
```

Nothing is overridden by default. A test
(`test_no_backend_silently_shadows_a_shared_part`) **fails** as soon as any
backend directory contains a file with the same name as one in `shared/`, because
at that moment the backends stop being measured with the same ruler. If you mean
to do it, add the pair to `ALLOWED_OVERRIDES` in that test and say so wherever you
report the numbers.

## Files

```
shared/rules.txt                     part 1
shared/tactics/p0-one-move.txt       part 2: the control (one move of lookahead)
shared/tactics/p1-threat-ladder.txt  part 2: the current default (open three/four + defence)
shared/tactics/p3-open-three-alarm.txt  part 2: trigger on a visible shape, not an inference
shared/state.txt                     part 3
shared/facts.txt                     optional addition to part 3: engine-computed line facts
                                     (none / span / status / geometry / threats)
shared/options.txt                   candidate-point wording, optionally carrying the
                                     consequence of each option (--option-facts)
jev/instructions.txt                 part 4: the instructions of jev's choice question
jev/tactics/p2-defence-first.txt     part 2, jev only: defence-first ladder
openai/system.txt                    part 4: the system prompt skeleton for choice mode
openai/answer_full.txt               part 4: goes into system -- choice + distribution + confidence
openai/answer_terse.txt              part 4: goes into system -- choice only, no step-by-step
                                     thinking (used for the retry after a truncated reply)
openai/user.txt                      part 4: the user turn (state + the option list)
openai/system_free.txt               part 4: free mode (no menu, a bare coordinate)
```

jev's `state` field is part 1 + part 3, assembled in code; free mode's user turn
*is* part 3. Neither needs a file of its own.

## Placeholders

| file | available placeholders |
| --- | --- |
| `shared/rules.txt` | `$rules` `$size` `$first_col` `$last_col` `$example` `$centre` |
| `shared/tactics/*.txt` | `$n` how many in a row wins, `$n1` = n-1, `$n2` = n-2 |
| `shared/state.txt` | `$move_no` `$colour` `$symbol` `$opponent` `$opponent_symbol` `$board` `$stones` `$history` `$facts` `$extra` |
| `shared/facts.txt` | the wording of the fact block, split into `[section]`s (below) |
| `shared/options.txt` | candidate wording, split into `[section]`s; `$col` `$row` everywhere, some sections also `$n` `$points` `$ways` `$best` |
| `jev/instructions.txt` | `$colour` `$symbol` `$tactics` |
| `openai/system.txt` | `$colour` `$symbol` `$rules_block` `$tactics_block` `$answer` |
| `openai/user.txt` | `$state` `$options` |
| `openai/system_free.txt` | `$colour` `$symbol` `$rules_block` `$tactics_block` |
| `openai/answer_full.txt`, `openai/answer_terse.txt` | none |

The syntax is `string.Template`'s `$name`, **not** `{name}`, so the JSON examples
inside these prompts need no brace escaping. An unknown name raises and names the
file. Write `${name}` when a letter or digit follows immediately, `$$` for a
literal `$`. The file's final newline is dropped, so a template that must end in a
blank line ends with two.

## Common commands

```bash
# add a tactical wording and compare it against the current one
cp shared/tactics/p1-threat-ladder.txt shared/tactics/p2-my-idea.txt
python3 -m gomoku.ablate --backend openai --versions p1-threat-ladder,p2-my-idea --repeats 3
python3 -m gomoku.cli --black jev --white openai --prompt-version p2-my-idea --size 9

# keep a whole template set outside the repository
cp -r gomoku/templates ~/my_prompts
python3 -m gomoku.probe --backend openai --templates ~/my_prompts   # or $GOMOKU_TEMPLATES
```

The tactics file name *is* the version, and it is recorded as `prompt_version` in
every match log, so runs made under different wordings never get averaged
together.

## Fact injection (`shared/facts.txt`)

Part 3 can carry a block of facts the engine worked out, switched on with
`--facts span|geometry|status|threats` (default `none`). The file is split into
`[section]`s, each an independently editable wording:

| section | purpose | placeholders |
| --- | --- | --- |
| `block` | heading for the whole block | `$lines` |
| `threats` | heading for the threat section | `$threats` |
| `row_span` | one line per run, `span` level | `$colour` `$coords` `$orientation` `$span` `$n_ends` |
| `row_geometry` | one line per run, `geometry` level | `$colour` `$coords` `$orientation` `$span` `$ends` |
| `row_status` | one line per run, `status`/`threats` levels | `$colour` `$coords` `$orientation` `$status` `$reason` |
| `status_live` / `status_dead` | the live/dead label | none |
| `reason_live` / `reason_dead` | why it is live or dead | `$span` `$n` `$ends` `$n_ends` |
| `threat_five` / `threat_open_four` | threat lines | `$colour` `$points` `$n` |
| `threat_none` / `no_lines` | fallback lines | none |

**Hard rule: facts only.** No ranking of candidate points, no recommendation, no
"best" or "should". The moment the text points at a particular move, the
experiment stops measuring the model and starts measuring this file. The level is
recorded in each match record's `facts` field, and injected runs are never pooled
with uninjected ones.

## Per-option annotations (`shared/options.txt`)

`--option-facts` writes the *consequence* of each candidate point into jev's
`criteria`, simulated point by point by the engine. It is a fact about the option,
not a recommendation -- several options may be safe, and the wording never ranks
them.

| level | sections | what each option says |
| --- | --- | --- |
| `threat` (**recommended**) | `threat_five` / `threat_open_four` / `threat_none` | the most urgent layer: can the opponent complete five at once, else make an unstoppable open four, else neither |
| `full` | adds `full_win` | as `threat`, plus "this move wins now" on top, so a move that finishes the game does not read like a quiet one |
| `open_four` | `opponent_open_four_yes` / `_no` | the open-four layer only. **Goes silent as a whole once the opponent already has a four** (every option then says "cannot make an open four"), see EXPERIMENTS.md E15 |
| `ways` | `ways` | how many ways to five the opponent has left: a continuous quantity, one sentence, one number |
| `windows` | `windows` | the same number in a more complex sentence -- measurably ignored, kept as the negative control |
| `none` | `plain` | the coordinate restated, nothing more |

Note that in a defensive position this is a **strong** hint: often only the
blocking points are safe, which is close to writing down the answer. Its
experimental value was in testing whether the `criteria` text is read at all --
every earlier injection went into `state`. It is (EXPERIMENTS.md E13).

A self-check worth keeping: count the *distinct* annotation strings for a move. If
there is only one, that move was effectively not annotated -- and that degenerate
case happens exactly when the threat is highest.
