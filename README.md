# gomoku-jev

A gomoku (five-in-a-row) referee built to **measure** the thing playing it. The
board, the rules, the verdicts and the scoring are deterministic code; the players
are pluggable. One of them is [jev](https://docs.typesafe.ai) (TypeSafe System
One), a decision engine rather than a chat model. Another is any
OpenAI-compatible endpoint. A local bot and a human are players too.

Gomoku is the probe, not the point. Every move is logged with the tactical facts
the engine computed *before* the model was asked, so a loss can be attributed
instead of merely recorded: *this* move had a win available and did not take it,
*that* move let an unstoppable threat form.

> **What it found.** jev reads a board almost perfectly (single points 54/54, both
> ends of a line 10/10, counting 10/10, AND/OR 10/10) and applies rules stated in
> prose almost perfectly (10/10). What it does not do is derive a *consequence*
> from the board -- "can this line still reach five" gets 4/10 from the grid and
> 10/10 from the same fact written in words. Full write-up in
> **[docs/FINDINGS.md](docs/FINDINGS.md)**, method and raw evidence in
> **[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)**.

## In one paragraph: what jev is

jev is a strong **facts → judgement** engine and not a **position → consequences**
engine. It reads what you put in the state with near-perfect accuracy, applies
rules you state in prose correctly, and picks sensibly among options whose
consequences it has been told. What it will not do is work the consequences out
itself: anything that means taking a rule and applying it to *this* position one
move ahead has to be done by the caller and handed over as a conclusion. Writing
that rule into the prompt does not substitute for it -- across four wordings and
two request fields, the instruction text moved the answer not at all, while one
sentence of concrete fact about the position moved it from 0.01 to 0.96.

Two properties make it easy to work with anyway. Its confidence is informative
rather than decorative: saturated (0.98–1.00) on facts, mushy (0.3–0.8) on
inferences, so it knows what it does not know and that band is directly usable as
a gate. And it is highly reproducible -- five identical requests returned the same
wrong move with near-identical probabilities, so "retry until it is right" is not
a strategy. Practical reading: use it as a decision layer over options your own
deterministic code has already annotated, not as a reasoning layer. Doing exactly
that took it from losing every game to conceding none, while never once creating a
threat of its own -- which is a division-of-labour result, not playing strength.

## Install

```bash
pip install gomoku-jev              # engine, bots, live view, jev backend
pip install "gomoku-jev[openai]"    # adds the chat-model seat
```

Or from a checkout: `pip install -e ".[openai]"`.

What each seat needs:

| to do this | install | configure |
| --- | --- | --- |
| local bots, play yourself, view or replay any of the recorded games | nothing at all | nothing |
| let jev play | `pip install -e .` | `TYPESAFE_API_KEY` -- **your own** TypeSafe account or trial; jev is a commercial product and this repository does not come with access |
| let a chat model play | `pip install -e ".[openai]"` | `OPENAI_API_KEY`, or just `OPENAI_BASE_URL` for a local server that needs no key |

The first row is literal: a bare clone runs `python -m gomoku.cli --black heuristic
--white random` and `python -m gomoku.replay` without importing a single
third-party package. A missing key is reported as one actionable line, not a
traceback.

## Try it without any API key

The engine, the referee, the scoring and the web view are pure standard library,
so this works immediately:

```bash
# two local bots, 4 games, with the score table
gomoku --black heuristic --white random --games 4 --seed 1

# watch it in a browser: a board, the move list, and each player's raw reply
gomoku --black heuristic --white random --size 9 --live

# play it yourself
gomoku --black human --white heuristic
```

`--live` starts a local HTTP server on `127.0.0.1:8765` (nothing is exposed
outside your machine unless you pass `--live-host`). If a proxy sits between your
browser and the server, `--live-file board.html` writes a self-contained page
instead, which needs no network at all.

## Play jev

```bash
export TYPESAFE_API_KEY=...          # or: TYPESAFE_API_KEY_FILE=/path/outside/the/repo

gomoku --black jev --white heuristic --games 2 --size 9 --out results/jev.json
python3 -m gomoku.probe              # connectivity plus five fixed positions
```

jev does not chat: it answers *questions about a state*. So one move is one
`choice` question -- the state is the position, the options are a bounded menu of
legal points, and the answer carries a probability distribution and a confidence
alongside the pick. That distribution is most of why this project exists: it can
be compared against what the engine knows to be true.

What the API guarantees, what it only appears to guarantee, and which of its three
published formulas are actually documented: [docs/jev-api-notes.md](docs/jev-api-notes.md).

## Play any other LLM

Any OpenAI-compatible endpoint works -- the hosted API, OpenRouter, vLLM,
llama.cpp, Ollama:

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1     # optional
gomoku --black openai:openai/gpt-oss-120b --white jev --size 9 --live

# a local server needs no key at all
OPENAI_BASE_URL=http://localhost:11434/v1 gomoku --black openai:qwen3:32b --white heuristic
```

The model id is whatever your endpoint calls it: `openai/gpt-oss-120b` is
OpenRouter's naming, the official API wants `gpt-4o-mini`, Ollama wants
`qwen3:32b`. `gomoku` passes the string straight through, so an unknown id comes
back as the endpoint's own error.

Two modes. `choice` (default) gives the model the *same* option menu jev gets and
asks for jev's answer shape, so the numbers are comparable. `free`
(`openai:<model>|free`) gives it a bare board and asks for a coordinate, which is
how a person would actually use it -- and the only mode where an illegal move is
possible.

### Adding your own backend

One file and one `register` call. Nothing else in the package needs editing: the
CLI, `--backend` choices, the ablation runner and the diagnostics all read the
same registry.

```python
from gomoku.backends import Backend, register
from gomoku.players import MoveResponse, Player

class MyPlayer(Player):
    def propose(self, view, feedback=None):
        # view.board_text(), view.stone_lists(), view.legal_moves(), ...
        return MoveResponse(move=view.legal_moves()[0])

register(Backend("mine", "my engine", lambda arg, seed, overrides: MyPlayer()))
```

Then `gomoku --black mine --white heuristic` works, and `gomoku --list-backends`
shows it.

## What gets measured

Per player, across a series:

| metric | meaning |
| --- | --- |
| `win_conversion` | a win was available in one move -- was it taken? (the hardest number to explain away) |
| `block_rate` | the opponent had exactly one winning point -- was it covered? (two points is scored `unavoidable_loss`, not a mistake) |
| `of.make` / `of.stop` | one level earlier: making / preventing a four with *two* completing points, which cannot be blocked |
| `illegal_move_rate`, `parse_failure_rate` | format and rule compliance, over attempts rather than moves |
| `forfeits`, `referee_fallbacks` | gave no usable answer at all |

Colours alternate between games, so first-move advantage cancels out. Exact
definitions, and the reasoning behind the candidate menu and the threat levels, are
in [docs/DESIGN.md](docs/DESIGN.md).

## Prompt versions

Every word sent to a model lives in [`gomoku/templates/`](gomoku/templates/) --
no prompt text is hard-coded. A prompt is four parts: the rules, the **tactics**
(the experimental axis), the board state, and a small amount of per-API glue.
Lookup is "backend first, then shared", and a test fails if a backend file
silently shadows a shared one, because at that moment the two players stop being
measured with the same ruler.

```bash
gomoku --list-prompts                                  # what is available
gomoku --black jev@p0-one-move --white jev@p1-threat-ladder --size 9
python3 -m gomoku.ablate --backend jev --repeats 5     # hit rate per wording
```

The tactics file name *is* the version, and it is recorded in every match log, so
runs made under different prompts never get averaged together. To write your own,
drop a file in `gomoku/templates/shared/tactics/` -- or point `--templates` at a
directory outside the repository.

## Tools

| command | what it does |
| --- | --- |
| `gomoku` (`python -m gomoku`) | play matches, score them, write JSON |
| `python -m gomoku.probe` | connectivity plus five fixed positions with known answers |
| `python -m gomoku.audit` | one game, one human-readable Markdown log, flushed per move |
| `python -m gomoku.ablate` | prompt/fact ablation over fixed positions, repeated |
| `python -m gomoku.perceive` | ask what a backend *sees*, not just what it plays (fan-out) |
| `python -m gomoku.vision` | decompose one judgement into its atomic steps |
| `python -m gomoku.infer` | board-free rule arithmetic, as a control |
| `python -m gomoku.postmortem` | re-interrogate one recorded move under several conditions |
| `python -m gomoku.replay` | turn a saved game into a standalone HTML page you can step through |

Recorded runs from the experiments are in [`results/`](results/), indexed by
experiment number in [`results/README.md`](results/README.md). To look at any of
them:

```bash
python3 -m gomoku.replay results/audit-optionfacts-threat.json --open
```

That writes a self-contained page: the board with a move slider, and for every
ply the model's raw reply, its confidence, its token cost, the option menu it was
given, and the facts the engine had already computed. No server, no network.

## Layout

```
gomoku/board.py rules.py game.py     geometry, the single verdict function, turn order
gomoku/analysis.py                   threats, dead lines, the scoring ground truth
gomoku/candidates.py                 the bounded option menu shared by all choice backends
gomoku/match.py metrics.py           the referee (keeps rejected attempts) and the scoring
gomoku/backends.py                   the registry: name -> player
gomoku/jev_client.py llm_player.py   jev transport, and jev as a player
gomoku/openai_player.py              any OpenAI-compatible chat model as a player
gomoku/templates/                    every prompt, as text
gomoku/live.py live.html             the browser view, live or from a saved game
gomoku/replay.py                     a recorded game -> a standalone page
docs/                                findings, experiment report, design, jev API notes
```

## Tests

```bash
python -m unittest discover -s gomoku/tests -t .
```

234 tests, a few seconds, no network: importing the test package replaces the
HTTP layer with something that raises, so a unit test cannot quietly spend tokens.
Live checks are separate, explicit commands (`gomoku.probe` and friends).

## Scope and honesty

- The findings describe **jev-1.13.0**, measured in September 2026, through this
  harness and these prompts. They have not been reviewed by TypeSafe.
- The comparison model was `openai/gpt-oss-120b`. It is a reference point for
  "is this weakness specific to jev", not a leaderboard.
- Any run with `--facts` or `--option-facts` set is no longer measuring "can it
  play gomoku" but "can it decide once the analysis is handed to it". Those runs
  are tagged in every record and are never pooled with the others. The one
  unbeaten jev game came from that mode, and it is a division-of-labour result,
  not a strength-of-play result -- see the end of
  [docs/FINDINGS.md](docs/FINDINGS.md).

Corrections and contradicting runs are welcome; the fixed positions and the
ablation harness exist so that disagreements can be settled with a command.

中文文档：[README.zh.md](README.zh.md) · [docs/FINDINGS.zh.md](docs/FINDINGS.zh.md) · [docs/EXPERIMENTS.zh.md](docs/EXPERIMENTS.zh.md)

## License

MIT -- see [LICENSE](LICENSE).
