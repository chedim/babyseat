# The motion record (#161, layer 1): what the seat has to write down for a body to exist

**Status: DRAFT for the 0.7 build (2026-10-15). Nothing below is implemented.**
The measurements are in the lab: `experiments/self_l1/` (`sandbox.py`,
`replay.py`, `density.py`, `probe.py` — each with its own tests), whose README
carries the raw tables. This page is the *design* those numbers dictate.

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

Every event and every pulse is written by one of three hands, and only one of
them can witness a body:

| author | share of pulses | may it answer an act? |
|---|---|---|
| the seat's own voice (its reasoning + its replies) | 76% | **no** — it is me talking, not the world moving |
| the operator | 9% | it is an *other*: the instrument's control case |
| the agent's own tool calls (the act's footprint) | 8% | **no** — it is the act's own shadow |
| the seat's machinery (ticks, jobs, watchers, getters, file landings) | 7% | **yes — this is where a motion belongs** |
| notes | 1% | — |

## What counts as a motion

A **motion** is an event, authored by an entity's own machinery, recording that
that entity's state changed because something ran — with six rules:

1. **Authored by machinery.** Never by the agent's turn, never by the LLM's
   prose. The LLM emits words; words are mentions. A motion is a change.
2. **Falsifiable.** The pulse exists **only if the state actually changed** —
   record the diff (before → after). A failed act, a script that errored, a job
   that landed with no effect: nothing is written. This is the rule the ledger
   *cannot* enforce (rung 4): a pulse we write unconditionally becomes a body
   part by construction, and the shuffled null will not catch it.
3. **Causally keyed.** The motion event names the act it answers
   (`caused_by: <the act's event key>`), so attribution is exact rather than
   windowed. This is also what removes rung 3's artefact — p_act inflated above
   the true rate because the seat acts in bursts and a neighbour's answer lands
   inside this act's window (the stray rung 1's MAD exists for).
4. **Invariant, not argument.** A body part answers **every** act of one kind,
   so a motion should carry the state the act *always* touches — the seat's own
   object, a component's own state — not the file the act happened to read.
   (Rung 4: pulsing the argument gives the densest possible channel and no body
   at all.)
5. **No prose.** One small event + one pulse. No sentence, no terms, no
   embedding: the motion record must not feed the mention stream it is trying
   to be readable apart from.
6. **Rate.** ~40-60 motions an hour on one entity (rung 3), serving one of the
   kinds the seat actually repeats (8 of 42 today).

## Where a motion would be emitted

The good news: the seat's machinery already funnels through **one event writer**
(`EntityField.write_events`, `efield.py:502`) and **one pulse writer**
(`EntityField.flush` → `upsert_multi("entity_deltas")`, `efield.py:1396`). Every
delta row already names the event that produced it, in the field **`event`**
(`efield.py:1113`, `efield.py:1257`) — so the link *pulse → cause* exists and is
exact. What does not exist anywhere in `membrane/` is the forward link *event →
the act it answers*: no `caused_by`, no `motion`, no `motor` (`motion` appears
only as prose about entity geometry, `entities.py:480`, `night.py:290`).

So the record is not a new subsystem: it is a small change at the ~9 sites that
already write machinery observations.

| emitter | site | trigger / cadence | as written today | writes a pulse? | fires only on a *change*? |
|---|---|---|---|---|---|
| **script verdict tell** (every script's result) | `night._script_tell` `night.py:800` | tick / watch / getter / thought / dreamed / change / nudge verdicts | `stream=discourse, role=note, src="script:<kind>", subject=<eid>` | yes | **no** — a tell is written because the script ran; a *silent* tick apply writes no tell at all (`night.py:4719`) |
| **tick mover** | `Thinker._tick_field_move` `thinker.py:517` | hum beat, movers only every 60th (`TICK_FIELD_EVERY`) | `stream=observation, tool="tick", args={beat}`, subject = the entity | yes | **no** — a clock, not a diff |
| **getter reading** | `consolidate.record_reading` `consolidate.py:149` | getter fires, ≤1/min per entity | `stream=observation, tool="read", session="readings"` | yes | **yes** — only when the read text changed |
| **file landing** | `night._file_land` `night.py:1130` | read / write / edit / glob / grep | `stream=observation, tool=<action>, args={path}, outcome=ok|error`, subject = the `f:` entity | yes | **no** — a read that changed nothing still pulses |
| **command landing** | `night.run_command_entity` `night.py:1016` | a `{run:true}` verdict, or the reaper batch (1/min) | `stream=observation, tool="shell", args={command}`, subject = the command entity | yes | **no** — an `outcome=error` run pulses too |
| **job settle** | `consequences._publish` `consequences.py:256` | daemon thread, every 300 s | `stream=observation, tool="consequence", session="consequences"` | yes | **no** — one event per settled horizon, effect or not |
| **method call** | `night.py:3029` | exec `call` / a script method call | `stream=observation, tool="call"|"read", args={object, method}` | yes | **no** |
| **LLM call trace** | `llm.py:174`, `llm.py:195` | every `llm.call` through the organ (600 s echo dedup) | `stream=observation, tool="call", subject="m:llm"` | yes | **no** — it records *that* the call happened |
| **stranger visit** | `strangers._field_line` `strangers.py:139` | login-page visitor, ≤12/min, 60 s per stranger | `stream=observation, tool="visit"` | yes | **no** |

And the machinery that deliberately does **not** write an event — which is why
the live channel is as thin as rung 2 measured it: watcher verdicts
(`entities.py:1194`, entity props only), `note_field_change` (`night.py:3691`,
the seat event pool), `fieldpulse` frames (`fieldpulse.py:112`, in-memory), the
presence pulse (`thinker.py:522`, an entity doc), `chat:out` (`chat_io.py:106`,
a hub message), `_log_mind` (`thinker.py:575`, in-memory).

**The build instruction this table gives**: at each of the nine sites, a motion
is *the same event, written one branch narrower* — after a real diff, with
`caused_by` set to the act's event key, and nothing written when the diff is
empty. The flag follows the house pattern (env `MEMBRANE_*` read with
`os.environ.get` + a `DEFAULTS` key in `settings.py:15` + a CLI flag that wins,
as `--no-dreamer` / `--ambient-pulse` do).

## The map it would build

Body parts become `self → owns → entity` (typed, weighted by contingency), and
are re-learned when the world changes — the ledger already emits exactly that
reading; it just has nothing to read. Others stay others: the operator, the
model's answers, the house's sensors.

## The gate (what "Layer 1 done" means)

Three conditions, measured on a live seat after the record has been on for
≥24 h — never claimed, only measured:

1. **A body exists**: the replay reads ≥1 body part at the 5 s window
   (p_act ≥ 0.80, a small p_base, delay MAD ≤ 0.75) for an eligible act kind.
2. **It survives the falsifiability audit**: sample motions and confirm each
   pulse is backed by a recorded state diff — and that its *absence* was
   possible (kill the script, the pulse stops). A body part whose pulse is
   written unconditionally fails the gate however good it looks.
3. **It beats babble**: a body map trained on the first half of the history
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
2. **The diff**: inline before/after, or a hash + size + short head (auditable
   without storing prose)? *Recommendation: hash + size + head.*
3. **Which emitters first**: the nine in the table all at once, or the two that
   serve the kinds the seat repeats (`exec` → the command and file landings;
   `entity_read` → the getter)? *Recommendation: start with the landings —
   they are the closest thing to a body today (rung 3's `tool:shell`).*
4. **What caps the trial**: a per-seat motions/hour budget, or a fixed entity
   list?
5. **First seat**: the Mac seat or the phone (Argus)?
