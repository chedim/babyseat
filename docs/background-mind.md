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
| **Self loop** (#161) | change-driven, ≥ 10 s apart, off by default | `self` (`self_loop.py`): try → expect → notice on its own position; the operator's switch; see [The self loop](#the-self-loop-161) |

All cadenced organs use the idle local model for any model calls they
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

An intention is never auto-closed by similarity — only `fulfill` closes it
(goals carry no TTL; a loop that landed the work closes it with the
receipt). Without an open intention the dreamer skips (unsteered dreams
confirm near-randomly — a measured lesson). The MCP tools `intend` /
`intents` (and an entity script's `{intent: …}` verdict) plant through the
same lane; every plant is an `intent-planted` event the thinking loops
RECEIVE as their next turn's message — the operator creates, the thinker
receives and executes. The model-facing teaching lives in the doctrine +
the `membrane-intents` skill.

## The pause (the operator's stable point, 2026-10-06)

`membrane: pause` stands the seat's ACTING lanes down — the stable point
from which the operator (and the model, read-only) can look around and
plan:

- **Stops**: the thinking loops (listener admits hold — the events pool
  keeps, bounded by its drop-oldest cap), the dreamer, the thinker's
  ticks + digest + idle-think (the queue accumulates under its cap), the
  NightClock's bodies (scripts/births/consolidation defer; the cadence
  continues so resume is automatic), the autonode (the file-tree diff
  accumulates silently; resume fires it as a bounded 64-burst), the claim
  + A/B idle judges, all composed speech (held; delivers on resume).
- **Keeps**: presence (a paused seat is ALIVE to the roster — identity
  metadata, not acting), capture (the operator talking is memory), the
  gate/recall reads, and `membrane: pause`/`resume` themselves.
- **The entity hands are down**: mutating MCP tools (`entity_add/edit/
  remove/link/unlink`, the filetool lane) refuse with 423 + the pointer,
  from every session. Reads, memory (`learn`/`forget`) and `intend` stay
  live — and a plant mid-pause is **inert by construction**: every lane
  that would react to the goal is down too. The intent board is the
  plan's artifact: the operator looks around, plans into the board, then
  `membrane: resume` — and the accumulated world pours in honestly
  (loop drains, speech delivers, the scanner's diff bursts bounded). The
  executor's queue is part of the pause: owed work keeps its pending
  edges and runs on resume (the executor mechanics:
  [entity-field.md](entity-field.md) — the queue + the ISA pipeline).
- **RAM-only**: a restart unpauses (a persisted pause is a stuck seat —
  a paused-looking seat the roster can't wake is the failure mode this
  avoids). `membrane: pause` is honored only from the operator's chat
  (a listener loop can never pause the seat). Kill switch:
  `MEMBRANE_NERVE_PAUSE=0` (pause becomes a no-op).

The banner carries one `PAUSED:` line while paused — the model sees why
the field's voices are quiet and that its writes are refused.

## Programmatic entities (the scripts — model-authored)

**Scripts are the model's to write** (the doctrine teaches the lane; the
MCP `entity_edit` is the pen). They run **off-turn** in the night
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
writes. Per-entity cooldown, 3-strikes auto-disable (retried on a 60s→1h
backoff; a passing retry re-enables; reads carry the failing state), every application
audited. A script's change to an entity emits an event, so the normal-mode
**state diff** (SINCE YOUR LAST MESSAGE) shows it next turn.

## Births, junk, activity — reading the field's own measures

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

## The self loop (#161)

`membrane::self` is an unnamed looped cliffhanger object, the seed of the
evolving self in #161. It grows up a **skill ladder**: one rung per PR, each
with a measured pass test; a rung opens by itself when its test passes. This
section covers what is built (`membrane/self_loop.py`).

- **Rung 0, sleep and wake, and the growth limit.** A per-seat chooser, **off
  by default**, set only by the operator: the 🌱 chooser (the home top bar,
  and the chat's side menu) → `POST /membrane/seat/control {"self_limit": n}`
  (`seat_panel.control`). The chooser picks **the top rung self may reach**:
  - below the limit, self still earns each rung by its test, in order;
  - above it, nothing opens however well it does: the chooser marks such a
    rung "ready, held by the limit", and the bar says it waits on "the limit";
  - `0` is off, and raising the limit never grants a rung.

  `{"self": "on" | "off" | "watch"}` still works. A seat switched on before
  the limit existed gets limit 1. Off runs
  nothing and erases nothing. The mode (`<data dir>/self/mode.json`) and the
  record survive a restart. `watch` runs like `on` until there is a
  babysitter to keep quiet.
- **Rung 1, feel itself (the amoeba's problem).** Its only act moves its own
  position on the field, and the world pushes it while it moves: the field's
  **current** (`EntityField.current()`), which self does not control. Every
  session that spoke lately pulls at once toward its own context: one unit of
  pull per event, fading with a 3-minute time constant (`CURRENT_TAU_S`). So
  busy streams pull harder, quiet ones fade out, and a lone write (a getter
  reading, a tick) nudges the current but can't flip it. `expect` says beforehand where it will
  end up: its own step plus the push it has learned to expect there. It
  learns that push from its own moves as `push ≈ b − g·position`, kept in its
  own model file (`<data dir>/self/model.json`; its `script_data` shows the
  learned pull strength only, because entity properties cap lists at 128 items
  on save). `notice` sees where it actually ended up. **The gap is the
  signal:** it closes as self learns the current and reopens when the
  conversation turns.
  - The test: over the last 20 pushed cycles, `1 − gap/naive ≥ 0.5`, where
    *naive* is the gap with no experience at all.
  - In a quiet seat (no current) there is nothing to feel, and the bar says
    it is waiting on the world.
- **Rung 2, first reach (the world's tempo).** Open once rung 1 has passed and
  the limit is at least 2. Each cycle, self also reaches for one thing that
  has a getter: reading it fires the getter (`watch_agents.entity_read_back`,
  `by="self"`).
  - **Before reaching**, self predicts whether the reading will have changed
    since it last looked: its smoothed share of reaches that found the thing
    changed. So it learns each thing's tempo at the pace it looks.
  - **After reaching**, `notice` records changed, unchanged, or **no answer**
    (throttled, held, errored, no reading). No-answers never count as the
    world's.
  - **The test:** for one thing, over its last 20 answered reaches, the Brier
    skill against the seat-wide change rate is at least 0.2. Then a **blind
    audit**:
    - membrane pauses that thing's getter (`pause_getter`) until self has
      reached for it 3 more times;
    - self isn't told: its reaches answer "unchanged", and it learns nothing
      inside the window;
    - the audit passes only if no change was recorded while the source was
      stopped.
  - **A still thing can't be audited** (it must change on at least 60% of
    looks), so it can't carry the rung: it can't show that a mark could fail.
  - **What passing means:** "I can tell which things move on their own, and
    my marks of their moving are real." That is the world's tempo, not yet a
    body.
- **Its methods run only in its own loop.** `try` / `expect` / `notice` are
  cliffhanger methods (`self::` no-shell dialect), and so is `reach`. `exec inspect` shows them;
  any other caller, the model included, is refused. The LLM talks *to* self,
  never *as* it. `entity_edit` can't write its `script_data`, and mentions
  can't move it (a one-segment `m:` name never gets a field state).
- **The record** is `<data dir>/self/record.jsonl`. It is append-only and
  never truncated, and it starts at birth (the first switch-on): scalars per
  cycle (step, push, gap, naive, the kind of change), errors, and each rung
  opening. Nothing before its birth is on it.
- **The runaway guard.** Each cycle's change wakes the next, at least
  `PACE_S` apart (`MEMBRANE_SELF_PACE_S`, default 10 s, floor 1 s) and at
  most 30 a minute. Three failed cycles in a row hold the loop until the
  operator's next toggle. A paused seat runs nothing.
- **On the viewer.** Each cycle is drawn in the 3D field viewer as an amoeba:
  - **green**: from where it started to where it meant to go (its own push);
  - **orange**: on to where it ended up (the world's push);
  - **red**: the gap from where it expected to end up (brighter when it is
    surprised);
  - **violet**: where it has learned the current pulls;
  - its trail.

  The seat places the points into the open projection (`fieldpulse`'s
  `self.cycle` frame: cube coordinates and numbers, never vectors or text).
- **The exp bar.** The panel shows the rung and a bar toward the next one.
  - The bar is the **weakest** criterion of the next rung's test, named
    ("waiting on: skill").
  - It can go down: a passed rung stays on the record, and the bar shows it
    slipping while its test fails.
  - Only the cycles' recorded gaps feed it.
  - It moves with each cycle: every cycle's `self.cycle` frame carries the
    numbers, viewer open or not.

## Kill switches (one per organ)

```
--no-thinker       # no background deliberation
--no-dreamer       # no timeline generation
MEMBRANE_EFIELD=shadow | off   # events-only capture / the field off
MEMBRANE_LANE_GATE=0           # the flat tool surface (no operator/listener split)
MEMBRANE_NERVE_PAUSE=0         # pause becomes a no-op
(the self loop)                # off by default; the operator's 🌱 switch
```

## The goal loops' rhythm (2026-10-08)

The cost review (116 loop runs, 2056 tool calls in 8 h, all cloud) set
these rules (`membrane/watch_agents.py`, pinned in
`tests/test_watch_cost.py`):

- **Waking.** A goal wakes when it is new, or when the board's pulse
  (every 5 min, idle-only, 3 per beat) finds it stale: 30 min since its
  last wake, doubled for each trailing pass that wasn't a STEP (capped
  ×16). A boot wakes only what the pulse would; it no longer re-fires
  every open goal.
- **The verdict.** Every pass ends with one line: `DONE <receipt>`,
  `STEP <what moved>`, `BLOCKED <on whom>` or `ignored: <reason>`. A pass
  that ends with no text is `aborted`, and its last tool calls ride into
  the next wake. `DONE` with the goal still open gets one "call fulfill
  now" nudge. `BLOCKED` parks the goal until the operator next speaks.
- **The budget.** About 8 tool calls per pass. The next wake carries the
  whole goal text and the last three landings.
- **The gate.** Only a NEW plant (and its landing) skips the alert budget.
  Rewakes and boot wakes pay like any watch event.
- **The close.** `fulfill` takes any spelling of the goal's id
  (`membrane::goal:<id>`, `m:goal:<id>`, `goal:<id>`, `#<id>`). An
  id-shaped spec never name-matches another goal, and a closed goal
  answers "already fulfilled".

