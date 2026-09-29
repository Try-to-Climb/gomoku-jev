# Design

How the harness is put together, and why each piece is shaped the way it is. The
README covers usage; this covers the parts that affect whether a measurement means
anything.

## The separation that matters

The engine knows nothing about models, and the models cannot touch the engine.

`Game` is the single source of truth about a position. Players never receive it --
they receive a frozen `GameView` whose board is a private copy, and they return a
coordinate. The referee owns legality, retries and the record. So a misbehaving
player can produce bad moves but cannot corrupt the game, and every backend is
scored by identical code.

`gomoku/rules.py::judge_placement` is the only function that decides win, loss or
draw. There is no second implementation anywhere, including in the analysis code.

## Rules

Free-style gomoku by default. Every dial lives in `RuleSet`:

| dial | default | meaning |
| --- | --- | --- |
| `size` | 15 | board edge, 5–26 (column letters A–Z) |
| `win_length` | 5 | how many in a row wins |
| `overline` | `win` | what a run longer than `win_length` means: `win` also wins (free-style) / `ignore` only an exact five wins and a six is inert (standard) / `forbidden` making one loses on the spot (renju; by default restricted to black, and the unrestricted side still wins with six) |
| `opening` | `free` | `center_first` black's first stone must be the centre; `pro` centre first, then black's second stone at least three intersections away |
| `max_retries` | 2 | extra attempts granted after an unusable answer, with the referee's reason fed back |
| `illegal_move` | `forfeit` | after retries run out: `forfeit` loses, or `random_fallback` has the referee play a random legal move and record that it did |
| `max_plies` | `None` | ply cap, drawn when reached -- a safety net for slow models |

Decisions:

- black first, alternating, stones are never moved or captured
- any of the four axes reaching `win_length` wins; a gap breaks the run
- a full board with no line is a draw (`board_full`)
- illegal-move reasons are stable strings used directly in logs and metrics:
  `occupied`, `out_of_bounds`, `opening_center_required`, `opening_pro_distance`,
  `game_over`

## Coordinates

`H8` notation: columns are letters from the left (**`I` is not skipped**), rows are
numbers from the bottom. The centre of a 15x15 board is `H8`. Internally a move is
a 0-indexed `(row, col)` with `row=0` at the top; `gomoku/notation.py` converts.

`parse_move` is deliberately forgiving, because the point is to score how well a
model *plays*, not how well it follows a serialisation spec. Compliance is
reported separately as `parse_failure_rate` instead of being punished silently.

It accepts:

- a bare coordinate: `H8`, `h8.`, `I play H8` (the **last** coordinate wins, since
  models habitually enumerate options before stating an answer)
- JSON: `{"move": "H8"}`, `{"row": 8, "col": 8}`, `{"move": [8, "H"]}`
- prose: `row 8, column 8`, `(8, 8)`
- after stripping ```` ```json ```` fences and `<think>…</think>`, and applying
  NFKC normalisation so full-width `Ｈ８` also reads

## What the scorer knows

Every move is annotated with facts computed **before** the player was asked, which
is what turns "it lost" into "it lost here, for this reason". Two threat levels:

- **the five level** -- decided by a single point. `winning_moves()` enumerates
  points that end the game now.
- **the open-four level** -- one move earlier. `open_four_moves()` finds moves
  after which a side holds *two* completing points; the opponent can cover only
  one, so playing it wins two plies later. This is the layer on which every
  recorded jev game was actually decided, and the first version of these metrics
  could not see it at all.

`score_move` assigns exactly one verdict per move, in a strict order: finishing
beats blocking a five, which beats making an open four, which beats preventing
one. When the opponent has two or more winning points the position is already
lost and not blocking is **not** counted as a mistake (`unavoidable_loss`) -- a
harness that blames a player for a lost position measures nothing.

One trap worth repeating, because it cost a game (EXPERIMENTS.md E15):
`open_four_moves()` returns `[]` when the side can already win, which is correct
for scoring and fatal for prompting. A function written for the scorer must not be
reused to generate hints without checking that its shortcuts still hold.

### Metrics

| name | definition |
| --- | --- |
| `win_conversion` | of positions where an immediate win existed, the share where it was played. The hardest number to explain away: it needs almost no playing strength |
| `block_rate` | of positions where the opponent had **exactly one** winning point, the share where it was covered |
| `open_four_conversion` (`of.make`) | of positions where an unstoppable open four was available, the share where it was made |
| `open_four_prevention` (`of.stop`) | of positions where the opponent could make one, the share where it was prevented -- judged by recomputing their options after the move, not by assuming the only defence is to occupy one of their points |
| `illegal_move_rate`, `parse_failure_rate` | denominator is **attempts**, not moves |
| `forfeits`, `referee_fallbacks` | gave no usable answer within `max_retries` |
| `win_rate`, `score`, `avg_latency_s`, `attempts_per_move` | the usual |

`play_series` alternates colours by default, so first-move advantage cancels
between games.

## The candidate menu

`gomoku/candidates.py` does one thing: turn a position into a bounded menu of
legal points. Shared by every backend that answers with a choice, so different
models always see the same menu.

```
1. legal   = all legal empty points
2. pool    = "near" -> empties within Chebyshev radius `radius` (default 2) of a stone
             "all"  -> every legal point (on an empty board "near" degrades to "all")
3. if len(pool) > max_candidates (default 30): keep the top N by
             attack_score(me) + attack_score(opponent)
4. include_tactical (on): add back every immediate win for either side, unconditionally
5. shuffle (on): randomise the order
```

Steps 4 and 5 are there to keep the measurement honest. Step 4 means a missed win
or a missed block is the model's decision and never an artefact of the shortlist.
Step 5 means an option's position in the list carries no ranking signal.

Measured cost on a 15x15 "must block I8" position (jev-1.13.0):

| strategy | options | result | confidence | input tok | output tok |
| --- | --- | --- | --- | --- | --- |
| `near` / 30 | 30 | I8 correct | 0.85 | 1626 | 267 |
| `all` / 225 | 218 | I8 correct | 0.72 | 5736 | 1852 |

**The one remaining source of measurement contamination** is step 3: when the cap
bites, which *non-tactical* points make the shortlist is decided by a local
heuristic. `--candidates all` removes it completely, which is entirely practical
on 9x9 (81 points).

## Prompts

Four parts, all as text files, no prompt string in Python. Lookup is "backend
first, then shared", and a test fails if a backend file shadows a shared one --
because at that moment the two players are no longer being measured with the same
ruler. Details and placeholder reference:
[`gomoku/templates/README.md`](../gomoku/templates/README.md).

The tactics file name *is* the version and is recorded as `prompt_version` in every
record, so runs under different wordings are never pooled.

## Fact injection changes the question

`--facts` and `--option-facts` write conclusions the engine computed into the
request. They exist because the diagnostics showed jev reasoning correctly from
facts *stated in words* while failing to derive them from the board, and the only
way to test that is to state them.

Once either is on, the proposition under test is no longer "can it play gomoku"
but "can it decide once the analysis is handed to it". Both settings are recorded
per player in every match record, and injected runs are never pooled with
uninjected ones. The hard rule for the wording is in the templates README: facts
only, no ranking, no recommendation.

## Testing

`python -m unittest discover -s gomoku/tests -t .` -- 253 tests, a few seconds,
no network.

Importing `gomoku.tests` replaces `requests` and `httpx` send paths with something
that raises, so a unit test cannot quietly spend tokens; `GOMOKU_ALLOW_NETWORK=1`
lifts it for a deliberately live test. Every model backend is exercised through a
fake client that records the prompts it was given, which is how the "both backends
receive byte-identical tactics" assertion is possible.

Live checks are separate, explicit commands: `gomoku.probe`, `gomoku.ablate`,
`gomoku.perceive`, `gomoku.vision`, `gomoku.infer`, `gomoku.postmortem`,
`gomoku.audit`.
