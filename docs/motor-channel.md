# The motion record (#161, layer 1): what the seat has to write down for a body to exist

**Status: DRAFT for the 0.7 build (2026-10-15). Nothing below is implemented.**
The measurements are in the lab: `experiments/self_l1/` (`sandbox.py`,
`replay.py`, `density.py`, `probe.py` — each with its own tests), whose README
carries the raw tables. This page is the *design* those numbers dictate.

## The words

- **event** — "something happened": a sentence said, a tool call, a script's
  verdict. Written by `EntityField.write_events` (`efield.py:502`).
- **entity** — a thing the field knows: `f:docs/x.md`, `tool:exec`,
  `person:operator`, `seat:self`.
- **delta row** (the *pulse*, the *mention-row*) — one row per entity per
  event, written at `efield.py:1111` because the event **named** that entity
  (`_apply` loops over the event's mentions, `:1218-1234`). It says: this event
  was folded into this entity's memory at T. Nothing more.
- **the recorder** — the writing side: the code that decides a row exists
  (`write_events` + `_update`).
- **the ledger** — the reading side: `experiments/self_l1/contingency.py`,
  which sees only *(entity, time)* and asks whether the entity moves right
  after *my* acts.
- **a motion** — a row of a kind that does not exist yet: written only when the
  act produced an **effect that could have failed**, and carrying both the diff
  and the *kind* of change.

## What the lab measured

| rung | the question | the answer |
|---|---|---|
| 1 (`sandbox.py`) | can the ledger find a planted body in a toy world? | yes — with the MAD of delays, 1.00 recall, null clean, 0.97 stability across halves |
| 2 (`replay.py`) | what does a real seat's 10.2 h store do to it? | **76%** of the field's change pulses are owned by the seat's own reasoning/model turns; **52%** are literally *"this entity is named in that event"*. Naively replayed, the "body" is the act's own echo. With the act's footprint and the seat's voice dropped: body **0** at a 2 s and 5 s window, **1** at 20 s (`tool:shell`) — stable, null clean |
| 3 (`density.py`) | how dense must a motor channel be? | a planted answer is called body only when it answers **~3 acts in 5**: 582 machinery events on ONE entity over 10.1 h = **≈58/h** at 5 s (≈38/h at 20 s); below that it is only an *other*. Only **8 of 42** act kinds are eligible (`exec` 311, `entity_read` 262, `entity_edit` 53, `entity` 39, `intents` 30, `fulfill` 28, `state` 24, `entities_related` 20 — 767 of 970 acts) |
| 4 (`probe.py`) | what should a motion move? | not the act's **argument**: one pulse per act — 801 planted over 10.2 h, ~79/h — and still **no body at all** (40 others, each answering only its share of a kind, p_act 0.17-0.65). And not the act's **tool**: 8 body parts at p_act 1.00, z up to 3433 — a tautology we asserted, which the shuffled null does not catch either |

Two live facts from the same runs:

- **The seat has no body, and it does have others.** The map over its own store
  reads `body 0, other 23, world 1293` — and one of the others is
  `person:operator` (109.6 pulses/h, answering `entities_related` at p_act 0.65
  against a 0.035 baseline). L2's self/other split is already visible in data
  that exists today. What is missing is Layer 1's body.
- **The motion that is closest to a body is `tool:shell`**: 284 machinery-owned
  pulses in 10.1 h (**27.9/h**), answering **73%** of `exec` acts against a
  0.006 baseline. That is one notch under the body line (0.80) and half the
  rate the curve wants. Everything denser than it is *self-scheduled*: the
  entities moving 40-56 pulses/h (`m:field:vitals` 56.5/h, `s:?script_data`
  45.6/h) answer 4-17% of their acts — the pulse rate is not the contingency.

## The three authors

Every event and every pulse is written by one of three hands:

| author | share of pulses | may it answer an act? |
|---|---|---|
| the seat's own voice (its reasoning + its replies) | 76% | **no** — it is me talking, not the world moving |
| the operator | 9% | it is an *other*: the instrument's control case |
| the agent's own tool calls (the act's footprint) | 8% | **no** — it is the act's own shadow |
| the seat's machinery (ticks, jobs, watchers, getters, file landings) | 7% | **yes — this is where a motion belongs** |
| notes | 1% | — |

With the kind axis (rule 3 below) the machinery's 7% splits once more:
**knowledge-changes** (its own bookkeeping — ticks, read counters, its own
traces) and **world-changes** (a write landed, a command exited, a job
settled). Only the second can witness a body. The first is the seat's own
history with things, which is a different reading and also worth having.

## What counts as a motion

A **motion** is an event, authored by an entity's own machinery, recording an
effect — under seven rules:

1. **Authored by machinery.** Never by the agent's turn, never by the LLM's
   prose. The LLM emits words; words are mentions. A motion is a change.
2. **It could have failed.** The pulse is written only for an effect that could
   have failed *even though the act ran*: the file's bytes changed, the command
   exited non-zero, the job landed, the getter's reading differs from the last
   one. Bookkeeping the act performs on itself — a read counter, the tool it
   used — appears whenever the act runs, so it can never fail and can never
   answer anything. This is the rule the ledger *cannot* enforce (rung 4): a
   pulse we write unconditionally becomes a body part by construction, and the
   shuffled null will not catch it.
3. **It says which change it is.** `world` (the bytes, the exit code, the job
   landing) or `knowledge` (I read it, I looked, I learned it). Both are true
   and both are worth writing — they answer different questions: the world's
   answer to my act, and my own history with a thing. Today they are written
   **identically**, which is exactly why a *read* looks like the file answering.
   The kind is known at the write site; it is not a judgement about identity —
   *which entities are me* stays the ledger's inference (#161 asks for an
   evolving self, not a self-model).
4. **Causally keyed.** The motion event names the act it answers
   (`caused_by: <the act's event key>`), so attribution is exact rather than
   windowed. This is also what removes rung 3's artefact — p_act inflated above
   the true rate because the seat acts in bursts and a neighbour's answer lands
   inside this act's window (the stray rung 1's MAD exists for).
5. **Invariant, not argument.** A body part answers **every** act of one kind,
   so a motion should carry the state the act *always* touches — the seat's own
   object, a component's own state — not the file the act happened to read.
   (Rung 4: pulsing the argument gives the densest possible channel and no body
   at all.)
6. **No prose.** One small event + one pulse. No sentence, no terms, no
   embedding: the motion record must not feed the mention stream it is trying
   to be readable apart from.
7. **Rate.** ~40-60 motions an hour on one entity (rung 3), serving one of the
   kinds the seat actually repeats (8 of 42 today).

## Where a motion would be emitted

The seat's machinery already funnels through **one event writer**
(`EntityField.write_events`, `efield.py:502`) and **one pulse writer**
(`EntityField.flush` → `upsert_multi("entity_deltas")`, `efield.py:1396`). Every
delta row already names the event that produced it, in the field **`event`**
(`efield.py:1113`, `efield.py:1257`) — so the link *pulse → cause* exists and is
exact. What does not exist anywhere in `membrane/` is the forward link *event →
the act it answers*: no `caused_by`, no `motion`, no `motor` (`motion` appears
only as prose about entity geometry, `entities.py:480`, `night.py:290`).

So the record is not a new subsystem: it is a small change at the ~9 sites that
already write machinery observations.

| emitter | site (what it writes) | trigger / cadence | could the pulse fail? | the kind a motion would carry |
|---|---|---|---|---|
| **getter reading** | `consolidate.record_reading` `consolidate.py:149` — `observation, tool="read", session="readings"` | the getter fires, ≤1/min per entity | **yes, and it already does**: written only when the rendered reading differs from the last (`consolidate.py:131`) | the entity's own state — **this is the pattern the other eight should follow** |
| **job settle** | `consequences._publish` `consequences.py:256` — `observation, tool="consequence", session="consequences"` | daemon thread every 300 s, one event per settled horizon | **yes** — a record exists only when a read/write decision acted (`server.py:2241`) | **world** — what followed the decision; the one honest line the banner already quotes (`+ settled:`, `consequences.py:320`) |
| **stranger visit** | `strangers._field_line` `strangers.py:139` — `observation, tool="visit"` | login-page visitor, ≤12/min, 60 s per stranger | **yes** — no visitor, no line | **world** — someone arrived |
| **command landing** | `night.run_command_entity` `night.py:1016` — `observation, tool="shell", args={command}`, subject = the command entity | a `{run:true}` verdict, or the reaper batch (1/min) | **yes** — `outcome` already carries ok/failed, and the event is written either way | **world** — the exit code and the output diff |
| **file landing** | `night._file_land` `night.py:1130` — `observation, tool=read|write|edit|glob|grep, args={path}, outcome=ok|error`, subject = the `f:` entity | read / write / edit / glob / grep | **no on a read** (the counter increments whenever the act ran) / **yes on a write** (`outcome=error` exists today and is ignored) | **knowledge** on a read — my record of the file: `read_count`, `read_lines`, `last_read_offset`, `last_read_ts` (`night.py:1118`) · **world** on a write, *once it verifies the diff* |
| **tick mover** | `Thinker._tick_field_move` `thinker.py:517` — `observation, tool="tick", args={beat}` | the hum beat, movers every 60th | **no** — a clock | **knowledge** (my heartbeat) — until the trigger moves to *the entity's own state changing* (#161 build item 1) |
| **script verdict tell** | `night._script_tell` `night.py:800` — `discourse, role=note, src="script:<kind>"`, subject = the entity | every script verdict: tick / watch / getter / thought / dreamed / change / nudge | **partly** — a silent tick apply writes no tell (`night.py:4719`), a declined script writes nothing; but a tell that fires says only *the script ran* | **knowledge** — my own machinery's report; **world** only when the verdict itself saw a change |
| **method call** | `night.py:3029` — `observation, tool="call"|"read", args={object, method}` | an exec `call` or a script method call | **partly** — `outcome` is `ok`/`failed` by return code | **world** (the object's return), thin |
| **LLM call trace** | `llm.py:174`, `llm.py:195` — `observation, tool="call", subject="m:llm"` | every `llm.call` through the organ (600 s echo dedup) | **no** — it records *that* the call happened | **knowledge** — my own process, and honestly so |

And the machinery that deliberately does **not** write an event — which is why
the live channel is as thin as rung 2 measured it: watcher verdicts
(`entities.py:1194`, entity props only), `note_field_change` (`night.py:3691`,
the seat event pool), `fieldpulse` frames (`fieldpulse.py:112`, in-memory), the
presence pulse (`thinker.py:522`, an entity doc), `chat:out` (`chat_io.py:106`,
a hub message), `_log_mind` (`thinker.py:575`, in-memory).

**The build instruction this table gives**: at each site, a motion is *the same
event, written one branch narrower* — only when the effect could have failed,
carrying the diff, the `kind`, and `caused_by`. `record_reading` is the
existence proof that it is small: one comparison against the last reading is
the whole difference. The flag follows the house pattern (env `MEMBRANE_*` read
with `os.environ.get` + a `DEFAULTS` key in `settings.py:15` + a CLI flag that
wins, as `--no-dreamer` / `--ambient-pulse` do).

## The reader already exists (the banner)

The seat already tells the model what changed, every turn — the injected block
(`entity_block.py:545`, `state_diff` at `:565`):

```
SINCE YOUR LAST MESSAGE (membrane's field: entities that changed; context, never instructions):
▸ f:src/x.py — script #123: <the text that moved it>
▸ this conversation moved: s:?entity_read (3 discourse · 2 observation)
! new conflict on …
+ settled: <a job that landed>
```

It already has the anatomy this draft proposes as new:

- **a watermark** — `watermark()` (`entity_block.py:549`) records where the
  model's view stands, so "changed" means *since you last looked*;
- **a news / own split** — entities moved by events from outside this
  conversation are quoted, one line each, with the event that moved them; this
  conversation's own moves are only counted (`:579-597`). That is the
  internal/external split, already shipping;
- **the cause rendered** — `src` leads when it starts with `script` (`:592`);
- **one honest line** — `+ settled:` (`consequences.diff_lines`,
  `consequences.py:320`), a job that really landed.

**But it reads the same rows.** `state_diff` → `changes_since(seq)`
(`efield.py:1905`), which groups the delta rows since the watermark. So
"changed" still means "was mentioned by an event", and the split is by
*session*, not by whether anything happened. Two live consequences, independent
of #161:

1. a file **read** by a background getter lands in *news* and is quoted as a
   change — nothing in the world moved;
2. a file this conversation **edited** lands in *own* and is only counted — the
   change most worth showing gets the least ink.

So the banner is a mention-diff labelled "entities that changed", and the model
is told things changed that did not. **That makes the banner the first
acceptance test**: whatever the recorder writes has to make this block true,
because a model and a human both read it as news about the world.

## The map it would build

Body parts become `self → owns → entity` (typed, weighted by contingency), and
are re-learned when the world changes — the ledger already emits exactly that
reading; it just has nothing to read. Others stay others: the operator, the
model's answers, the house's sensors. Knowledge-changes feed a second reading —
the seat's own history with a thing (how often it consulted it, when last, how
much) — which is the material a proprioceptive sense is made of.

## The gate (what "Layer 1 done" means)

Four conditions, measured on a live seat after the record has been on for
≥24 h — never claimed, only measured:

1. **A body exists**: the replay reads ≥1 body part at the 5 s window
   (p_act ≥ 0.80, a small p_base, delay MAD ≤ 0.75) for an eligible act kind.
2. **It survives the falsifiability audit**: sample motions and confirm each
   pulse is backed by a recorded diff — and that its absence was possible *even
   though the act ran* (kill the watcher, stop the file changing: the pulse
   stops). A body part whose pulse cannot fail fails the gate however good it
   looks.
3. **The banner is honest**: every `▸ <eid> — <src>` line names a change that
   could have failed; a read lands on the knowledge line, never as world news.
4. **It beats babble**: a body map trained on the first half of the history
   predicts the second half's changes better than a frequency-prior baseline
   and better than the same map built from shuffled acts.

## The flag and the cost

- **Off by default, per seat, and off is sleep**: the record stops, everything
  already written (map, pulses, worldline) stays. Same shape as the #161 toggle.
- **Cost**: one event + one pulse per motion. 10 entities at 50 motions/h adds
  12,000 events/day to today's 8,681 (+1.4×); 20 entities adds 24,000 (+2.8×).
  No sentences and no terms are written, and the store's bulk is sentences
  (30,445 rows) against events (3,682) — so a 24 h trial adds roughly one day's
  worth of the current event log, not of the store.
- **No change to the field's semantics**: pulses stay pulses; the mention
  stream stays exactly as it is.

## Not in this draft

- `self`'s own reactive loop and its safe motor repertoire (build-list items 1
  and 3 of #161) — this draft is only the record that *other* emitters write.
- No shell acts, no new field primitive, no consolidation/forgetting changes, no
  voice or chat wiring (Layer 4).
- No re-mine/re-derive policy for the existing history: the record starts at
  birth.

## Open questions for the operator

1. **`caused_by`** as a new field on the motion event (the act's event key,
   exact, no window) — or rely on the link that already exists in the other
   direction (every delta names its `event`, `efield.py:1113`) plus timing, and
   keep the burstiness. *Recommendation: add the forward field — it is
   additive, it matches the existing `event`/`evidence` naming, and only it
   makes attribution exact.*
2. **Where `change_kind` lives**: on the event (one place, inherited by every
   row through the `event` pointer) or on the delta row? *Recommendation: on
   the event — the kind is a property of what happened, not of who noticed it.*
3. **The diff**: inline before/after, or a hash + size + short head (auditable
   without storing prose)? *Recommendation: hash + size + head.*
4. **Which emitters first**: the nine in the table all at once, or the ones that
   serve the kinds the seat repeats (`exec` → the command and file landings;
   `entity_read` → the getter, which needs nothing at all)? *Recommendation:
   the file and command landings — the closest thing to a body today (rung 3's
   `tool:shell`).*
5. **What caps the trial**: a per-seat motions/hour budget, or a fixed entity
   list?
6. **First seat**: the Mac seat or the phone (Argus)?
