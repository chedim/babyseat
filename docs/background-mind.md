# The background mind — thinker, dreamer, and the timeline organ

> The architecture after the cells retirement: membrane is a **seat-local
> mind with a shared symbolic layer**. It perceives (capture), decides
> (gate), sleeps (thinker/dreamer), elects (reality's vote), condenses
> (compactor/night), speaks (planner-gated), and modifies itself
> (model-authored entity scripts). The foreground composer is rented from
> the LLM — the background mind is membrane's own.

This document covers the organs built in the timeline-dreaming arc
(commits `e7a6f2d` through `eb99522`). The dev organism (cells) that this
supersedes is described in [dev-organism.md](dev-organism.md); the cell
pool is retired from the seat — `membrane/cell.py` / `pool.py` stay as
library + experiment code.

---

## Three clocks, one seat

| Clock | Cadence | Job |
|---|---|---|
| **Turns** | on operator messages | capture → gate → learn (fn stamp, why stamp) → reality's vote |
| **Thinker** | 1s heartbeat, duty-capped ~3-5% CPU | rumination: digest the last stimuli (captures, dream outcomes, night findings, **the operator's chat turns**) into a 5-item working memory; in silence, extend open timeline branches |
| **Dreamer** | 60s idle waves | solution generation IS timeline generation: proposed branches become Timeline entities under `session:now` |

The clocks are separate organs by design (the operator's ruling:
*thinking is different from dreaming*). The dreamer **generates** (new
branches from silence, steered by open intentions); the thinker **digests**
(the last few stimuli, chewed until understood) and **extends** (what could
happen next). Different inputs, different cadence, both bounded.

### The thinker's working memory

`deque(maxlen=5)`, drop-oldest — **attention, not a backlog**. Rendered in
`/membrane/state` as `think` (the queue + stats). Feed hooks: `_capture`
(every real capture, with a computed top-1 field sim as the surprise
input), `dreamer.vote_on_turn` (confirmations), the night loop
(materializations), and **every operator chat turn** (`enqueue_chat`).

### The thinker's voice (the authorship covenant)

The thinker may **speak on its own** — through `speak()`'s ActTable
planner gate (stay-silent is the learned default; chatter is
reinforcement-demoted) — but never initiates a model turn or a tool call.
**Membrane proposes; the operator acts.** The closest honest
"initiating a conversation" is the `ask-operator` act: an explicit
question the operator answers on the next turn. Both delivery channels
are seat-local: the peer push (shown immediately) and the model relay
(the `[membrane-says]` marked block riding the next model-bound request).

## The timeline organ (dreamer v2)

**Solution generation is timeline generation.** `session:now` is the only
TRUE timeline (the lived present); every dreamed branch is a `Timeline`
entity whose ISA parent is `session:now` — `branch_sim` (the
parent-offset cosine) IS the how-far-fetched measure.

The lifecycle, each step reality-owned:

| Stage | Mechanism |
|---|---|
| **Propose** | the field n-gram over trusted actions (Phase T tie-in: the typed token walk as second generator), steered by open intentions (cos ≥ 0.60) |
| **Vet** | topic / fresh / value (effective rank grows) + the **physics lane**: a branch landing near a known FailureMode (cos ≥ 0.85) is `impossible` — **kept** as the field's learned physics |
| **Commit** | a Timeline entity under `session:now`, status `quarantined`, **seat-local** (ephemeral: never written to the shared layer) |
| **Elect** | reality's vote: a real capture near the branch serving its `seed_intent` → **confirmed** (published to the commons); a near capture serving nothing → **refuted** (removed) |
| **GC** | unconfirmed past the horizon expire; a branch past ⅔ of the horizon gets **hungrier** (`curiosity +0.2`, cap 1.0) — the mind's last attention before expiry |
| **Render** | WHERE WE ARE shows open branches as tracks (`timeline (quarantined, branch_sim …): …`) |

**The seat boundary**: quarantined + impossible branches are RAM-only
(ephemeral). A seat's unconfirmed dreams are nobody else's business — the
parallel seat's banner renders ITS OWN speculation. Confirmation is what
makes a branch part of the commons.

### The curiosity loop (self-extinguishing attention)

`curiosity` is an entity property (default 0.7 at commit):

- **Confirmed** → `×0.4`, floor 0.05 (a served question relaxes; a
  confirmed branch still teaches)
- **Approaching the GC horizon unconfirmed** → `+0.2`, cap 1.0 (an
  unanswered question gets hungrier)
- **The thinker's idle attention** goes to the **hungriest** branch
  (highest curiosity, ties → newest), not simply the oldest

## Steering the dreamer: the intent board

The dreamer **skips without open intentions** (unsteered dreams confirm
near-randomly — v2's lesson). The steering wheel:

```
membrane: intend that make the benchmark loop fast [for 2w]
membrane: intents          # the board
membrane: fulfill #N       # explicit close
```

An intention is never auto-closed by similarity; only `fulfill` or expiry
closes it. With an open intent planted, the dreamer commits quarantined
branches within a minute of idle, the thinker extends them, and reality
elects them as you work.

## Programmatic entities (the scripts — model-authored)

**Scripts are the model's to write** (the doctrine teaches the lane; the
MCP surface is the pen): `membrane_entity_edit` carries
`mention_script` / `thought_script` / `dreamed_script` — small python
bodies that run **off-turn** at the thinker/dreamer hooks:

| Trigger | Fires when | Budget |
|---|---|---|
| `mention` | the entity appears in a turn's tags (the thinker digest) | 0.25s |
| `thought` | the thinker extends a branch linked to it | 1s |
| `dreamed` | the dreamer commits/elects a branch under it | 2s |

Each script sees the entity's geometry as `state` **plus the read-only
`membrane` object** (`membrane.field.top_k/sketch/directions`,
`membrane.me.describe/velocity/offsets/parents/children`,
`membrane.entities.get/timelines_of/related_by_class`,
`membrane.timeline.walk/distribution`, `membrane.intents.open/nearest`)
and the trigger shape (`state.extra`: `operator_text`, `sim`,
`terminal`, `branch_sim`, …).

The script returns `result = {render, patch, delete}` — **the engine
applies**: `render` → the entity's banner fragment; `patch` → the
entity's OWN properties (bounded 16 keys / 2000 chars; `script_*` keys
untouchable — a monitoring getter can **move the entity toward "done"
coordinates**); `delete` → retire (audited). Per-entity per-trigger
cooldown (60s default, `script_cooldown_s` overrides downward),
3-strikes auto-disable, every application audited. **Scripts analyze and
return; the engine writes.**

## Recall: the database is the field (E13/E14)

Couchbase 8.0's native vector index (HNSW) is the recall substrate:

- `membrane_exemplars_vec` over the field's exemplar docs (1024-d COSINE)
- `membrane_entities_vec` over the entity layer

`ExemplarStore.top_k` is **ANN-first hybrid**: the index answers primary
recall (flat ~5-17ms at any field size — the RAM matrix-rebuild's
80-200ms per call disappears); the RAM path is the fallback (ANN
degrade) AND the freshness tail; tier flags (`canon_only`/`live_only`)
are post-filters. `MEMBRANE_ANN=0` is the kill switch. Parity measured
(E14: 3/3 top-1 agreement on live probes) before the hot path switched.

### The personalized field

`ExemplarStore.load_neighborhood(seed_vec, radius=400)` — the seat's
working set = the **ANN neighborhood around the seed** + the seat's own
patch + the commons. The world stops hydrating wholesale; foreign
memories are reached **through the ANN at recall time**.

The seed: `session:now`'s final position, persisted **seat-locally** at
shutdown (`<field>.seed.json` — `session:now` itself stays RAM-only by
covenant). Where the operator left off is where they return. First boot
(no seed): the world hydrate; the swap starts the boot after.
`MEMBRANE_FIELD=world` reverts.

## Auto-compact (metric-targeted, on by default)

The compactor holds the field at a size and tracks how much of it is
still un-distilled:

| Trigger | Flag | Default |
|---|---|---|
| Growth | `--compact-growth-frac` | 0.25 (new raw turns since last note) |
| **Size target** | `--compact-max-field` | **8000** (field over budget → compact every interval) |
| **Raw-ratio target** | `--compact-max-raw-frac` | **0.4** (≥40% of the field stays distilled) |

`0` reverts a target off. The absolute floor (`compact_min_field`) and
the cluster guard hold under every target.

## Boot-time speech

The thinker's hello rides the same planner gate as everything else: after
the organs start, `_init_speak` reports one line (`membrane up: field N
cells, thinker + dreamer humming`). The boot context is seeded
(`report-state ×2`) so the hello speaks on every fresh boot; the ActTable
still learns from how you react to it.

## Kill switches (one per organ)

```
--no-thinker       # no background deliberation
--no-dreamer       # no timeline generation
--no-receipts      # (the lane is retired; kept for compat)
MEMBRANE_ANN=0     # pure RAM recall (the ANN-first hybrid off)
MEMBRANE_FIELD=world   # the world hydrate (the personalized field off)
```

## What this replaced

The **cell pool** (dev-organism.md's cells) is retired from the seat:
each cell was a second full Membrane whose grazed side-field nothing in
the recall path read back. The dreaming direction replaces them — same
idle-cognition role, on the main field, no parallel Membranes. The cell
machinery stays as library + experiment code; `--cells` no longer exists.