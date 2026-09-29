# Experiment report: probing jev with gomoku

Subject: **jev-1.13.0** (TypeSafe System One). Raw data in `gomoku/results/`, prompts in
`gomoku/templates/`, reproduction commands at the end.

> This is the **process** report: method, evidence, lessons. For the conclusions see
> **[FINDINGS.md](FINDINGS.md)**. A control backend (a chat model) is used only to
> confirm whether a given gap is specific to jev; it is not profiled here.
>
> *(中文版: [EXPERIMENTS.zh.md](EXPERIMENTS.zh.md))*

---

## Glossary

**Shapes** (with $n=5$)

| Term | Meaning | Why it matters |
| --- | --- | --- |
| five | 5 stones in a line | wins immediately |
| four | 4 stones + one empty point that completes five | if the opponent has a four you must take that point, or you lose next turn |
| **open four** | a four with **two** different completing points | **cannot be blocked** — you can only cover one. Making one locks up the game |
| open three | 3 stones in a line with both ends empty | leave it alone and it becomes an open four next turn |
| **dead line** | the usable run of points is already shorter than 5 | adding stones to it is pointless, and so is blocking it |
| span | the longest stretch along a line that is on the board and free of opposing stones | span < 5 means dead; this is the only thing that decides live/dead |

**Fixed test positions** (case names in `probe.py` / `ablate.py`)

| Case | Position | The only correct answer |
| --- | --- | --- |
| `must_block` | the opponent already has a four | take its completing point |
| `must_win` | you already have a four | take the completing point and win |
| `make_open_four` | you have an open three; one move makes it an open four | take either end (offence — effectively decisive) |
| `stop_open_four` | **the opponent** has an open three, one move from an open four | take either end of their three (defence) |

**Injection levels** (engine-computed facts written into the request)

| Where | Level | What each injection says |
| --- | --- | --- |
| `state` (the position, one block) | `span` | each line's span and how many open ends, no coordinates |
| | `status` | plus a `LIVE`/`DEAD` verdict |
| | `geometry` | plus the coordinates of the open ends (in a defensive position ≈ giving the answer) |
| | `threats` | plus "the opponent can make an open four at X or Y next turn" |
| `criteria` (one sentence per candidate) | `ways` | how many ways to reach five the opponent has left after this move |
| | `windows` | the same number in a more complex sentence (negative control) |
| | `open_four` | whether the opponent can make an open four after this move (**blind at the five level**) |
| | `threat` | the most urgent level: five → open four → neither |

**Prompt versions** (the tactics block, in the `instructions` field)

`p0-one-move` only covers "make five" and "block their five" ·
`p1-threat-ladder` adds open four / open three definitions and a six-level priority list ·
`p2-defence-first` defence first (jev only) ·
`p3-open-three-alarm` replaces the inferential trigger with a visible shape

**Metrics**

| Name | Meaning |
| --- | --- |
| **correct-answer mass** | the summed probability jev assigns to the correct point(s) out of its 30-candidate distribution. Far more sensitive than right/wrong: 0.01 = never entered view, 0.35 = barely picked, 0.97 = decisive |
| rank | where the correct answer sits in that distribution (out of 30) |
| `win_conversion` | share of immediate-win chances taken |
| `block_rate` / `of.stop` | share of opposing fours blocked / opposing open fours prevented |
| `of.make` | share of own open-four chances converted |
| `unavoidable_loss` | the opponent already has two completing points; no single move saves it |

---

## The investigation route (read this first)

The whole thing is a chain of eliminations. Each step is the question forced by the previous
answer:

```
With no injection jev lost 11 games in a row. Format compliance was flawless; it lost on play
   ↓  Move-by-move audit: at the moment of loss the opponent already had an open four
      (unblockable), so the real mistake happened earlier (E1–E5)
Hypothesis 1: it does not know the tactics → write open four / open three definitions and a
      priority list into the prompt, in three versions
   ↓  No effect whatsoever; not one number moved → eliminated (E4, E5)
Hypothesis 2: it cannot see the opponent's threat → just ask it "can this line still reach five"
   ↓  Already-blocked lines: 0/5, all answered "yes" — looks like genuine blindness (E6)
   ↓  But it might simply answer "yes" to that shape of question → decompose the judgement into
      a chain, and cross-check the conclusion with a positive and a negated phrasing
   ↓  Reading a point, reading each end, counting stones, measuring the span, AND/OR — all
      perfect. Only the final conclusion is wrong (E7)
Hypothesis 3: it cannot reason → ask the same fact in pure words, with no board at all
   ↓  10/10 correct at 0.95 confidence → eliminated (E8)
Located: it can read, compute and reason; the one thing it will not do is derive a conclusion
      from the board itself
   ↓  So change tack: stop teaching it to derive, and hand it the conclusion instead
Intervention 1: write the conclusion into the position description (state) → 0/10 → 10/10 on
      fixed positions (E10)
   ↓  The same move fails on a real game position: several lines read almost identically, so the
      facts carry no discriminating power (E11)
Intervention 2: state the rule more plainly, and move it to another field → 0/9, still nothing (E12)
Intervention 3: write the consequence onto each candidate option (criteria) → works, and reveals
      that sentence structure matters more than content (E13, E14)
   ↓  Live test: it works, but my annotation missed the highest threat level, so the game was
      still lost; fixed (E15)
   ↓  Rematch, 48 plies, 14 out of 14 defensive tasks met, first game not lost (E16)
```

How to read: for conclusions only, see [FINDINGS.md](FINDINGS.md). To see how a given
conclusion was established, follow the chain above to the matching E number.

---

## Setup

9×9 (some fixed positions 15×15), five in a row, free-style. Each move is one `choice`
question whose `criteria` are 30 candidate points (empty points within radius 2 of a stone,
with both sides' immediate winning points always included, order shuffled). jev returns a
choice plus a probability distribution and a confidence. About 1.7k input / 250 output tokens
and 0.6 s per move.

---

## Experiments

### E1–E5 Baseline behaviour with no injection (no new findings; condensed)

Five experiments (connectivity probe, 4 games, move-by-move audit, prompt ablation p0→p1, and
a defence-first version p2) all landed on the same picture:

- **Format compliance is flawless**: zero illegal moves, zero parse failures, one call per move.
- **The two easy levels are reliably correct**: `must_block` 5/5, `must_win` 5/5.
- **The open-four level is uniformly wrong**: `make_open_four` 0/5, `stop_open_four` 0/5, and
  five repeats produce the **same** wrong move (confidence 0.6–0.85). "Try again" does not help.
- **Tactical wording has no effect**: across p0 → p1 → p2 those numbers did not change at all
  (10/20 → 10/20).
- Every game lost. The audit shows the reason is **always** `unavoidable_loss` — by the time it
  lost, the opponent already had an open four, which means the real error happened earlier and
  the metrics of the time (which only covered the five level) could not see it.

The blind spot E3 exposed was later fixed by adding open-four metrics (`of.make` / `of.stop`).
The only visible change from p2 was that jev stopped adding stones to its own dead lines and
started playing *next to* the opponent's line — but on a parallel point rather than on its open
end, i.e. a new way of losing.

### E6 What does it actually see? The first direct question

**Why this experiment** The previous five had eliminated "does not know the tactics". The
remaining suspicion was that it simply never saw the opponent's threat. But a match record only
tells us which move it made, not what its picture of the board was.

**How to ask** jev's API allows **many questions in one request** (its docs call this fan-out).
So alongside "which point do you play", we slip in a batch of yes/no questions whose correct
answers the engine already knows. Same position, same request: we get its choice and its
"perception" and can compare them directly.

**The key question: dead lines**

If both ends of a line are blocked by the opponent, the remaining space cannot hold five stones,
so it can never win — that is a **dead line**. Adding to one, or spending a move blocking the
opponent's, is pure waste. So "can this line still reach five" is the question that best
separates "saw it" from "did not".

A **live line** (both ends still open, genuinely able to reach five) must be asked in the same
breath as a control. Otherwise, if it answers "yes" to every question of that shape, we cannot
tell "it cannot spot a dead line" from "it just says yes".

**Results** (6 positions combined; the correct answer is in brackets)

| What was asked | Correct |
| --- | --- |
| Can this **live** line still reach five? (should be "yes") | **8/8** ✓ |
| Can this **dead** line still reach five? (should be "no") | **0/5** ✗ |
| Can I complete five with this move? | 5/6 |
| Does the opponent already have a four (so I must block)? | 5/6 |
| Can I / the opponent make an open four in one move? | 3/5 · 2/5 |
| Was the move actually played the objectively best one? | 2/6 |

**How to read this**

The first two rows are the same question with opposite correct answers, and it answered "yes"
both times — **live perfect, dead all wrong**. More telling is the probability: the five dead
cases came back at 0.63–0.85, the eight live ones at 0.61–0.91 — **the two ranges overlap
completely**. There is no signal in its answers that separates live from dead.

The second-to-last row (open four 3/5 and 2/5) echoes the old problem from E1–E5: that level was
never reliable. The last row confirms the move quality really is poor: 2 of 6 positions with a
unique correct answer.

**The ambiguity this leaves** All the dead-line questions have "no" as the correct answer and all
the live-line questions have "yes", while it answered "yes" throughout. So "it cannot spot a dead
line" and "it says yes to this phrasing" both fit — which is exactly what E7 was built to settle.

### E7 Decomposing the chain: it can read, count and combine, but not conclude

**Why this experiment** The ambiguity from E6 has to go first: is it really blind to dead lines,
or does it just answer "yes" to that phrasing?

**How** Take the **single** judgement "can this line still reach five" and break it into every
small step a person would go through, asking all of them about the **same line** in one request.
If it is right up to some step and wrong after it, the break is located exactly there.

Additionally, ask the final conclusion in a **positive and a negated** phrasing ("can it still
reach five" and "is it now impossible"). Answering "yes" to both is pure acquiescence and has
nothing to do with understanding — that is the control for E6's ambiguity.

5 positions (both ends blocked / one end blocked / both ends open / a two-stone dead line / a
dead line against the edge) × 2 repeats:

| Class | Question | Result | Probability |
| --- | --- | --- | --- |
| atomic | is `D5` a white stone | 10/10 | 0.99 |
| atomic | is the **left** end blocked | **10/10** | 0.99 |
| atomic | is the **right** end blocked | **10/10** | 0.99 |
| atomic | how long is the usable span (≤2/3/4/≥5) | **10/10** | — |
| atomic | is there a four anywhere | 10/10 | 0.99 |
| combined | are **both** ends white (**AND**) | **10/10** | — |
| combined | is at least one end white (**OR**) | **10/10** | — |
| combined | are both ends **empty** (AND + polarity flip) | 7/10 | 0.38–0.56 |
| **conclusion** | can it still reach five | **4/10** | 0.50–0.79 |
| **conclusion** | is it now impossible | 7/10 | 0.12–0.48 |

AND and OR are both perfect, so **conjunction is not the problem**. The striking part is that it
measured the span correctly (including 4 for the edge case and "≤2" for the two-stone case) and
then failed at "4 < 5, therefore it cannot reach five". Positive/negated contradictions occurred
only 2/10, so it is not acquiescence.

The first version of this experiment only asked "is that one end white" and "are both ends
empty"; the latter scored 8/10, which made it look like "roughly sees both ends".
**Splitting the two ends into separate questions revealed that each end alone is 10/10 and the
problem lies elsewhere.**

### E8 Isolating the break: fact vs counterfactual

**Why this experiment** E7 localised the break to "has all the facts, cannot reach the
conclusion". So is the problem **the reasoning itself**, or **getting the facts off the board**?
Three groups pin down one possibility each:

① **Pure rule arithmetic**: no board at all; describe the situation in a sentence ("a player has
four in a line, both ends taken by the opponent") and ask the same question. If this fails too,
the reasoning itself is broken.
② **Three-colour discrimination**: ask about one named point three ways — "is it white / is it
empty / is it black". This tests a specific guess: maybe it cannot tell "empty" from "occupied".
③ **The conclusion as a count**: instead of "can it still reach five" (yes/no), ask "how many
more stones can still be added" (a number), in case the phrasing is what breaks it.

| Group | Result | Probability |
| --- | --- | --- |
| Abstract rules (no board, 5 questions) | **10/10** | 0.95–0.97 / 0.07–0.14 |
| Three-colour discrimination (54 questions) | **54/54** | 0.98–1.00 / 0.01 |
| "How many more stones can be added" | **1/10** | 0.24–0.66 |

The decisive contrast — the same fact in two representations, opposite answers:

| Representation | Question | Answer |
| --- | --- | --- |
| **Words** | a player has four in a line, both ends taken — can they ever make five? | **No, p=0.13** ✓ |
| **Board** | same position: can black's E5 F5 G5 H5 still reach five? | **Yes, p=0.61** ✗ |

It also contradicts itself: for the same line it answered "≤2" for the span including its own
stones (correct) and "3 or more" for how many more could be added (wrong).

**Hypotheses eliminated** (all by experiment, not by argument): cannot see the board ✗ (reads
points at 0.99) · cannot tell empty from occupied ✗ (54/54) · cannot do conjunction ✗ (AND/OR
10/10 each) · does not know the rules ✗ (abstract 10/10) · merely acquiescing ✗ (contradictions
only 2/10) · the tactical wording was not good enough ✗ (three versions, no change).

### E9 Is the gap specific to jev? (one line)

**Why this experiment** Maybe these questions are simply hard and no model does well. Translating
the same battery to another backend answers that.

Same questions, same positions, one request each: **factual class 124/124 for both, pure-word
rule reasoning 10/10 for both, deriving counterfactuals from the board jev 19/40 versus the
control's 40/40.** So the gap is not in the questions; it is in that one capability of jev's.

### E10 Fact injection (fixed positions)

**Why this experiment** E8 proved it reasons correctly from **premises given in words** and only
fails to extract those premises from the board itself. So stop teaching it to derive — write the
engine's own conclusions in as text.

**Four graded levels** exist to find out *which layer of information is actually required*:
numbers only → plus a live/dead verdict → plus coordinates. The grading has a second purpose: if
numbers alone suffice, then what it lacked really was just "turn the number into a conclusion".

Five repeats per cell:

| Position | `none` | `span` | `status` | `geometry` |
| --- | --- | --- | --- | --- |
| `make_open_four` (offence) | 0/5 | 0/5 | **5/5** | 5/5 |
| `stop_open_four` (defence) | 0/5 | **5/5** | 5/5 | 5/5 |

**Injection works, and it is not answer-copying**: the `status` level reaches 10/10 while its text
contains **no coordinates at all**.

**My pre-registered prediction was wrong.** I predicted geometric facts would be useless (because
E7 showed it can compute spans itself); in fact `span` alone lifted the defensive case from 0/5 to
5/5. So it **can** compute the span but **will not** compute it while choosing — anything not
written in the text does not enter the decision.

Two self-inflicted leaks were found and fixed along the way (each found first, then fixed, then
re-run): the `geometry` level wrote the open-end coordinates into the text, and in a defensive
position those coordinates *are* the answer; the `status` level's reason clause carried them too.
That is why the coordinate-free `span` level exists.

### E11 Post-mortem: why that move did not block

**Why this experiment** E10 succeeded on constructed positions. But I chose those positions, and
they may be easier than real ones. So redo it on **the actual losing move from a real game**.

**How** `postmortem.py` takes the position before any recorded move and asks three kinds of
question using **the same candidate menu that move was given** (important: a different menu would
be a different problem): what did it see, can it point at the right square, and how would it
choose under each injection level.

Sample: move 6 of a real game. The opponent had a diagonal open three (both ends empty); jev
played an irrelevant point and lost two moves later.

| Question | Truth | jev | Probability |
| --- | --- | --- | --- |
| does the opponent have a three | yes | yes ✓✓ | 0.60 / 0.63 |
| can that line still reach five | yes | yes ✓✓ | 0.66 / 0.64 |
| are both its ends empty | yes | yes ✓✓ | 0.65 / 0.63 |
| which point sits at an end of it | C7/G3 | **G3** ✓✓ | 0.42 / 0.38 |
| **can the opponent make two completing points next turn** | yes | **no** ✗✗ | **0.36 / 0.38** |
| **can an open four be blocked (pure rule)** | no | yes/no ✗✓ | **0.51 / 0.48** |
| **which point stops the opponent** | C7/G3 | **its own preferred move** ✗✗ | 0.35 |

The three, the liveness, both ends empty, and the end coordinate: all correct. The break is at
"a three with both ends open ⇒ they are unstoppable next turn", plus the rule "an open four
cannot be blocked" itself (a coin flip). Asked directly which point stops the opponent, it names
the move it wanted to play anyway.

Injection A/B on that same move: `none` 0.01 → `span` 0.15 → `status` 0.09 →
**`threats` 0.97** (and the move becomes correct).

**This corrects E10**: there, `span` was enough; here `span` and `status` both fail. Because in
this position the two lines read almost identically (both LIVE, both 2 open ends, span 8 vs 9) —
**the fact block provides no discriminating power**. E10's position had only one live line, so the
numbers were unambiguous.

Its scoring also follows "how close to my own stones": the 3 points with probability ≥0.13 average
distance 1.00 to its own nearest stone, the 18 with <0.02 average 2.17, and the two correct
answers sit 2–3 away with 0.00 and 0.01.

### E12 Writing "an open three is fatal" into the tactics, and swapping fields

**Why this experiment** E11 made me re-read the prompt, where I found that the defensive rule in
p1 **triggers on the wrong thing**: it reads "if the opponent could make an open four next turn,
break that line" — and "could they make an open four next turn" is exactly the inference it
answers at 0.36. The action was right; the condition could never fire.

So the trigger was replaced with something it **can see** (a three with both ends empty, which it
answers at 0.60–0.66). New version `p3-open-three-alarm`, plus a second arm that moves the same
text from `instructions` into `state`.

| Condition | Correct | Correct-answer mass |
| --- | --- | --- |
| p1, tactics in instructions | 0/5 | 0.01 |
| **p3, tactics in instructions** | **0/6** | 0.02 |
| **p3, tactics in state** | **0/3** | 0.02 |
| p1 + `threats` injected into `state` | 2/2 | **0.96** |

**Conclusion** Writing "an open three is fatal" into the tactics **does not work** (0/9 across
both fields), and **it is not a field-placement issue** — the same text scores 0.02 in
`instructions` and 0.02 in `state`. Meanwhile the same requests still answer "there is a three",
"both ends are empty" and "which point is the end" correctly: **it sees the shape, is told the
shape is lethal, is told which kind of point to take, and still does not change.**

The last row shows the only thing that works: one concrete statement about **this position** ("the
opponent can make an open four at C7 or G3"), which lifts the correct-answer mass from 0.01 to
0.96. Rules no; facts yes.

### E13 Putting the consequence on each candidate

**Why this experiment** E12 showed rule text is useless and only concrete statements about the
current position work. So change where it lands: instead of describing lines on the board,
describe "what happens if you play here" **per candidate**.

This also answers a question never tested: does jev read the `criteria` field at all? That field
holds each candidate's description; it had been a throwaway string (`Play at column D, row 7.`,
just restating the coordinate) while every injection so far went into `state`.

| Condition | Move | Correct-answer mass | Rank |
| --- | --- | --- | --- |
| `criteria` unannotated | ✗ ×3 | 0.00–0.01 | 14–30 |
| **`criteria` annotated with the consequence** | **✓ ×3** | **0.69–0.71** | **1 / 2** |

**`criteria` text is read and it works** — that field had been wasted. This settles the ranking of
the three channels: `instructions` (rules) ≈ useless < `criteria` (per-option facts) <
`state` (position facts).

Caveat: in this position only 2 of 30 candidates were marked safe, which effectively circles the
answer. So this can only be read as "`criteria` is read", not as "jev learned to defend".

### E14 Tightening the annotation to something that is not the answer — and the power of phrasing

**Why this experiment** E13's annotation marked only 2 points "safe" in a defensive position,
which is as good as circling the answer. To test whether it is **actually using the information**,
the annotation must not equal the answer — so switch to a continuous quantity it has to
**compare**, rather than a verdict it can read.

The quantity: "how many ways to reach five does the opponent have left after this move". The
gradient here is genuinely three-valued: blocking an end leaves 1, blocking inside the window
leaves 2, the other 27 points leave 3.

| Annotation | Sentence shape | Move | Correct-answer mass |
| --- | --- | --- | --- |
| `none` | — | ✗ ×3 | 0.01 |
| `windows` | two clauses, two numbers | **✗ ×3** | **0.02** |
| **`ways`** | **one sentence, one number** | **✓ ×3** | **0.33–0.38** |
| `open_four` | a verdict | ✓ ×3 | 0.68–0.70 |

The same number, two ways of writing it:

```
windows: "the opponent's best still-winnable line holds 3 of the 5 points it needs,
          and 1 line(s) on the board are that far along."      → 0.02 (as good as nothing)
ways:    "the opponent has 1 different ways left to reach 5 in a row."
                                                               → 0.35 (works)
```

**Continuous quantities do work** (my earlier "only verdicts work" in E12/E13 was too strong and
is corrected); **sentence structure can override content**, so per-option annotations must be one
sentence with one number; **a verdict is still strongest** (0.70 vs 0.35).

Had I stopped at the `windows` arm I would have concluded "it cannot compare numbers", which is
false — it took an extra arm that changed **only the phrasing** to rule that out.

### E15 A blind spot in the injection, exposed in a live game — and fixed

**Why this experiment** Everything so far tested single positions. Whether the injection helps
over a **whole game** can only be answered by playing one.

Playing with E13's `open_four` annotation: **it works at the level it covers.** The game lasted
16 plies (previously 9–12) and jev **blocked an open four for the first time** (2/2).
**But it lost at the level the annotation does not cover.** At move 15 the opponent already had a
diagonal four whose only completing point was B3, and the `criteria` actually sent were:

```
30 entries, 30 identical sentences:
"... After this move the opponent cannot create an unstoppable open four on their next turn."
```

Deduplicated, only 1 distinct string → that move effectively had no injection. The root cause is
the first line of `analysis.open_four_moves()`: `if winning_moves(...): return []` (my comment
read "already winning, the level below is moot"). That is correct for a **metric** and fatal as a
**prompt**: with the opponent already holding a four, the whole level goes silent and every option
is told "the opponent cannot make an open four" — literally true, entirely uninformative, while 29
of those points let the opponent finish next turn.

| Annotation | Move | Mass on B3 | Rank | Confidence |
| --- | --- | --- | --- | --- |
| `none` | ✗ ×3 | 0.00 | 23–30 | 0.29–0.32 |
| `open_four` (blind version) | ✗ ×3 | 0.00–0.01 | 22–30 | 0.23–0.26 |
| **`threat` (fixed version)** | **✓ ×3** | **0.79–0.81** | **1** | **0.78–0.82** |

B3 was at 0.00 with and without the annotation, so the old annotation did not *cause* the miss;
but with the fix the same move is blocked immediately. **Conclusion: jev missed that diagonal four
because nobody told it, not because it ignores being told.**

Fix: a new `--option-facts threat` that reports the most urgent level (five → open four → cannot
win within two turns). `open_four` was left untouched so E13/E14 data stay comparable, and a
regression test pins the blind spot down.

Two bugs in `postmortem.py` were fixed in passing: computing a run's end points mixed the
display-sorted coordinates with the run's original direction, so on an anti-diagonal the "ends"
landed inside the run (which made jev's correct answer score as wrong); and the scoring target
was hardcoded to open-four points, leaving it empty in five-level positions.

### E16 The live game after the fix: first game not lost

**Why this experiment** E15 fixed the coverage blind spot; a live game is needed to confirm
whether a third failure mode appears once both levels are covered.

`--option-facts threat` (jev only), 9×9, 48 plies.

| | No injection (3 games) | `open_four` (E15) | **`threat` (this game)** |
| --- | --- | --- | --- |
| Plies | 9 / 12 / 12 | 16 | **48** |
| Defensive chances → met | 0/1, 0/1, 0/1 | 2/2 open four, 0/1 five | **14/14** |
| Blocked a five | — | 0/1 | **6/6**, best conf 0.95 |
| Prevented an open four | 0% | 100% | **8/8** |
| Result | all lost | lost | **won (opponent forfeited)** |

**Conclusion** **14 of 14 defensive tasks met, zero errors** — the injection's effect is confirmed
over a long game, against 0/3 with no injection. This is the most direct result of the whole
intervention series.

**Two caveats that must be stated**: jev **never had a single attacking chance**
(`win_conversion` empty; it never built an immediate win), so the only thing it did was avoid
losing, and that came from the caller's engine computing threats per option — **a win by division
of labour, not by playing strength.** The opponent forfeited because its own output budget ran out
(three consecutive empty replies), and that ceiling was set by me, so this game **cannot** be used
as evidence about relative playing strength.

---

## Cumulative record (jev's view)

With no injection: **0 wins, 11 losses**, split evenly between playing first and second (the
first-move advantage made no difference); plus one loss to this project's 60-line heuristic bot.
With `--option-facts threat` it recorded its first non-loss (E16).

Throughout: **zero illegal moves, zero parse failures.** Format compliance was never an issue;
every loss was a loss on play.

---

## Methodological lessons

1. **Truncation disguises itself as a parse failure**: check `finish_reason`, not just whether
   parsing succeeded.
2. **A single sample proves nothing**: repeat every measurement.
3. **Compound questions manufacture false signals**: "are both ends empty" at 8/10 suggested
   "roughly sees both ends"; splitting it showed each end alone is 10/10. Diagnostics must be
   decomposed to atoms.
4. **Metrics have blind spots**: while only the five level was measured, the real cause of defeat
   (letting an open three become an open four) was invisible.
5. **Positive and negated phrasings** are a cheap way to rule out acquiescence.
6. **Keep the control arm in the code**: `p0-one-move` is retained as a version and can be re-run
   at any time.
7. **Never pool data across prompts or injection levels**: every record carries
   `prompt_version` / `facts` / `option_facts`.
8. **An intervention that works on constructed positions may fail on real ones** (E10 vs E11).
   Constructed positions often contain a single live line, so the information is discriminating by
   accident; in a real game several lines look alike and the same text stops working. Verify on
   positions extracted from real games.
9. **Questions with consequences get contaminated by the model's preference**: in the same request
   "which point is at the end" was right while "which point stops the opponent" fell back to the
   move it wanted. Ask geometry and consequence separately.
10. **Rules ≠ facts**: rules written into the prompt (any wording, any field) barely affect the
    choice; only concrete statements about this position / this option do.
11. **Sentence structure can override content**: the same number was ignored in two clauses (0.02)
    and worked in one sentence (0.35).
12. **Rule out phrasing before accepting a negative result**: any conclusion of the form "the model
    cannot do X" must first be retried with different wording.
13. **An injection must cover every threat level**: an annotation covering one level degenerates
    into "the same sentence on every option" as soon as the threat escalates. Self-check: count the
    distinct annotation strings; if it is 1, that move has no injection.
14. **A function written for scoring must not be reused for prompting**: `open_four_moves`
    returning an empty list when a win is already available is correct for a metric and fatal for
    an annotation.

---

## Appendix: key raw evidence

All taken verbatim from `gomoku/results/`.

### A. The losing move with no injection (E11, move 6)

The opponent had the diagonal open three D6 E5 F4 with both ends (C7/G3) empty. jev's full reply:

```json
{"type": "choice", "choice": "F5", "confidence": 0.27,
 "probabilities": {"F5": 0.31, "E4": 0.25, "D4": 0.13, "F3": 0.04, "F6": 0.04,
                   "C4": 0.03, "E7": 0.03, "F7": 0.02, "G4": 0.02, "G5": 0.02,
                   "C5": 0.02, "D7": 0.02, "E3": 0.01, "G6": 0.01, "B5": 0.01,
                   "C6": 0.01, "E8": 0.01, "G7": 0.01, "G3": 0.01,
                   "C7": 0.0, "F8": 0.0, "G8": 0.0, "C8": 0.0, "B6": 0.0,
                   "B7": 0.0, "D8": 0.0, "H6": 0.0, "B8": 0.0, "B4": 0.0, "H5": 0.0}}
```

The two correct answers: `C7`=0.00, `G3`=0.01. The top three all sit adjacent to its own stones.

### B. The annotation degenerating into one sentence (E15, move 15)

The opponent already had a diagonal four whose only completing point was B3. Three of the
`criteria` actually sent (the other 27 are identical):

```json
"B3": "Play at column B, row 3. After this move the opponent cannot create an unstoppable open four on their next turn."
"G6": "Play at column G, row 6. After this move the opponent cannot create an unstoppable open four on their next turn."
"H7": "Play at column H, row 7. After this move the opponent cannot create an unstoppable open four on their next turn."
```

jev played G6; B3 had probability 0.00 and rank 30/30.

### C. The same move after the fix (E15, `--option-facts threat`)

```json
{"choice": "B3", "confidence": 0.82,
 "probabilities": {"B3": 0.81, "C3": 0.02, "B4": 0.02, "C7": 0.01, "B5": 0.01, ...}}
```

Same position, same menu; only the `criteria` wording changed: 0.00 → 0.81.

### D. Holding every fact, unable to conclude (E7)

Black E5 F5, with white on both ends (D5 and G5). Four answers from one request:

```
is the left end white   truth=yes   answer=yes   p=0.99   ← perfectly clear
is the right end white  truth=yes   answer=yes   p=0.98   ← equally clear
how long is the span    truth=≤2    answer=≤2    correct  ← the number that decides it
can it still reach five truth=no    answer=yes   p=0.81   ← conclusion wrong
```

### E. The contradiction between words and board (E8)

```
asked in words: "a player has four in a line, both ends taken — can they ever make five?"  → no,  p=0.13  ✓
asked on the board: same position, "can black's E5 F5 G5 H5 still reach five?"             → yes, p=0.61  ✗
```

---

## Reproduction

```bash
# unit tests (203, fully offline; importing the test package blocks HTTP, so any
# test that tries to call an API fails loudly)
python3 -m unittest discover -s gomoku/tests -t .

# connectivity + the 5 fixed positions
python3 -m gomoku.probe

# prompt ablation (control arm: --prompt-version p0-one-move)
python3 -m gomoku.ablate --repeats 5

# fact-injection ablation on fixed positions
python3 -m gomoku.ablate --facts-levels none,span,status --repeats 5 \
  --only make_open_four,stop_open_four

# the three diagnostics
python3 -m gomoku.perceive                 # fan-out perception + the move
python3 -m gomoku.vision --repeats 2        # the decomposed chain (with negated phrasings)
python3 -m gomoku.infer  --repeats 2        # abstract rules / colours / the count form

# post-mortem of one recorded move (perception + A/B across injection levels)
python3 -m gomoku.postmortem --game gomoku/results/audit-live-0929.json --ply 6 \
  --option-facts none,ways,open_four,threat --repeats 3

# a live game with a move-by-move audit and a browser view (file mode needs no network)
python3 -m gomoku.audit --black jev --white openai --size 9 \
  --option-facts black=threat \
  --live --live-file gomoku/results/live.html --live-hold 3600 --no-open
```

## Data files

| File | Contents |
| --- | --- |
| `jev_probe_p{0,1}.json` | E1 / E4 probes |
| `jev_vs_gptoss{,_2}.json` | E2, the first 4 games |
| `audit-*.{md,json}` | E3/E5/E15/E16 move-by-move audits (`.json` holds the full prompt per move) |
| `ablation_jev.json`, `ablation_jev_facts{,2,3}.json` | E4 prompt ablation, E10's three injection rounds |
| `perception_jev.json` | E6 |
| `vision_jev{,_v2}.json` | E7 |
| `infer_jev.json` | E8 |
| `postmortem_*.json` | E11–E15, each arm |
