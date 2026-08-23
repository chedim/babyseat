# Variety, novelty, and the gate — the math findings

> **Phase note.** This is the **substrate-phase** math — the gate/variety work
> that was membrane's *original* identity. membrane has since become a
> **field-first, self-authoring world you inhabit as a voiced character**; the
> variety math below did not get discarded, it became the **thermodynamics of
> the world's growth** (`effective_rank` = the world's life, `free_energy` = its
> vitality). See `README.md` for what membrane *is* now; this doc stays the
> honest record of what was measured and settled in the substrate phase.

This documents the math we ran in `membrane` and what it settled. It is a
narrative of three experiments, one of which **corrected an earlier one**, and a
final conclusion that is honest about being self-correcting. No formal-math
background is assumed; every term is explained when it first appears.

The one question that started all of it, from the README:

> **How do we measure "variety" for the Membrane process?**

Variety calibrates the confidence gate. A field that has only experienced a few
kinds of situation cannot be *sure* about a query off its experienced manifold,
no matter how close the nearest cosine match is. "Measure variety" is the thing
that turns a brittle cosine threshold into a calibrated `not-sure` signal.

---

## The measures (in `membrane/variety.py`)

Three quantities, all built on the exemplar field (a set of past
`situation → action → outcome` judgments, each with a local embedding vector).

**`effective_rank` — the field's variety.**
Picture the field's exemplars as points in space. If they all lie along one
line, the field has one real axis of experience — low variety. If they spread
evenly in every direction, it has many — high variety. `effective_rank` is the
Shannon entropy of the variance spectrum: one dominant axis → ~1; variance
spread flat across many axes → ~the number of axes. It is computed from an SVD
of the centered embeddings (so it scales to the 1024-d real vectors without
forming a 1024×1024 matrix). Low effective rank → low variety → the field
cannot be sure about anything off its thin slab.

**`novelty` — how far a query sits OFF the field's manifold.**
Take the field's principal directions (the axes it actually varies along). Keep
a fraction of them as the "experienced manifold"; the rest is the **null space**
— the directions the field has little experience in. A query's *novelty* is how
much of it sticks out into that null space, scaled by the null space's width.
High novelty → the query points where the field has no experience → escalate,
*even if its nearest cosine match is high*. This is the case plain cosine
misses: near a neighbor (high cosine) but off the field's shape (high novelty).

The under-keeping (`KEEP_FRACTION = 0.8`, keep 80% of the effective rank) is
deliberate and load-bearing: keep the *full* rank and the null space shrinks to
nothing and the measure blows up. The dropped 20% is what gives the null space a
real scale to measure against. This is "the null space is the asset."

**`spread` — a cheap proxy.** Mean pairwise cosine distance; a sanity
cross-check on `effective_rank` with no eigensolve.

---

## The filter cutoff — where absorb ends and escalate begins (`membrane/knee.py`)

A real workload is **trimodal**: a clear-routine mass (low novelty), a
borderline band (medium), and a clear-novel mass (high). The absorb/escalate
cutoff is the **first valley** — the low-density dip between "clearly routine"
and "everything above" (both borderline and clear-novel escalate, so the second
valley, between borderline and clear-novel, is irrelevant).

Two finders, one correct, one a trap:

- **`valley_knee`** (correct intent): the first local minimum after the first
  local maximum — the valley after the routine peak.
- **`otsu_threshold`** (the trap): max between-class variance; it assumes the
  data is **bimodal** and picks the *biggest* gap. On trimodal data it grabs the
  bigger gap — between borderline and clear-novel — and absorbs the whole
  borderline band (50% safety error). Otsu is kept in the code as the contrast.

The **reversibility guard** is the cutoff's risk-scaling: the valley is the
conservative anchor; **risky** actions hold/tighten it (escalate anything not
clearly routine); **safe/idempotent** actions loosen it into the borderline band
(absorb more, save tokens, accept that some signal gets absorbed — it is cheap
to undo).

---

## Experiment #1 — real embeddings: the (wrong) course-correction

`experiments/real_knee.py`. Toy vectors separated cleanly; the honest test was
real `mxbai-embed-large` embeddings of real code: a 15-snippet Java Couchbase-SDK
field, workload of more-Couchbase (noise) / other-Java-libs (borderline) /
Python-Go-SQL (novel).

Result (as first read): the field is **high-rank** — `effective_rank` 10.3 of 15
(0.69 ratio). The null-space novelty **collapsed**: compressed to a 4–10 blob,
noise/borderline/novel overlapping, no findable valley; `valley_knee` at 4.46 was
an edge artifact → 93% false-escalation. Plain cosine separated cleanly (noise
max-cos 0.86, novel 0.57; cosine filter FN 9% / FP 0%).

Conclusion as first drawn: **novelty is wrong for dense/high-rank fields; cosine
is the primary gate; novelty retires to a narrow low-rank specialist.** The gate
(`membrane/gate.py`) was rebuilt **cosine-primary**, with novelty as a *conditional
veto* firing only when `rank_ratio = effective_rank / len(field) < 0.5` (a
genuinely low-rank field). Validated in `experiments/real_gate.py`: noise 100%
absorbed, novel 100% escalated, borderline 20% absorbed (the real acquisition
gap).

This **re-derived rosetta-check's own tiering**: cosine default; the
covariance/null-space operator opt-in for narrow trained (low-rank) fields.

---

## Experiment #2 — rank-growth: does the domain ever saturate? (`experiments/rank_growth.py`)

If #1's high-rank field was just *too small* (each snippet a distinct concept),
maybe a larger same-domain field **saturates** — new snippets become variations
of covered concepts, `effective_rank` stops growing while N grows, the ratio
drops below 0.5, and novelty earns a general role. Or mxbai is inherently
high-rank and novelty stays a specialist forever.

Result: mxbai code embeddings settle to **`effective_rank` ≈ 0.6×N at every
granularity the corpus can test** — the whole-domain field (N=8→34: 0.74→0.59,
asymptoting ~0.59, never below 0.5) **and** single-operation sub-fields (find
0.60, transaction 0.64, index 0.62, … all > 0.5). Even 3 near-duplicate "find"
lines span rank 1.8. **Append-only growth never specializes** the field, so the
null-space novelty has no general role on situation fields by growth alone.

The honest home for the null-space operator is one place it was already landed:
rosetta-check's linter, where the field is **atomic code events** (a receiver
member access) — far more redundant, so the trained 20-file field *was* low-rank,
the null space *was* the asset, the operator hit 18/18 (in-sample). Membrane-core's
field is **situations** (rich, high-rank) where the operator doesn't earn its
place. Same math, different embedding unit, opposite verdict.

---

## Experiment #3 — consolidation: the self-correction (`experiments/consolidate.py`)

The user's intuition: *variance lets the field learn its shape; massage it
periodically with variance* — high-variance acquisition, then periodic
**consolidation** (sleep: compress accumulated experience into the essential
shape). A compressed core is intentionally narrow → its null space is large →
novelty's precondition holds. Maybe consolidation un-retires novelty.

The test: sweep how many principal directions the working field keeps (the
consolidation knob) on #1's canonical collapse case. **This produced a
self-correcting result.**

**#1's REASON was wrong.** "Null-space novelty collapses to a constant blob on
high-rank fields" was a **knee-finder artifact, not the metric**. `valley_knee`
grabbed a left-tail wiggle at 4.46 — *below the noise median* (5.23) — and
escalated ~all the noise (93% FP). The metric **does** separate: medians cleanly
ordered at every compression level (noise 5.23 < borderline 7.36 < novel 8.53,
no noise/novel overlap); an oracle cutoff at 6.43 gives FN 0% / FP 7% — as good
as cosine. **Same lesson as the Otsu trap: trimodal → the FINDER is the bug, not
the metric.** (I made that mistake twice: blamed Otsu correctly in the toy, then
blamed the *metric* wrongly in #1.) `valley_knee_robust` (first valley after the
first *significant* peak) was added; it improved 4.46→4.75 but still stops in the
intra-routine valley.

**But #1's DECISION (cosine-primary, novelty off by default) survives — for a
CORRECTED reason.**

- **(a)** The useful threshold (solo boundary ~6.1, or a novelty-veto ~6.5) is
  the *same* hard-to-find-unsupervised point: the routine mass is **internally
  multimodal** (a low cluster 4.2–4.7 and a main cluster 5.1–5.9) with an
  intra-gap (~4.9) as big as the real routine|signal gap. Every "first valley"
  finder stops inside the routine, not at the real boundary.
- **(b)** Novelty does **not** cleanly complement cosine. The acquisition gap
  (cosine's 9% FN) sits at **low novelty** (6.43–6.77), *overlapping the noise
  tail* (noise max 6.66), not in the clear-signal zone. A **high** veto (≥6.8)
  misses the gap (FN stays 9% = cosine alone); a **low** veto (~6.5) catches half
  the gap (FN 5%) but false-alarms noise (FP 7%) — a net *safety-weighted* win
  *only if* you can find that threshold, which is the same hard unsupervised
  boundary. The high-cosine-**off**-manifold case novelty was built to catch
  (the toy: sim 0.80 + novelty 9.38) does **not** materialize on real data: the
  real gap is **on-manifold-ish** (same-language-different-lib), not
  off-manifold.

**Net:** novelty is **un-retired as a signal** (it separates; it is not useless)
but **does not earn a general gate role** over cosine on real situation data.
Cosine-primary stands — now justified *correctly* (cosine already captures the
separable structure; novelty's marginal value is small; its threshold is hard to
find unsupervised; the gap overlaps the noise tail). Consolidation did **not**
change this: the raw field already separates; the obstacle is the finder and the
gap's overlap with the noise tail, not the field's rank.

---

## The settled conclusion (the math phase is done)

- **The gate is cosine-primary**: nearest-similarity + the reversibility guard
  (confident+safe → act; confident+risky → escalate-with-hint; not-sure →
  escalate). `membrane/gate.py`.
- **Novelty** is a real signal but a **narrow specialist**, not a general gate:
  conditionally useful on genuinely low-rank fields, and a marginal
  high-veto on top of cosine *if* a threshold can be set. It does not replace or
  clearly beat cosine on real situation embeddings.
- **Consolidation** (raw append-only store = the permanent audit/training log; a
  compressed *working field* re-derived periodically = what the gate reads;
  consolidate when the field stops being surprised — residual-entropy plateaus)
  is a sound architecture and stays in the design, but it is **not** what
  un-retires novelty — that was the finder bug.

The arc, in one line: the proposal imagined a fancy manifold-residual novelty
gate; the lab found it is a narrow-field specialist; cosine carries the general
decision; the one "it collapsed" finding was partly a finder artifact, but the
*conclusion* (cosine-primary) was right all along, now for the right reason.

---

## What is NOT math (the runtime, still open)

These are about *where Membrane lives and how it learns*, not the gate math.

**The proxy / substrate (decided).** Membrane is a **local proxy process** between
the chat window and the model server — the window used to call the model
directly; Membrane slides in, embeds each request, gates it, and either replays a
recalled action (no model call) or forwards to the model and logs the turn. The
field is a **local file the proxy owns** (local-first; a shared Core would be a
later move where several proxies point at the same store). In `membrane/proxy.py`:
the proxy gates `/api/chat` in **both** stream and non-stream modes — an absorb
synthesizes an NDJSON stream from the recalled action (no model call), an
escalation tees the backend stream to the client while accumulating the response
into the field; all other routes pass through. So any GUI/IDE Ollama client
(Continue, Open WebUI, …) pointed at the proxy drives the field for real. Loop
proven in `experiments/proxy_demo.py` (non-stream) and the streaming smoke test.

**The training loop (Tier 1 landed — passive self-train on re-asks; boundary softening
landed).** The decision loop (absorb/escalate) is built. The **training** loop is how the
*outcome* — was an absorb correct? — flows back. The key property: **it is passive.** You use
the chat normally; Membrane learns from how you naturally use it, with no extra labor. The
strongest outcome signal is a **re-ask**: asking the same thing again right after the field
*absorbed* (replayed) it means the replay was wrong. Membrane sees the re-ask because it is in
the path — no notification, no model call.

A re-ask is now a **vote, not a verdict** — the environment is not treated as infallible.
Each exemplar carries two evidence counters; `safe` is *derived*:
`safe = (reask_count - contest_count) < demote_threshold`. A re-ask increments
`reask_count` (the environment's usefulness vote); a contest increments `contest_count`
(the model's correctness vote — "my answer was right anyway, that re-ask was a
misunderstanding"). Each side checks the other; neither has a monopoly; both keep a veto.
Demotion accumulates (raise the threshold to require multiple re-asks) and is reversible
(a contest cancels a re-ask's vote and may restore `safe`). A re-ask also forces *that turn*
to escalate-with-hint (never replay the answer just rejected), even before the lasting
demotion crosses the threshold. The raw store stays permanent; only the evidence counts (and
the derived `safe`) change.

This is the safety-critical half: a wrong absorb is the dangerous `absorb_signal` error (a
bad answer with no LLM net), and the re-ask catches it the moment it hurts — so the error rate
is self-limiting, not compounding, even though the model's weights stay frozen. The field is a
self-correcting cache of how *you* use the model: cheaper and self-correcting, not smarter.

The proxy is stateful across turns (an in-memory ring of recent decisions; the durable evidence
is written to the field file). The audit log (`membrane-audit.jsonl`) records every decision
(`event: decision`) plus a re-ask vote (`event: outcome`) and a contest (`event: contest`),
each joinable by `ex_idx` and carrying the net evidence + the contest's source/restore flag.
The in-window review surface (`python -m membrane.review`) reads the audit + field and prints
**both sides** of the boundary — the honest "notify you in this window" mechanism, on demand.

The *model-judges-the-audit* half (Membrane calling a fresh model per demotion — the
"LLM-as-MCP-server" inversion) is **live**: `OllamaJudge` asks a fresh model "was the replayed
answer correct?" and CONTESTS or ACCEPTS. It is opt-in (default `StubJudge` no-ops, zero behavior
change); the demo uses a `ScriptedJudge` so it runs without a chat model. The anti-collapse rule
that keeps the boundary two-sided even with a live judge: the model's **first** contest restores
(one veto), a **later** contest is **record-only** (the environment re-asserted by re-asking
again = irreducible disagreement → environment wins the action, model wins the record). On any
judge error/timeout the verdict is ACCEPT — the boundary degrades toward the environment, never
toward the model judging itself unopposed. The passive re-ask self-train keeps the field honest
regardless.

**Bootstrap conservatism (open, now concrete).** A fresh field has no
exemplars → every query escalates (the "variance first" bootstrap) → the field
self-builds → routine queries start getting absorbed. `sim_min` starts
conservative (0.85, near-exact) and loosens once the audit proves replays are
safe. The demo shows this end-to-end with no chat model (a stub responder), so
the loop is runnable before any model is attached.