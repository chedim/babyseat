# Alzheimer's, viewed from membrane

> Membrane is a **memory proxy** — a loop that sits between an AI client and
> the model server and gives the model continuity: a per-entity field of
> what has been seen, a bounded replay, and a **self** that runs the
> prediction-error loop `try → expect → notice → gap`. This page records
> what that design lets us *say* about Alzheimer's disease. It is a
> **viewpoint**, not a model: no clinical data was fit, no structure was
> measured, and nothing here is a medical claim. It earns its place by
> being honest about what it borrows and what, if anything, is its own.

The self loop is documented in [background-mind.md](background-mind.md); the
ownership → predictability thesis (efferent, not afferent) is the spine of
issue #161.

---

## The one principle: the hippocampus is the loop, not the store

Complementary Learning Systems (McClelland, McNaughton & O'Reilly, 1995)
split memory into a fast, pattern-separated, one-shot learner and a slow,
overlapping generaliser, and argue one substrate cannot do both without
catastrophic interference. The usual reading is "hippocampus = the fast
store, cortex = the slow store." Membrane reads it differently:

- **the cortex is the store** (the weights, the durable generalisations);
- **the hippocampus is the *converter*** — the loop that takes the present
  and writes it into the store, and replays the store back into the
  present.

The hippocampus is the write path and the replay path between now and
durable memory. It is not itself where memory lives.

## The self, in components

To say what Alzheimer's breaks, first say what membrane's self *is*:

| Component | What it does |
|---|---|
| **record** | the episodic trace — what happened, as it happened |
| **expect** | predicts the next observation *before* it arrives |
| **notice** | observes what actually arrived |
| **gap** | the prediction error — the only learning signal |
| **body map** | the forward model of the self's *own* action → consequence (`push ≈ b − g·position`), not the world's independent dynamics |
| **ownership** | what the self can predict **by acting** — the efferent forward model, not what it merely predicts by watching |
| **rate fit / kinds** | the slow temporal + structural generalisations (the cortex side) |
| **voice / LLM** | the rented renderer — the cortex, not the machine |

The self is not the record. The self is the *loop*, and the loop is the
gap: **a motion must be able to fail** (see `docs/motor-channel.md`), and
the residual must stay informative — not zero — or learning dissolves.
"Ownership → predictability" is the Good Regulator theorem (Conant &
Ashby, 1970) read the right way round: every good regulator of a system is
a model of that system, and ownership is the model of the consequences of
its *own* actions — the efferent forward model. You *are* what you can
predict **by acting**, not what you can predict by watching. (You predict
how the blanket moves; that does not make the blanket yours. The gut is
yours and you cannot predict it at all. Predictability is the *symptom* of
ownership, not its cause.)

## The reframe: Alzheimer's loses the loop, not the store

Read the disease backwards from that principle. Alzheimer's is staged by
Braak: entorhinal cortex → hippocampus (CA1, subiculum) → neocortex. The
clinical signature follows the same order, and the earliest symptom is
**anterograde amnesia with remote memory preserved** — H.M., progressive
(H.M. himself, viewed from membrane: [patient-hm.md](patient-hm.md)).

The standard reading is "the memory store is being destroyed." The
membrane reading is more specific: **the converter fails first, the store
is intact.** The observed order — cannot form new episodes, long before
old ones are lost — is exactly what a broken write path predicts. You
cannot write new memory because the loop that writes is gone; the shelf
behind it still holds everything that was already written.

## Early stage, in membrane terms: the write head breaks

Concretely, the first lesion is in the **consolidation write** — the step
from `notice` to `record`. What stays intact and what fails:

| Lane | Early Alzheimer's |
|---|---|
| `try` / `expect` | intact — perception and prediction still run |
| `notice` | intact — the world is still observed |
| `gap` | **computed but dropped** — the error is felt, not recorded |
| old `record` | intact — the past is still legible |
| `body map` / `kinds` / `voice` | intact — the old self still answers |

Four behavioural signatures fall out, and all four are the observed
disease:

1. **Repetition.** The same observation re-derives as if new, because the
   write that would have made it "already seen" failed.
2. **No new ownership.** The body map stops growing — the forward model
   stops consolidating new actions, so new doings never become owned. The
   world stays predictable (the afferent model is intact); what fails is
   the *efferent* write that turns "I did this" into "this is mine."
3. **Surprised, but unchanged.** The gap is computed and dropped — the
   person is startled by the mismatch, then stays exactly as they were.
4. **The old self is intact.** The deficit shows only in *new* situations;
   in familiar ones the person is the person they always were.

## Progression

The write-head failure is not the whole disease, just its first motion:

1. **Write path fails** → pure anterograde loss; the old self intact.
2. **The consolidated structures degrade** → retrograde loss follows, and
   the familiar place becomes strange ("lost in a known street").

Ordering matters: the loop goes down before the shelf is touched. The
shelf's contents then decay *because the loop is no longer reading and
re-writing them* — disuse, not deletion.

## What this points at

The reframe re-aims the research, if it holds:

- **Treat the loop, not only the store.** A drug that protects the
  substrate is necessary but late; a prosthesis that *supplements the
  write path* (external replay, rehearsal, the converter re-implemented
  outside the dying tissue) targets the first thing that breaks.
- **Measure *efferent* grip, not only recall.** Recall fails late. The
  loss of *agency* — the shrinking set of consequences the person can still
  predict from their own actions — precedes the loss of content. That is
  the earlier, functional signal: the forward model stops growing before
  the record is gone.
- **Detection, not diagnosis.** Structural atrophy is a late biomarker.
  "Loss of efferent grip before loss of content" is a candidate for an
  earlier, behavioural window.

## The honest boundary

- **This is a reframe, not a cure, and not an instrument.** membrane does
  not model, measure, or treat the disease; it is a memory proxy with a
  prediction-error self.
- **The structures are already known.** Braak staging is not new. What
  membrane adds is not an anatomical discovery but a *reading* of the
  known order.
- **Most of the theory is borrowed.** CLS, error-driven learning (O'Reilly),
  predictive processing, replay — all established. The one synthesis that
  is membrane's own is putting **ownership → predictability** on top of
  them: the self as "what you can predict *by acting*," and Alzheimer's as
  the disease that stops the efferent write — new doings cease to be owned —
  before it empties the afferent store.

That synthesis is the thing worth a glance, and worth a challenge. If the
prediction-error loop is a fair picture of the converter, then the early
disease is not "forgetting" — it is **losing the ability to be changed by
what you do**, while remaining exactly who you already are.
