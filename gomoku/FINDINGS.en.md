# jev: a capability profile

A systematic assessment of **jev-1.13.0** (TypeSafe System One), using gomoku as the probe.
The control is **openai/gpt-oss-120b** (via gpt-oss). Every claim below is backed by an
experiment; the method, raw data and reproduction commands are in
[EXPERIMENTS.en.md](EXPERIMENTS.en.md) (16 experiments, E1–E16).

*(中文版: [FINDINGS.md](FINDINGS.md))*

---

## In one sentence

**jev is an excellent "facts → decision" engine, not a "position → lookahead" engine.**
It reads whatever you put in the state with near-perfect accuracy, applies rules correctly
when you state them in words, and decides well when you hand it the relevant facts. What it
does not do is derive "what happens next" from the state on its own. Any work that requires
*applying a rule to a position and looking one move ahead* has to be done by the caller
before the request is sent.

---

## What it can do

| Capability | Measured | Confidence shape | Source |
| --- | --- | --- | --- |
| Read a single point (black / white / empty) | **54/54** | 0.98–1.00 / 0.01, saturated | E8 |
| Read whether each end of a line is blocked | **10/10 each** | 0.99 | E7 |
| Count stones in a line (is there a four?) | **10/10** | 0.99 | E7 |
| Measure a span (how many usable points a line still has) | **10/10** | — | E7 |
| Combine facts with AND / OR | **10/10 each** | — | E7 |
| **Rule reasoning from premises given in words** | **10/10** | 0.95 / 0.07, saturated | E8 |
| Decide from injected facts | 10/10 fixed positions; 14/14 in a live game | 0.7–0.8 | E10, E16 |
| Compare numbers across options | 3/3 (needs one plain sentence) | 0.35 | E14 |

In short: **reading, counting, combining, and reasoning from words are all near-perfect, and
the confidence is saturated — it knows what it knows.**

---

## What it cannot do

| Missing capability | Measured | Confidence shape |
| --- | --- | --- |
| Derive "can this line still reach five" from the board | 4–5/10 | 0.50–0.79, mushy |
| Derive "can the opponent build an unstoppable four next turn" | 2/5; 0.36 on the decisive position | noise |
| Derive "how many more stones can the opponent still add here" | **1/10** | 0.24–0.66 |
| Apply a rule from the prompt when choosing a move | **0/9** (4 wordings × 2 fields) | — |

The cleanest contrast (E8) — the same fact in two representations, opposite answers:

| Representation | Question | Answer |
| --- | --- | --- |
| **Words** | "A player has four in a line, both ends taken by the opponent. Can they ever make five?" | **No, p=0.13** ✓ |
| **Board** | Same position: "Can black's E5 F5 G5 H5 still reach five?" | **Yes, p=0.61** ✗ |

The dividing line is not "can it reason" but **whether the premises arrive as words or as a
position**.

---

## Six behavioural traits

**1. It performs no sub-derivation while choosing a move.** It computes spans correctly
(10/10) when asked, but does not compute them when picking a point. Whatever is not written
in the text does not enter the decision. (E7 vs E10)

**2. Its probability mass follows "how close is this to my own stones".** In a position that
had to be defended, its three highest-probability points were all adjacent to its own stones
(distance 1), while the two correct answers sat 2–3 away and received 0.00 and 0.01. Defence
was not ranked lower — it was not part of the scoring at all. (E11)

```
3 points with probability ≥0.13: mean distance to own nearest stone 1.00
18 points with probability <0.02: mean distance 2.17
the two correct answers: distance 2 and 3, probability 0.00 / 0.01
```

**3. The three input channels differ enormously in influence.**

| Channel | What goes there | Measured effect |
| --- | --- | --- |
| `instructions` | rules, tactics, priorities | **≈ no effect** (0/9, and it is 43% of the request) |
| `criteria` | the consequence of each option | works (0.35–0.70) |
| `state` | concrete facts about this position | strongest (0.96) |

Writing rules is useless; writing **facts about this position / this option** works.
(E12, E13)

**4. It is sensitive to sentence structure — enough to override content.** The identical
information:

```
two clauses, two numbers → ignored entirely (0.02, same as no annotation at 0.01)
one sentence, one number → works (0.35)
```

Per-option annotations must be short. (E14)

**5. Its confidence is a usable signal.** Factual questions come back at 0.99 / 0.01
(saturated); inferential ones at 0.3–0.8 (mushy). It "knows what it does not know", and that
gap can be used directly to decide whether a question falls inside its competence.

**6. It is highly reproducible.** Five repeats of the same request produced the same wrong
move (B5 / C5) with near-identical distributions. There is no "try again and it will get it".
(E4)

---

## How to use it

**Do**

- Do the "apply the rules to this position" work on the caller side; send only the
  **conclusions**
- Injected facts must be: **one sentence**, **one quantity**, **covering every priority
  level**, and **discriminating between options**
- Gate on confidence: do not trust answers that land in the 0.3–0.8 mush
- Use `criteria` to carry per-option consequences — that field is easy to waste (it often
  just restates the coordinate)

**Don't**

- Don't expect it to execute rules from `instructions`, no matter how clearly, how
  urgently, or in which field they are written
- Don't give it tasks needing multi-step lookahead or counterfactual search
- Don't reuse a scoring function as a prompt generator. "If we can already win, the level
  below is moot" is correct for a metric and fatal for an annotation — it cost us a game (E15)

**Self-check**

> Count the **distinct** annotation strings. If every option carries the same text on some
> move, that move effectively has no injection — and that degeneration tends to happen
> exactly when the threat is highest. (E15)

---

## Against gpt-oss-120b (same questions, same positions, one request each)

| Capability | jev-1.13.0 | gpt-oss-120b |
| --- | --- | --- |
| Read point / ends / AND / OR / span / count / three colours | **124/124** | **124/124** |
| Rule reasoning from words | **10/10** | **10/10** |
| **Deriving counterfactuals from the board** | **19/40** | **40/40** |
| Games won, no injection | 0 | **11** |

The two ends are identical; 100% of the difference sits in the step between them. gpt-oss
saturates at 0.00/1.00 with zero self-contradiction; jev lands in 0.24–0.79 on exactly those
questions.

**The price** (one 48-ply game):

```
jev       24 requests   out=  6,176 tokens   avg  0.6 s
gpt-oss   27 requests   out=291,938 tokens   avg 82.7 s
```

gpt-oss buys that one capability with **47× the output tokens and 138× the latency**. It also
has an independent fragility there: past a certain complexity, its 16k output budget is
consumed entirely by hidden reasoning and it returns **empty content** three times in a row
and forfeits (E16).

---

## Fit and misfit

**Task shapes that suit jev**

- Candidates are already enumerated and each option's consequence has been computed by
  deterministic code; what is needed is "pick one and give a calibrated distribution"
- The state can be fully written out, and the judgement depends only on what is explicit in it
- A probability distribution and a confidence are wanted, not just an answer
- Latency and cost matter (0.6 s / ~250 output tokens per decision)

**Task shapes that do not**

- Lookahead, search, "if I do this, what will they do"
- Complex rules that the model itself must instantiate on a concrete case
- A state where the model must distil implicit conclusions out of a raw representation
  (a board, a graph, a long document)

One line: **treat it as the decision layer, not the reasoning layer.** Put the reasoning in
deterministic code, or in a model willing to burn 47× the tokens.

---

## One boundary that must be stated

Once injection is switched on, the proposition under test changes: it is no longer "can jev
play gomoku" but "can jev decide correctly once handed a threat analysis". jev won the E16
game, but it **never had a single attacking chance** — the only thing it did was avoid losing,
and that came from the caller's engine computing threats per option. **That is a win by
division of labour, not by playing strength.**

Every match record carries `facts` / `option_facts` / `prompt_version`; injected and
non-injected data are never pooled.
