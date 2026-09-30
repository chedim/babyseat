# The background mind on the entity field

> Membrane is a **seat-local mind with a shared symbolic layer**. It
> perceives (capture → events), decides (the gate), sleeps (thinker /
> dreamer / the NightClock), speaks (planner-gated), and modifies itself
> (model-authored entity scripts). The foreground composer is rented from
> the LLM — the background mind is membrane's own.

The field's design lives in [entity-field.md](entity-field.md). This
document is the organs that read and write it in the background. What the
cell organism was, and the timeline-dreaming arc before it, is git history
(`070af25`; the `experiments/multi_*.py` rigs that imported the cells are
broken, kept as history).

---

## The clocks

| Clock | Cadence | Job |
|---|---|---|
| **Capture** | as turns arrive | the `Capturer` worker splits turns into events, writes them, embeds + applies them (membrane/capture.py) — every applied live event is a **stimulus** |
| **Idle hooks** | ~30 s of capture silence | the **claim judge** and the **replay A/B judge** run on the idle local model (`capturer.idle_hooks`) |
| **Thinker** | 1 s heartbeat, duty-capped | digests the latest stimuli (their entities as tags, an observation as the fn stamp) and eats them into its working memory; in silence, extends open timeline branches |
| **Dreamer** | 60 s idle waves | solution generation: proposes from the model's own recent sentences, vets a candidate by its **surprise** against the entity states (on topic, but not already said), commits quarantined `Timeline` entities under `session:now` |
| **NightClock** | 15 min | the entity scripts; **births**; **junk**; activity; the coherence null (once a day); entity-doc materialization + the overlay consolidation |

All cadenced organs use the idle local cortex for any model calls they
make (`--idle-cortex-model`), so a wedged background seat can never burn
cloud tokens.

### The thinker's working memory

`deque(maxlen=5)`, drop-oldest — **attention, not a backlog**. Feed hooks:
`on_applied` (every applied live event), dream confirmations, the night
pass (materializations), and every operator chat turn. Rendered in
`/membrane/state` as `think`.

### The thinker's voice (the authorship covenant)

The thinker may **speak on its own** — through `speak()`'s planner gate
(stay-silent is the learned default) — but never initiates a model turn or
a tool call. **Membrane proposes; the operator acts.** Delivery channels:
the peer push and the `[membrane-says]` marked block on the next
model-bound request.

## Reality's vote (the dream lifecycle)

`session:now` is the seat's lived present; every dreamed branch is an
ephemeral `Timeline` entity under it. Proposed from recent model sentences
(steered by open intentions — `membrane: intend that …`), vetted by topic
/ freshness / surprise, committed **quarantined and RAM-only**. A real
capture near a branch serving what it asked is **elected confirmed** and
may surface in the commons; a near capture serving nothing refutes it.
Unconfirmed branches expire past the horizon; one past two-thirds of the
horizon grows **hungrier** (`curiosity +0.2`, cap 1.0) — the mind's last
attention before expiry. The steering wheel:

```
membrane: intend that make the benchmark loop fast [for 2w]
membrane: intents          # the board
membrane: fulfill #N       # explicit close
```

An intention is never auto-closed by similarity — only `fulfill` or expiry
closes it. Without an open intention the dreamer skips (unsteered dreams
confirm near-randomly — a measured lesson).

## Programmatic entities (the scripts — model-authored)

**Scripts are the model's to write** (the doctrine teaches the lane; the
MCP `membrane_entity_edit` is the pen). They run **off-turn** in the night
pass and its boot-time echo (`run_scripted_entities(..., force=True)` runs
at boot — a script edited between boots does not wait for the next 15-min
tick); duty-cycled (`script_every_n`, default 4 passes) and per-pass
budgeted. Each entity's **delete script** runs first (retirement wins over
nudge, and deletes also unwatch the entity's track).

A script sees the entity's geometry as `state` plus a **read-only
`membrane` object** — no mutators, the authorship covenant (`membrane/
night.py::_script_membrane_api`):

    membrane.field.recall(v, k)         # nearest entities by state vector
    membrane.field.vitals()             # the field's vitals dict
    membrane.me.describe/velocity/direction
    membrane.entities… membrane.timeline… membrane.intents…

It returns `{delete?, render?, data?}` — **the engine applies**: the
banner fragment, the entity's own properties (bounded, `script_*` keys
untouchable), or retirement. Scripts analyze and return; the engine
writes. Per-entity cooldown, 3-strikes auto-disable, every application
audited. A script's change to an entity emits an event, so the normal-mode
**state diff** (SINCE YOUR LAST MESSAGE) shows it next turn.

## Births, junk, activity — reading the field thermodynamically

`membrane/efmind.py`, in the NightClock's pass (all thresholds constants
there):

- **births** — live entities crossing **mass ≥ 8** (accreting mentions)
  and **specialization ≥ 0.30** (their use drifted from the everyday
  seed): announced ONCE, only for kinds that make good subjects (files,
  models; symbols/materialization graduate silently).
- **junk** — words said **≥ 24 times** whose residuals still hold no
  direction (coherence ≤ 0.02): no shared sense, never a subject.
- **the coherence null** — recomputed once a day from random sentence
  sets; the bar every entity's coherence reads against.
- **activity** — what moved since the previous night, half-split.

## What this replaced

The **compactor** and the ephemeral tier, the exemplar-layer night passes,
and the cell organism were deleted with the exemplar field (git history is
the archive): distillation is a claim source (the `distilled` stream,
read-only), the field's states update **online** at mention time, and
`rebuild()` replays the `entity_deltas` log to the same states without
re-embedding. The night reads statistics, not the raw field.

## Kill switches (one per organ)

```
--no-thinker       # no background deliberation
--no-dreamer       # no timeline generation
MEMBRANE_EFIELD=shadow | off   # events-only capture / the field off
```