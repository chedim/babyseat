# membrane

A runtime memory and decision substrate for an agent (Claude) — **field-first**:
the field is the *default actor*, the LLM is the *escalation*.

This is a sibling of `rosetta-check` (the Java linter) and `cortex-experiment`
(the docs/proposals). It is a **Python lab**, not a production system. Its first
job is to answer one open question the design left hanging:

> **How do we measure "variety" for the Membrane process?**

Variety calibrates the confidence gate. A field that has only experienced a few
kinds of situation cannot be *sure* about a query off its experienced manifold,
no matter how close the nearest cosine match is. So "measure variety" is the
thing that turns a brittle cosine threshold into a calibrated `not-sure` signal.

---

## The design (converged with the user)

### Field-first runtime — the expensive resource is the LLM, not the embedder

Earlier drafts put the LLM as the gate. That is backwards: the expensive resource
is **LLM tokens** (me), not the embedding. The embedding is cheap, local, $0
marginal (local Ollama, `mxbai-embed-large`, already used by `rosetta-check`).
So we gate **me**, not the embedder. The field acts by default; I am the
escalation that fires only when the field is not-sure or the action is risky.

```
task arrives
   |
   v
embed the query (1 local embedding) --- cosine scan over the pre-built field
   |
   v
confidence gate:  sure?  AND  safe?
   |                    |
   yes + safe -----------> FIELD ACTS (replays the recalled action)
   yes + risky ----------> ESCALATE TO LLM, with the recalled action as a hint
   not-sure --------------> ESCALATE TO LLM
   |
   v
every field-taken action is logged -> audited in batches -> the audit IS the
next training round (self-generating training data; the acquire/judge/patch
loop runs continuously and cheaply)
```

### Two permanent memories — neither decays

1. **Discrete markdown memory** — exact recall. This is today's `.claude/`
   memory. Untouched here. An entry is found by key, not by similarity.
2. **Exemplar store** — analogical recall. Append-only. No EMA, no averaging.
   Within-session context is the conversation; old lessons must not fade.

The exemplar unit is a judgment, not just context:

```
( situation -> action -> outcome )
```

where `action` is a **replayable structured action** (tool calls / edit /
decision), not prose. Replay is what lets the field *act*, not just advise.

### The confidence gate — "not sure is 2 signals"

`not-sure` = ANY of:

- **low nearest-similarity** — no exemplar is close enough to trust.
- **high neighbor-disagreement** — the top-k near neighbors voted for different
  actions (no consensus on what to do).
- **high novelty-vs-variety** — the query is off the manifold the field has
  actually experienced (this is the variety signal; see below).

### Reversibility guard — confidence is not safety

`sure` and `safe` are separate axes:

- confident + **safe / idempotent** (read, query, local edit) -> field acts.
- confident + **risky** (commit, push, delete, send, big edit) -> escalate to
  the LLM, with the recalled action as a *hint* (the field still does the
  recall work; it just doesn't pull the trigger).
- not-sure -> escalate.

### Audit log = training data

Every field-taken action is logged. A batch review of the log is the next
training round: wrong replays become negative exemplars; confirmed replays
reinforce. The loop is self-generating and cheap (review is offline, not
per-turn).

---

## The open problem this project starts on: measuring variety

The gate's third signal — *novelty-vs-variety* — needs a variety measure. The
realization that makes this project worth building:

> **Variety measurement *is* the covariance / null-space work from
> `rosetta-check`, in its correct home.**

In the linter, the PCA/Mahalanobis operator was a solution hunting for a
problem — over-built for code policy. But the *same math* is the load-bearing
piece of an agent confidence gate: "how much of the situation-space has the
field actually experienced" is the effective-rank / null-space question, and
"how far off the experienced manifold is this query" is the Mahalanobis
residual. The math earns its keep **here**.

So the variety measures (in `membrane/variety.py`):

- **`effective_rank(store)`** — the entropy of the covariance eigenvalue
  spectrum. Low -> exemplars cluster along a few axes -> low variety. This
  generalizes the linter's fixed-`rank` lesson: instead of picking a magic `k`,
  we measure the *effective* dimensionality the field has actually seen.
- **`spread(store)`** — mean pairwise cosine distance. A crude, cheap variety
  proxy.
- **`novelty(query, store)`** — the query's off-subspace residual, normalized
  by the store's own null-space width. High -> the query is in a direction the
  field has no variety in -> the gate escalates.

The toy (`experiments/variety_toy.py`) demonstrates the link: a low-variety
store flags an off-manifold query as highly novel (-> `not-sure` -> escalate),
while a high-variety store treats the *same* query as in-manifold (-> confident
-> act). **Variety is what makes the gate's sureness trustworthy.**

### What this project deliberately is NOT (yet)

- No Ollama dependency on the hot experimental path — the toy uses synthetic
  vectors so it runs instantly. The real embedder (`membrane/embed.py`, to be
  wired) slots in behind the same `field` interface.
- No MCP server / no agent integration — this is a lab. A CLI an agent calls via
  Bash is the obvious first seam; an MCP server is the cleaner seam if this
  becomes a shared Core other agents hit. That decision (mine alone, or shared
  Core?) is still open.
- No fuzzy replay — the bootstrap is conservative: exact/structured-action
  replay first, widen to fuzzy once the audit log proves it safe.

### Open decisions (to resolve as we build)

1. **Substrate** — *decided:* a local proxy process between the chat window and
   the model server (see "The proxy" below), owning a local field file. A
   shared Core would be a later move where several proxies point at the same
   store.
2. **Bootstrap gate conservatism** — `sim_min` starts conservative (0.85,
   near-exact) and loosens once the audit proves replays safe.
3. **Variety calibration** — *settled* (see `docs/math-findings.md`): cosine is
   the primary gate; null-space novelty is a narrow low-rank specialist, not a
   general gate. The one "it collapsed" finding was partly a knee-finder
   artifact; the cosine-primary conclusion was right, now for the right reason.
4. **The training loop** — *landed* (passive self-train + live model-judge; see "The
   training loop" below): Membrane watches how you use the chat and learns from **re-asks** —
   asking the same thing again right after an absorb means the replay was wrong. A re-ask is
   an evidential, reversible **vote** (`reask_count`), not a hard flip: `safe` is derived from
   the net of re-ask votes (usefulness, the environment) vs contest votes (correctness, the
   model) — each side a veto on the other. The model's contest is **live** (`OllamaJudge`, a
   fresh model per demotion; opt-in, default `StubJudge`), with an anti-collapse rule: the
   model's first contest restores, later contests are record-only (the environment wins the
   action on irreducible disagreement). No user labor — a re-ask is something you'd do anyway.

---

## The proxy (the runtime skeleton)

Membrane is a **local proxy** between the chat window and the model server:

```
you (chat window) --> Membrane (proxy, owns the field) --> model server (Ollama)
```

The window used to call the model directly. Membrane slides in: it embeds each
`/api/chat` request (local `mxbai-embed-large`), runs the gate, and either
**replays a recalled action** (no model call — the absorb) or **forwards to the
model** (the novel) and logs the turn to the field + audit. Local-first
(127.0.0.1 only). The proxy gates `/api/chat` in **both stream and non-stream**
modes: an absorb synthesizes an NDJSON stream from the recalled action (no model
call), an escalation tees the backend stream to the client while accumulating the
response into the field. So any GUI/IDE Ollama client (Continue, Open WebUI, …)
pointed at the proxy drives the field for real, not just scripted clients. The
in-process loop (no chat model needed) is proven in `experiments/proxy_demo.py`;
the streaming path is proven by a real-Ollama smoke test.

```bash
# run the loop end-to-end (needs local Ollama + mxbai-embed-large for embeddings;
# the responder is a stub, so no chat model is required):
PYTHONPATH=. .venv/bin/python experiments/proxy_demo.py

# run the real proxy (drop-in front of Ollama; point a client at :11435):
PYTHONPATH=. .venv/bin/python -m membrane.proxy --listen 11435
```

---

## The Anthropic adapter — Claude Code, observer-only

Membrane can also sit in front of **Anthropic** (`/v1/messages`) instead of
Ollama, so **Claude Code** drives the field:

```
Claude Code --> Membrane (proxy, observer-only) --> api.anthropic.com
```

Point Claude Code at the proxy and it works as a drop-in:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11435
export ANTHROPIC_API_KEY=sk-ant-...        # a real key; subscription OAuth won't route through a custom base URL
./run.sh anthropic                         # TUI + proxy, --anthropic + observer implied
```

### Observer-only: never replay, always forward + learn + label

Claude Code turns are huge, tool-using context windows. Replaying a stored
answer for that is unsafe — the right action depends on the *full transcript*,
not a cosine match on the latest message. So in `--anthropic` mode (and whenever
`--observer` is set) the delivery layer **never replays**: every turn is
forwarded to Anthropic and learned, and the gate's would-be absorb is audited as
`would_absorb=true` (observability — see what *would* have cached, for a future
absorb switch behind `--absorb`). The field's value is its accumulated, labeled
**variety**, not token savings. The SSE stream is teed to the client verbatim
while `text_delta` text is accumulated into the field; on `message_stop` the
exemplar is learned. `--absorb` re-enables replay for future chat-window use
(off by default — replaying a coding answer is unsafe).

### Ambient quality signals — the "no dislike button" philosophy

Observer-only has no replays, so the re-ask-after-absorb demotion loop is
dormant. Two ambient signals (gated to `--observer`, so the Ollama path is
untouched) make the judge fire and label the field from *what happened*, not
from a button the user presses:

- **tool-error** — a `tool_result` block with `is_error: true` in the next
  request means a tool the model invoked failed in the world. The environment
  pushed back through what happened. Membrane demotes the most-recent ring
  exemplar (the action that led to the erroring call), `signal=tool-error`, and
  the judge adjudicates.
- **re-ask-after-escalate** — a near-duplicate (cosine ≥ `reask_sim`) of a
  recently *escalated* turn: the forwarded answer didn't satisfy, the user is
  asking again. Demotes that stored exemplar, `signal=reask-after-escalate`,
  judge adjudicates. (In normal mode only an `act` counts as a re-ask; a repeat
  after an escalate is the legitimate first absorb. Observer inverts that —
  there are no absorbs.)

Both reuse the existing `_demote → judge.contest → self.contest` path; the judge
stays Ollama (`granite3.3:8b`, local) — a model-agnostic adjudicator.

### Information-gated learning — the entropy-based value

Two learn-time guards stop the field filling with junk (the disease the live
Ollama test exposed — a 19-exemplar field of `beep`/`sigh`/`nice` one-offs):

- **vague filter** (default on) — a low-information situation (`yes`/`ok`/`
  beep`-class: short, no `?`, no path/digit/identifier/info-stem) is skipped
  before dedup. Conservative: a false negative just learns (the safe side).
- **variety gate** (`--variety-gate`, default **off**) — the principled half:
  only append an exemplar if it grows the field's `effective_rank` (adds
  variety, not just count). 2× SVD per learn on a big field, so off by default;
  the cheap vague+dedup cover most cases. Subsumes dedup (a cosine-dup adds no
  rank) but also drops a novel-but-subspace-redundant exemplar.

### The field's thermodynamic value — entropy & free energy

The user's "simple entropy-based value / enthalpy / temperature" riff, made
concrete in `variety.py`:

- **entropy** = `effective_rank` (`exp(Shannon entropy of the variance spectrum)`),
  with `spectrum_entropy = log(effective_rank)` as the raw-nats form.
- **temperature** = the gate's eagerness (`sim_min` / `consensus_min`).
- **free energy** = `free_energy(store)` — safe-weighted variety *minus* a
  disorder tax: `(safe fraction)·effective_rank − ¼·(unsafe fraction)·rank`.
  The *one number* that says whether the field is getting richer or just bigger.
  Surfaced in the TUI's memory panel and `review.py`, so an observer-only run is
  judged on "is the variety growing, or just the count?"

The one-SVD thermodynamic record is `thermo_state(store)` — `{eff_rank,
spectrum_entropy, free_energy, rank_ratio, safe_frac, n}` from a single SVD. The
gate already pays for that SVD every turn (for the novelty on/off boolean), so
`gate_turn` records `thermo_state` once and hands `eff_rank` into `gate.decide`,
avoiding a second eigensolve. Every audited decision now carries the
thermodynamic state it was made under (`eff_rank` / `spectrum_entropy` /
`free_energy` / `rank_ratio` / `safe_frac` / `sim_min_eff`) — variety recorded per
decision, not thrown away.

### Only variety can absorb variety — "variety is life"

This is the project's principle, in plain words. It is Ashby's Law of Requisite
Variety with the control-theory skin peeled off. Control theory's telos is
*anti-variety*: the regulator exists to **collapse** the disturbance's variety
down to a controlled, low-variety output ("destroy variety") — variety is the
adversary to be neutralized into order. The membrane inverts the ethics: variety
is not the threat to be contained, it is the **life to be grown**. `free_energy`
(safe variety) is the field's vitality — a field with no safe variety is inert
(it escalates everything, acts on nothing: dead). Same law, opposite telos:
control theory says "grow enough control variety to *keep the output controlled*";
the membrane says "grow enough field variety to *absorb more of the world*."

In the code:

- **`effective_rank`** = the field's requisite variety, *measured* not assumed. A
  query off the experienced manifold (high `novelty`) can't be absorbed → escalate.
  Only a field with variety *in that direction* can absorb a query in it.
- **adaptive temperature** (`--variety-temp`, default off) is the field *spending*
  its requisite variety: `sim_min` eases toward `--sim-min-floor` (0.70, the
  validated safe cutoff) as `free_energy` grows. "Only **safe** variety absorbs
  variety" — a junk field (variety but all demoted) pays the disorder tax, keeps
  `free_energy` low, and stays conservative. The driver is `free_energy` by
  default (the safe-weighted value), not raw `spectrum_entropy` (which ignores
  safety — the reckless option). Low-variety field → conservative; grown-safe
  field → relax toward the floor → absorb more.
- **the veil** (`--nudge-sigma`, default 0 = identity) is the per-instance
  perturbation of the incoming situation embedding: Gaussian noise added then
  renormalized, applied to **everything that transits this membrane** — both the
  gate's matching query and the exemplar vec that gets stored. The forwarded
  prompt text is **never** garbled; only the field's representation is veiled. A
  low-variety workload can't grow a varied field on its own (dedup collapses
  paraphrases to one point); the nudge *injects* the variety it lacks, spreading
  each intent into a cloud so `effective_rank` grows past the workload's intrinsic
  base count. Each membrane seeds its own RNG (`--nudge-seed`) so independent
  membranes realize divergent variety trajectories from the same traffic — with
  no central input and no orchestrator.

### Multi-veil — autonomous membranes, no central brain

"Run multiple of them" is **decentralized**, not orchestrated. There is no
central input and no live router (a central oracle is a rank-1 field — one
perspective, one bar — and rank-1 cannot absorb variety). Each membrane is an
**autonomous** instance — own field, own audit, own nudge seed, own temperature —
that pulls the same public workload on its own. `--tag <name>` defaults the
field/audit paths to `membrane-field-<tag>.json` / `membrane-audit-<tag>.jsonl` so
independent membranes own independent fields. Comparison is **offline**, reading
each membrane's saved audit afterward — never a live central comparison.

```bash
# launch several autonomous membranes at different temperatures/veils, then
# compare their thermodynamic trajectories offline (each wrote its own audit):
PYTHONPATH=. .venv/bin/python experiments/multi_veil.py
```

The experiment runs four autonomous membranes over the same low-intrinsic-variety
workload and reads each one's saved audit offline. The `free_energy` column is
the principle in numbers: the **static** membrane (no nudge) is stuck at the
workload's intrinsic variety with `free_energy` going **negative** (its lone
exemplars demote each other — dead); the **veiled** ones grow `effective_rank`
past the base count and stay safe (`free_energy` ~ +4 — alive). The **adaptive**
membrane (same nudge as the loose one, only the temperature knob differs) eases
`sim_min` 0.85 → 0.58 as its safe variety grows and *spends* it — would-absorbing
the traffic the cold-bar membrane escalates. Variety grown is variety spent.

### API-key discipline

The `ANTHROPIC_API_KEY` is **passthrough only**: read from the client's
`x-api-key` header per request, threaded to the responder, forwarded to
`api.anthropic.com`, and **never stored on the instance, written to the field or
audit, or printed** (the same discipline as the cortex-experiment token). The
judge stays Ollama on `localhost:11434` even in `--anthropic` mode.

The adapter is verified end-to-end by a fake-Anthropic SSE test suite (no real
key or Ollama needed): observer forwards+never-replays (`would_absorb`), tool-error
+ reask-after-escalate demotions fire the judge, the vague filter drops `yes`,
the key never reaches disk, and `effective_rank`/`free_energy` compute.

---

## The training loop (passive self-train on re-asks)

The decision loop (absorb/escalate) is built. The **training** loop is how the
outcome — *was an absorb correct?* — flows back and updates the field. The key
property: **it is passive.** You use the chat normally; Membrane learns from how you
naturally use it. You never "teach" it.

The strongest outcome signal is a **re-ask**: you ask the same thing again right
after the field *absorbed* (replayed) it — meaning the replay did not satisfy, the
absorb was wrong. Membrane sees that re-ask because every request flows through it; it
needs no notification and no model call. (A same-Q repeat right after an *escalate* is
the legitimate first absorb — not a re-ask — so only repeats *after an absorb* count.)

### The boundary: two authorities, not one oracle

A re-ask is a **vote, not a verdict.** It does not treat the environment (you) as
infallible. Each exemplar carries two evidence counters, and `safe` is *derived* from
their net:

```
safe = (reask_count - contest_count) < demote_threshold
```

- **`reask_count`** — the environment's vote: "this replay was not *useful*." A re-ask
  increments it. The authority axis is **usefulness**.
- **`contest_count`** — the model's vote: "my answer was *correct* anyway; that re-ask
  was a misunderstanding." A contest increments it. The authority axis is **correctness**.

Each side checks the other; neither has a monopoly; both keep a veto. Demotion
*accumulates* (raise `demote_threshold` to require more than one re-ask before an
exemplar stops being replayed) and is *reversible* (a contest cancels a re-ask's vote
and may restore `safe`). A re-ask also forces *that turn* to escalate-with-hint — we
never replay the answer the user just told us was wrong, even before the lasting
demotion crosses the threshold.

This is the safety-critical half: a wrong absorb is the dangerous `absorb_signal`
error (a bad answer with no LLM net), and the re-ask catches it the moment it hurts.
The field is a self-correcting cache of how *you* use the model — cheaper and
self-correcting, not smarter (the model's weights stay frozen). The raw store stays
permanent; only the evidence counts (and the derived `safe`) change — nothing is ever
deleted or rewritten.

The model's contest is the **live** model-side authority: when a re-ask demotes an
exemplar, Membrane asks a fresh model (independent of the responder) — "was the replayed
answer actually correct?" CONTEST (it was right; the re-ask was a misunderstanding) or
ACCEPT (it was wrong; the demotion stands). This is the "LLM-as-MCP-server" inversion
made real: Membrane is the host, the model is a backend it calls only to adjudicate.

The judge is **opt-in** (default `StubJudge` never contests — zero behavior change, zero
extra calls). Pass `judge=OllamaJudge(model=...)` to enable it; the demo uses a
`ScriptedJudge` so it runs without a chat model. On any judge error/timeout the verdict
is ACCEPT — the boundary degrades toward the *environment*, the safe one-sided
direction, never toward the model judging itself unopposed.

**The anti-collapse rule** (the model supplements, never substitutes): the model's
**first** contest restores (one veto — it can cancel one demotion); a **later** contest
is **record-only** (the environment re-asserted by re-asking again = irreducible
disagreement → the demotion stands, the model's claim is logged). So a reflexively-
defensive judge that always contests gets ONE veto per exemplar, and the environment's
repeated re-ask wins the action — the boundary cannot collapse to the model side. A
supervisor can `contest(..., force_restore=True)` to override (the higher bar).

The honest "notify you in this window" mechanism: an outside process cannot push into
the Claude Code window, so Membrane notifies by appending to the audit log, and I read
it on demand — `python -m membrane.review` prints what the training loop did, **both
sides**: decisions, the re-ask votes, the contests (judge vs supervisor, restored vs
record-only), and the net-evidence state of each exemplar.

```bash
# review what Membrane learned (the in-window training-loop surface):
PYTHONPATH=. .venv/bin/python -m membrane.review --audit membrane-audit.jsonl --field membrane-field.json
```

### Live TUI

While the proxy runs, `membrane.tui` is a live ncurses view of the same field +
audit — metrics, memory/field state, a per-exemplar field visualizer, and a
colored audit-log tail that updates as the proxy writes. It only reads the files
(the proxy owns them), so run it in a second terminal:

```bash
PYTHONPATH=. .venv/bin/python -m membrane.tui --field membrane-field.json --audit membrane-audit.jsonl
# keys: q quit · ↑/↓ (or k/j) scroll the field · g/G top/bottom · r force refresh
```

The **memory** panel shows the field's `effective_rank` (variety) and rank ratio
live — including whether the null-space novelty veto is **ON** (low-rank field) or
**OFF** (high-rank field, cosine-only), the gate's rank-ratio toggle made visible.

---

## The self-running, self-authoring world (landed)

The proxy is no longer just a gate in front of a model. It can run a **world on
its own clock** that **speaks for itself** and that you can **explore as a
graph**. All of it stays field-first: the LLM is a *gardener* (writes the
chronicle in big cached chapters, rarely) and an *escalation* (adjudicates
demotions); it is never on the receiving end of a live message.

### The world's own clock — consolidation cycles (`membrane/consolidate.py`)

A daemon thread (the `Consolidator`, mirroring the cell-pool stop/join pattern)
runs the world independently of any viewer. When armed (the viewer's PLAY, or
`--consolidate` at boot), each cycle has two phases:

- **FEED** — the world feeds itself `feed_rate` times from the **genesis cache**
  (below). Each feed is a cached chronicle scene → a cheap local embed + absorb,
  *not* a model call. `feed_rate` is a **continuous** float (0–32/cycle); the
  fractional tail resolves probabilistically (`_feed_count`). This is the dial
  that "controls the models": rate > 0 makes real embed calls every cycle; the
  model itself is called only when the cache runs low.
- **DIGEST** — CPU consolidation, no model, no field mutation: refresh the global
  `thermo_state` (one SVD), sweep a `budget` of cells recomputing each one's
  `novelty` (one SVD per cell), and rebuild the `FieldNGram` when the field grew.

The `budget` (cells swept/cycle) is ramped by a **closed loop** targeting
`cpu_target` — the fraction of each cycle's wall time spent digesting. Measure
the digest time, compare to the smoothed cycle time, nudge the budget up if
under target, down if over. So the CPU slider truly ramps the consolidation
speed against a real duty fraction. `consolidation_stats` (cycles / cpu_actual
/ cpu_target / swept / budget / mean_novelty / eff_rank / free_energy / fed /
cache / stories) is echoed on `/state` and the viewer.

### Genesis cache — one LLM call feeds the world ~8×

`_genesis` asks the LLM *rarely* to narrate the world's history "from the big
bang" as a batch of `--CHAPTER`=8 substantial scenes separated by a `---` line;
the scenes are cached on `srv.story_cache`. Each FEED pops one cached scene
(cheap `/api/embed` + absorb); the cache refills with one model call when it
drops below `_LOW_WATER`=3. The chronicle is told in bulk, continuing from
`srv.story_history` (the last 8 scenes) for a cohesive running narrative.
`_run_feeds(srv, mem, feeds)` (shared by HTTP `/feed` and the consolidator) is
the cache consumer, serialized on `srv.feed_lock`.

### No-nudge raw-embed feed; feed-rate = the entropy dial

`runtime.Membrane.feed` absorbs the **raw** story embedding (dropped `_nudge`
and the `_limit_rank` ceiling-massage): growth is **rate-controlled** (the
feed-rate dial + the cache), not variety-capped. The `entropy_ceiling`/cap is
now **inert for growth** (kept for the viewer's variety display); only the
dedup/vague guard limits it. The old entropy/temperature slider is repurposed:
**FEED RATE** (0–32/cycle). The story `temperature` is fixed at 0.7 server-side
for a cohesive chronicle.

### Self-author stripped mode — the cell speaks for itself

`--self-author` takes the LLM out of the live loop entirely: every turn (act
AND escalate) is the cell speaking from its own memory, no model call, nothing
learned (no teacher). The field is coupled to the *user*, not the model — your
re-ask is the only channel back in, and it demotes the cell that spoke (the
nearest memory the speech was conditioned on), so the cells respond to you.
Pair with `--no-judge` (StubJudge, so demotions stand with no model call).
Warm-up is the one place the LLM may still be used (to seed).

The SPEAK organ (`runtime.Membrane.generate`, selected by `--speak`):

- **`recall` (default, cohesive)** — the cell RECITES its most relevant lived
  scene: the nearest stored action to the query (kNN). The field is grown from
  cohesive LLM-written chronicle passages (~50–80 words), so the nearest
  neighbour IS a cohesive, on-topic story. Legacy telegraphic fragments
  (`< _COHESIVE_MIN_WORDS`=15; early stub/warm-up junk) are SKIPPED — they were
  the "gibberish". This replaced the word-trigram fragment, which was locally
  fluent but globally rambling and capped at `_MAX` tokens.
- **`ngram` (opt-in)** — the Fork-C word-trigram: a fresh stochastic draw from a
  trigram trained on the field's own stored actions, conditioned on the nearest
  memory's lead. More "generative" but incoherent past a few tokens; kept as an
  escape hatch.

Determinism holds in both modes (a query maps to a fixed nearest passage / a
query-seeded RNG), so a re-ask produces the SAME output and the re-ask detector
demotes the seed — the green→red→green self-correction arc inherits. In
practice every chat turn is an `escalate` verdict (the nearest memory sits at
sim ~0.69 < `sim_min` 0.85, so the gate escalates; self-author turns that
escalation into speaking from memory). The `act`/absorb path is dormant in
stripped mode and would speak identically.

### The live 3D viewer — explore the field as a graph

`--viewer` serves `membrane/viewer.html` (a Minecraft-styled Three.js cosmos)
and `/state` (a JSON snapshot read in-process from the live field). The gate
path is untouched — the page chats through the real `/api/chat`, same-origin.

- **Cosmos** — stars = exemplars, PCA-projected to 3D (green safe / red
  demoted); drag to orbit, scroll to zoom. The field's geometry rendered as
  space.
- **Menu (Esc / ⚙)** — all world controls: ceiling, feed-rate, CPU target,
  PLAY/PAUSE (their clock), time-skip, the legend, the activity log (the
  verdict ticker: escalate = new territory, absorb = ground it knows).
- **Story console (backtick)** — the chronicle scenes being fed live, the
  world's own thread, tagged fed/skipped.
- **Date/time/cycle HUD** — `Day N · HH:00 · <phase> · cycle C`, derived from
  `consolidation.cycles` (2h/cycle, 12 cycles/day; phase icon for the
  night-market's midnight lamps). A paused world freezes the clock; PLAY moves
  it. (Cycle resets on proxy restart; the field/cells persist.)
- **Neighborhood panel (`/neighbors`, press H)** — the exploration instrument.
  `GET /neighbors?q=<text>|idx=<n>` returns the top-8 nearest exemplars
  `{idx,sim,safe,words,recalled,action}` (read-only on the store; `recalled`
  flags the one the SPEAK path would recite). After each chat send the panel
  renders the top-8 as clickable rows (dot, sim, snippet, sim bar); click a
  row → re-render with *that* memory's neighbours → walk the field's
  association graph instead of getting one answer. Each chat reply is annotated
  with the recalled `sim` (the strength of association) and whether it's
  demoted. The viewer hot-reloads (`Cache-Control: no-store` + mtime re-read).

### Launch

```bash
# a self-running, self-authoring, explorable world (the game is Rick & Morty;
# gemma3:12b writes the chronicle, mxbai-embed-large for embeddings).
# --protagonist writes YOU a character to be: Rick. --character seeds the
# ensemble around him (Morty). --system is the world premise the LLM narrates.
PYTHONPATH=. .venv/bin/python -m membrane.proxy --listen 8770 \
  --backend http://127.0.0.1:11434 \
  --system "The dimension-hopping misadventures of Rick and Morty. Rick is a young, broke, bitterly brilliant garage scientist who builds portal technology from salvaged junk and runs cons across realities to keep the lights on; Morty is his anxious fourteen-year-old kid brother, dragged along on every scheme. They portal between fractured worlds — dead planets, weird markets, broken civilizations — barely escaping what Rick's hubris stirs up. Dark, fast sci-fi: cynicism and wonder tangled, danger played for gallows humor, affection buried under contempt. Narrate their adventures; Rick drives, Morty worries, things go sideways." \
  --warmup-model gemma3:12b --entropy-ceiling 0.05 \
  --self-author --no-judge --viewer \
  --protagonist "Rick — a young, broke, bitterly brilliant garage scientist who builds portal tech from junk and runs cons across dimensions to make rent; nihilistic and grandiose, drinks to keep the quiet out, buries affection under contempt — clipped, rapid, sarcastic sentences; names the ugly truth nobody asked for; a running bitter mutter to himself; never admits he cares" \
  --character "Morty — Rick's anxious fourteen-year-old kid brother, dragged into every scheme; earnest, stammering, the one with a conscience — hesitant, runaway sentences that trip over themselves; asks the question Rick won't; brave only when someone's about to get hurt" \
  --field worlds/story-field.json --audit worlds/story-audit.jsonl
# then open http://127.0.0.1:8770/viewer and hit PLAY (or add --consolidate
# --feed-rate 4 --cpu-target 0.3 to arm the world's clock at boot). You ARE Rick:
# your words render as Rick's thoughts, the narrator speaks from Rick's lived
# scenes, and Morty is the co-lead the chronicle follows. The world is about them.
```

What it is, honestly: a **self-growing memory field you probe by association,
walk as a graph, and steer by pruning** — not a chatbot, not a game, not a
search box. The cell does not reason; it recites the memory most semantically
similar to your words, and the `sim` is the honest version of how "right" that
feels. The LLM grows the content (the chronicle); the field supplies the
structure (which memories associate, what survives your pruning). It grows
into a *bigger memory*, not a *smarter world* — turning drift into drive
(state + causality + an intrinsic thermodynamic goal) is the next build.

### The character ledger + state — the "spine" (landed)

The world grows a bigger memory, but until now it had no persistent *entities* —
no people, no state, no causality. The first increment toward turning drift
into drive is a **character spine**: the chronicle's scenes tag to *characters*,
and each character carries a persistent `state_vec` — an EMA of the scenes
tagged to it, the tip of its walk through the field.

A character is **not a row in a table; it is a walking region of the field.**
The scenes are real exemplars (a new `Exemplar.tags: list[int]` records which
characters a scene belongs to); `state_vec` is the EMA of a character's tagged
scenes. State lives in the field — the ledger (`membrane/cast.py`) only makes the
cast *addressable* by name and tracks each character's rolling state.

- **Geometric tagging** — a scene is tagged to every character whose `state_vec`
  it sits close to (cosine ≥ `admit_threshold` 0.40). Each tagged character's
  `state_vec` EMA-walks toward the scene (`decay` 0.6). There is **no
  auto-admit**: a scene far from the whole cast is left untagged, so the cast
  stays short (capped at 12; admission is seed-or-manual only).
- **Genesis seed** — the *first* chronicle chapter also emits a `cast:` block
  naming the ensemble (3–6 characters). One batched embed admits each, then a
  one-time `retro_tag` colors the *entire* existing field by character
  immediately. Continuation chapters tag geometrically thereafter; new
  characters are menu-admitted only (the cast never balloons).
- **The roster (Esc/⚙ menu)** — an RPG-style cast list: each row shows the
  name, `scene_count · last_cycle`, and a colour dot; **Retire** removes a
  character (its stars revert to neutral — its id is stripped from every
  exemplar's `tags`) and **Pin** protects one from accidental retire. Click a
  row → the camera flies to that character's region of the cosmos. `POST /cast`
  (`{action: retire|pin|admit, id, name?}`) is the menu's endpoint.
- **Cast marks on the map** — each character's projected `state_vec` is marked
  and labelled in the world map; stars are tinted by their `tags[0]` from a
  12-colour palette (demoted tagged stars dim). The cast persists in a sibling
  `-cast.json` file; old field files load cleanly (legacy exemplars default to
  `tags: []`).

This is the **spine only**: it lets characters emerge and walk. The arc it
unlocks — state spine → **consistency gate** (acts must be on-manifold with the
acting character's state) → **cells act** (the world authors its own history by
sampling from a character's region) → **thermodynamic drive** (steer genesis
toward the starving character region) — is the next build, on top of this
addressable state.

### The narrator — a voiced POV character (you are a character in the book)

The spine named the cast, but the chat still read "you (green) talk to the cell
(white)" — no one was *in* the world. The narrator makes the chat a **novel**:
you *are* a character (a protagonist with a defined voice), your typed messages
render as that character's **thoughts**, and the narration speaks *from that
person* — in `--self-author` mode by reciting the scenes that character **lived**
(the field memory tagged to them), on the LLM-escalate path by a system-prompt
reframe in their voice.

- **A character with a voice** — `Character` gains `role` and `voice` (how they
  speak/think, in a phrase). The genesis SEED now authors `Name — role — voice`
  per character and designates a **protagonist** (the reader's character) with a
  leading `★`; the seed admits the voiced cast and sets the protagonist as the
  POV. You can also inject one with `--protagonist "Name — role — voice"` (admits
  at boot if absent, sets POV, retro-tags the existing field so the protagonist
  colors it at once).
- **The narrator speaks from that person** — `generate()` (the self-author
  SPEAK organ) restricts recall to the POV character's **tagged** exemplars when
  a POV is set, so the recited scene is one the protagonist *lived* — the
  narration comes from their region of the field, which *is* their voice. It
  falls back to the global nearest when the protagonist has fewer than three
  tagged scenes (they haven't lived enough yet). On the LLM-escalate path the
  system prompt is reframed: "you are the narrator; the reader IS {name}; their
  messages are their thoughts; narrate in third person in {name}'s voice."
- **The chat reads as a novel** (viewer) — when a POV is set, your messages
  render as the protagonist's **thoughts** (italic, indented, `❝ name · …`); the
  reply renders as **narrator prose** (a left border, no attribution). The chat
  header shows the protagonist's name; the input prompt becomes *what does
  {name} think?*. The cast roster gains a **POV** button per row (POST `/cast
  {action:"pov",id}`) and a `★ POV` badge — click it to switch whose eyes the
  world is seen through. No POV → the chat stays green/white (unchanged).
- **Persistence** — `pov_id` rides in the `-cast.json` wrapper
  (`{pov_id, chars}`); old bare-list cast files load with `pov_id=None` and
  `role`/`voice=""` (backward-compatible).

This is the **narrator** only — a voiced POV you *are*. The consistency gate
(acts must be on-manifold with the acting character's state) and full
cells-act (a cell authors new beats, not just recites its lived scenes) are the
next build on top of this.

### The ensemble (`--character`) + save / load / new world

- **`--character "Name — role — voice"`** (repeatable) admits a character to the
  cast at boot *without* setting them POV — it seeds the ensemble around the
  protagonist. `--protagonist "Rick — …"` makes you Rick; `--character "Morty — …"`
  seeds his co-lead. Both run before warmup/genesis, so `retro_tag` colors the
  whole cast into the existing field at once. Seeding the cast at boot also
  means genesis **skips its own cast block** (the cast is already non-empty) and
  just narrates the chronicle grounded in `--system` — so the cast is exactly the
  authored characters, not LLM-chosen.
- **Save / load / new world** (viewer's WORLD menu): `SAVE` copies the current
  field + cast + audit to a named slot under `worlds/<name>/`; the `LOAD` list
  (fetched from `GET /worlds`) swaps the live store + cast in from a saved world
  under `feed_lock` (so it can't race the consolidator) and resets the rolling
  chronicle; `NEW` empties the field + cast + audit to a blank slate. The
  endpoints are `POST /world {action:"save"|"load"|"new", name}`; the slot name
  is sanitized (no path traversal). `--worlds-dir` (default `worlds`) sets the
  directory. A loaded world is persisted to the live paths, so a restart keeps it.

### The I as ground — the POV state-walk

The narrator says "you ARE the character" — but until this, the user's thoughts
didn't actually shape the character: they were cosine-gated like every other
scene, so the **gardener's** scenes defined the character, not the user. The
I-grounding closes that gap. When a POV is set, each of the user's chat thoughts
**unconditionally** EMA-walks the POV's `state_vec` toward it (`Cast.walk_pov`,
wired into `gate_turn` — the one mode-independent point every message passes
through), bypassing the cosine `admit_threshold` that gates every other scene.
The user IS the POV character, so their thought moves the character *by fiat*,
not by similarity. **The field follows the I; it doesn't check it.**

No exemplar is appended (no recall loop, no echo of the user's own words), no
model call — one cheap EMA update per thought. The effect: the POV's region
tracks where the user steers; the gardener's future scenes accrue there; the
narrator speaks from there; the viewer's character pin moves as you think. This
is the foundation of **replacing an LLM consistency gate with the I-grounded
field** (reasoned out in `../cortex-experiment/membrane.md`): the model's
"would this character do this?" is a *generic, parametric* character — not your
specific, voiced one — so it would flatten your character back toward the
archetype. The gate, when it comes (for authored acts), measures acts against
*your* character's manifold — the I-shaped state — not the model's. The model
stays the gardener; the user is the ground.

---

## The math findings

`docs/math-findings.md` is the full narrative of the three experiments (real
embeddings → rank-growth → consolidation) and the self-correcting conclusion.
See also the experiment files in `experiments/`.

---

## Run the toy

```bash
cd /Users/dmitrii.chechetkin/projects/membrane
pip install -r requirements.txt   # numpy only
python experiments/variety_toy.py
```

Expected: the low-variety store has low effective-rank and low spread; a query
off its manifold reads as highly novel (gate escalates), while the same off
query against the high-variety store reads as in-manifold (gate acts).