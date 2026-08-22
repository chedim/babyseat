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