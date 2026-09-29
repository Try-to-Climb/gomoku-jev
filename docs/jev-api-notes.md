# Notes on the jev API

What this project had to work out in order to use
[jev](https://docs.typesafe.ai) (TypeSafe System One) as a player, and which of
those things are documented versus inferred. Written down because the distinction
matters: code that thresholds on an inferred formula is code that breaks when the
vendor changes something they never promised.

Measured against **jev-1.13.0**, September 2026.

## The contract

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer $TYPESAFE_API_KEY

{
  "state":     "<everything the engine should know, as text>",
  "model":     "jev-latest",
  "questions": { "<your id>": { "type": ..., "instructions": ..., ... } }
}

-> { "model": "jev-1.13.0", "answers": { "<your id>": {...} }, "usage": {...} }
```

jev does not chat. It answers *typed questions about a state*, which is why a move
in this project is one `choice` question rather than a conversation. The smallest
working example is [`examples/jev_hello.py`](../examples/jev_hello.py).

**Fan-out is the feature worth knowing about.** One request may carry many
questions about the same state, all answered in parallel. That is what makes the
diagnostics in this repository possible: `gomoku.perceive` asks "which point do
you play" *and* a batch of scorable yes/no questions about the same position in a
single call, so the model's choice and its perception can be compared without any
risk that the position drifted between calls.

## Question and answer shapes

| type | ask with | answer carries |
| --- | --- | --- |
| `noul` | `instructions` | `noul`: the probability that the answer is yes |
| `choice` | `instructions` + `criteria` (a dict of option key -> description) | `choice`, `probabilities` over exactly your option keys, `confidence` |
| `score` | `instructions` + `criteria` (an ordered list of levels) | `score`, `probabilities` keyed `"0".."n-1"`, `legend`, `confidence` |

Observed invariants, useful as assertions:

- `choice` returns a probability for **exactly** the keys you supplied -- no
  invented options, none dropped.
- `choice` -> `choice` is always the argmax of `probabilities`.
- probabilities are rounded to two decimals and sum to 1 within about 0.02.
- `criteria` must be bounded. This project caps the option menu (see
  [DESIGN.md](DESIGN.md)); offering all 225 points of an empty 15x15 board works
  but costs roughly 3.5x the input tokens and 7x the output tokens of a 30-option
  menu.
- a `choice` question needs at least two options, so a forced move must be played
  without calling the API.

## The three formulas, and how much to trust them

Only the first is documented. Treating all three as equally official is the
mistake this section exists to prevent.

### 1. `score` -- documented

```
score = sum(level_index * probability)
```

Stated in prose on the *Score* primitive page ("a probability-weighted mean of the
level numbers") and again on the API page. Safe to rely on.

### 2. `choice` confidence -- measured, not promised

```
confidence = clamp((n * peak - 1) / (n - 1), 0, 1)
```

where `n` is the number of options and `peak` the largest probability. The only
formula published anywhere is inside a demo widget on the *Confidence* page, and
it is stated for three options as an **approximation**:
"(3 x largest probability - 1) / 2". Generalising 3 -> n reproduces jev's `choice`
confidences to within 0.010 over the 27 answers measured here, the residual being
the two-decimal rounding of the published probabilities.

This is an empirical fit that happens to match. It is not a guarantee.

### 3. `score` confidence -- reverse-engineered

```
confidence = clamp(1 - 2 * sum(p_i * |i - mean|) / (n - 1), 0, 1)
```

Not on the vendor's site in any form. The docs describe `score` confidence only
qualitatively and defer the real computation to a future cookbook. Fitted here to
27 `score` answers with a mean error of 0.014.

The published peak form does **not** fit `score` answers (mean error 0.054, max
0.113), and that makes sense: score levels are ordered, so mass on two adjacent
levels is a precise reading while mass on the two extremes is not, and a peak-only
measure cannot tell those apart.

### What to do about it

TypeSafe state that you are "never locked into our definition" of confidence. So:

> **Derive your own certainty measure from `probabilities`. Do not threshold on
> the returned `confidence` field.**

This project reports `confidence` because it is a useful signal about jev's own
state (see below), but no control flow depends on its exact value.

## Confidence is a usable signal, for a different reason

Independently of how it is computed, the *shape* of the returned distribution
turned out to separate questions jev can answer from questions it cannot:

| kind of question | typical confidence | example |
| --- | --- | --- |
| reading a fact off the state | 0.98–1.00, saturated | "is D5 occupied by a white stone?" |
| applying a rule stated in prose | ~0.95, saturated | "with only 4 points available, can five ever be made?" |
| deriving a consequence from the board | 0.24–0.79, mushy | "can this line still reach five?" |

It knows what it does not know. That is directly usable as a gate: an answer
landing in the mushy band is one to verify rather than act on. Evidence in
[FINDINGS.md](FINDINGS.md) and [EXPERIMENTS.md](EXPERIMENTS.md).

## Which input channel actually influences the answer

Measured, because it is not obvious and it cost this project several rounds of
wasted prompt engineering:

| field | what goes in it | measured influence |
| --- | --- | --- |
| `instructions` | rules, tactics, priorities | **close to none** (0/9 across four wordings and two field placements), despite being 43% of the request |
| `criteria` | a description per option | works (moved the answer's probability from 0.01 to 0.35–0.70) |
| `state` | concrete facts about the situation | strongest (0.01 -> 0.96) |

Two consequences for anyone building on jev:

1. Writing rules into the prompt barely moves the answer. Writing **facts about
   this instance** does. Do the rule-to-instance step in your own deterministic
   code and feed jev the conclusion.
2. `criteria` is read, and it is usually wasted -- the default is often just the
   option key restated. Per-option consequences belong there. Keep each one to a
   single sentence with a single quantity: the same number in a two-clause
   sentence was ignored (0.02) where one clause worked (0.35).

## Operational notes

- **Latency and cost.** About 0.6 s and roughly 1.7k input / 250 output tokens per
  move in this harness. Reuse one `requests.Session`: doing so moved the measured
  p50 from 1.35 s to 0.56 s, because the TLS handshake was otherwise paid per
  call.
- **Reproducibility.** High. Five repeats of an identical request returned the
  same answer with near-identical probabilities. There is no "try again and it
  will be right".
- **Retries.** `gomoku/jev_client.py` retries 408/429/500/502/503/504 with a
  linear backoff and raises on everything else.
- **Credentials.** `$TYPESAFE_API_KEY`, or a file named by
  `$TYPESAFE_API_KEY_FILE`. There is deliberately no in-repository path, and the
  key is printed only as a length plus a short hash.

## Provenance of this page

The formula analysis comes from a separate benchmark that put the same ten typed
questions to jev and to four chat models forced into jev's response schema. That
harness is not part of this repository; what survived is the part that is useful
to anyone calling the API. The numbers quoted here (27 `choice` answers, 27
`score` answers) are from that work; everything else on this page was measured by
the gomoku harness in this repository and can be reproduced with the commands in
[EXPERIMENTS.md](EXPERIMENTS.md).

Not reviewed by TypeSafe. Corrections welcome.
