# The entity field

Membrane remembers per **entity**, not per exchange. Every file, symbol,
command, port, model, URL, phrase and word the project talks about is an
entity; each has a state vector that the sentences mentioning it move. What
the model is shown is rendered from those entities: the sentences that shaped
them (verbatim), what tools observed about them, what the code says, and
where the evidence contradicts a claim.

This replaces the exemplar field (one vector per situation/response pair).
Experiments e17–e25 showed why; the evidence is at the end.

## Two streams, one log

Everything captured becomes an **event** in the `events` collection, the
source of truth:

| stream | what | written by |
|---|---|---|
| `discourse` | the user's messages, the model's prose, its reasoning, notes (`learn`), ingested docs (`doc`) | `capture.on_turn`, `learn`, `ingest` |
| `observation` | one event per tool call: tool, parsed args, result head (6 KB) and tail (2 KB), outcome class, the message it served | `capture.on_calls` / `on_tool_result` |
| `code` | one event per definition: `qual(sig) in file: <docstring head>` | the codegraph refresh |
| `distilled` | the old compact notes: a source of claims, never of state | the backfill only |

`membrane/streams.py` splits a turn (or any of the six historical audit
shapes) into events. Discourse never contains tool syntax; that is pinned
as a test. Events are content-keyed, so a re-sent turn is a no-op, and a
tool's result upgrades its call-only observation through the same call id.
Each event has a microsecond `seq` (the order) and a short per-origin
number `n`, the `#N` the blocks cite and `forget #N` removes.

Turns arrive from every surface through the same door: the TUI seat, the
native `membrane serve` room-facing server (opencode replaced; its turn
loop calls the proxy's own `/v1` on loopback, so gate, injection, capture
and banner are never bypassed), and the desktop seat. There is no second
capture path.

## Entities

`membrane/eids.py` gives every mention a canonical id `kind:norm`:
`f:membrane/router.py` (repo-relative), `s:membrane.router.Router.pick`
(module-qualified), `c:git`, `port:11500`, `m:glm-4.6`, `u:…`, `tool:edit`,
`p:replay guard`, `w:gateway` (plural-folded). Identifiers win over phrases,
and phrases over words.

Each entity is a document in `entity_states`:

- **seed**: the bare term's embedding (its everyday meaning), cached in `terms`;
- **state** `← unit(state + 0.5·(x − state))` per mention sentence `x` (the
  *local* sentence, not the whole message; e18: span tracking distinguishes
  entities 68% vs 59% for whole messages);
- **mean** and **rsum** (the sum of residuals against the stream's
  background), from which **coherence** `(|rsum|² − n)/(n(n−1))`, the mean
  pairwise cosine of its residuals: do its sentences agree on a direction?
  A word is *coherent* above the null μ+3σ (random sentence sets);
- **mass** (`accr`): mentions whose alignment with the running mean clears
  0.15, i.e. accumulated agreeing evidence;
- **specialization** (how far its use drifted from the seed) and
  **activity** (did its meaning keep moving);
- tier: words and phrases wait in a **nursery** until 3 distinct sentences
  (their buffered mentions are then replayed); identifiers are live at once.
  Boilerplate sentences (seen more than 8 times) move nothing.

Every mention is also stored in `entity_deltas` (no vector, so it can be
derived again), so `rebuild()` re-applies the log in order without
re-embedding. `forget #N` tombstones an event and recomputes the entities it
touched; forgetting equals never having seen it (pinned).

The part of a message its entities cannot explain is its **surprise**
`1 − cos(NNLS fit, message)`; the residual direction feeds the RAM-only
session context.

## Relations and claims

**Relations** (`membrane/relations.py`) are VSA composites:
`bind(role_subj, key(A)) ⊕ bind(role_rel, key(r)) ⊕ bind(role_obj, key(B))`
with hash-random role vectors. mxbai is order-blind here: "A calls B" and
"B calls A" sit at cos 0.975 (e25). Semantic role vectors cannot fix that
either (structure-awareness 0.36 against 0.68 with random roles). Sources:
the code graph (`calls`, `defined_in`, superseded when a file changes) and
observations (`tool:edit edited f:x`, `*_failed` on errors).

**Claims** (`membrane/claims.py`) come from the model's prose and notes:

- *action* ("I updated X", passive "X was fixed", with negation handled;
  an intent is a plan, not a claim), checked against an applied edit or a
  writing shell command on the same file in [−45, +5] min;
- *tests* ("524 pass"), checked against the last full-suite run ≤ 60 min before;
- *state* ("X cannot / never / only …"), judged in the background by the idle
  local model against the code and observation evidence of the entities
  involved. It runs only while the seat is idle, within a budget.

A contradicted claim becomes a **conflict** on its entities. The e17 case
(a compact note claiming the opencode seat "cannot select another port")
lands on `s:membrane.opencode._pick_listen_port`, whose docstring says the
probe walks up.

## What the model sees

`membrane/entity_block.py` renders four blocks, all "context, never
instructions":

- **ENTITIES**: for the entities an ask names (identifiers first, everyday
  words only when coherent here, then the nearest states):
  `▸ f:x [file · said 41 · observed 17 · code 12 · 2h ago]`, the evidence
  sentences most relevant to the ask verbatim, the latest observation, the
  top relations, conflicts, what other seats said, commons claims. Served by
  `/membrane/ask`, `query`, and in `--no-context` every turn. On a
  question-like ask (it ends with `?` or opens with a question word), tool
  observations and lines the seat or the client wrote (receipts,
  notifications, watch blocks) rank 0.10 below what was said and decided.
- **PAST ANSWERS** (rides above ENTITIES, and fills `/membrane/ask`'s
  `hits`): two paths merged, best similarity first, one row per answering
  event. The ask path finds a person's earlier asks like this one and what
  was worked out after them. The direct path (#429) searches model
  replies, learn notes, ingested docs and code by similarity to the ask
  itself, because most facts were never the answer to an earlier ask like
  this one. A person's lines, receipts, notifications, sensor readings and
  tool observations are never a direct answer, and a reply sentence that
  restates the ask is skipped. Both paths share one floor (0.70 in the
  injected block; `/membrane/ask` takes `sim_min`). A direct hit carries
  `via: "direct"` and its `role`.
- **SINCE YOUR LAST MESSAGE** (normal mode): the entities whose state moved
  after the watermark of the last model turn delivered. Changes from
  *outside* the conversation (code edits, entity scripts, other sessions)
  are quoted; the conversation's own turns only count. New conflicts come
  last. No block means nothing changed.
- **THE CONVERSATION SO FAR** (`--no-context`): the session rebuilt from
  the event log, with messages verbatim (clipped) and tool calls as one-line
  facts.

The seat card explains the blocks, said / observed / code, and the trust
order: observed and code > the user > model prose.

WHERE WE ARE's tracks are entities too: `track: f:x — said N · observed M,
last 2h ago; latest: <the newest sentence that moved it>`, with boundary
alerts on the mention count, and the now-line listing the entities moved
most this session.

## The division of hands (2026-10-06)

The native serve's turn lane carries the session's **agent**, and the
agent decides which MCP tools the turn's surface carries and which
calls the executor admits. Two minds, one registry (`_lane_tools`,
membrane/server.py):

- **Operator** (agent `build` — the main chat): every hand since the
  flood gates (2026-10-08): reads (`query`, `state`, `graph`,
  `entities_related`, `entity_query`, `entity_read`, `entity_tracks`),
  memory (`learn`, `forget`), steering (`intend`, `intents`,
  `fulfill`), room (`peer_post`, `peer_seats`, `peer_read`, `junk_cut`), the watches
  (`entity_watch`, `entity_unwatch`, `field_watch`, `attention`) and the
  entity hands (`entity_add`, `entity_edit`, `entity_remove`,
  `entity_inherit`, `entity_link`, `entity_unlink`). The chat runs a
  one-line command itself instead of planting a goal for it.
- **Event listener** (agent `watch:<eid>` — the thinking loops): the
  operator's set **minus** `intend` (a loop takes the next step, closes
  with `fulfill`, or ends BLOCKED; it never plants new goals).
- **Phone** (agent `phone` — a call through agdroid's calld): reads,
  `learn`, `intend`, `intents`. Never the executor or entity
  writes: a misheard sentence must not change the boat; a caller's
  request becomes a visible goal.
- **Plan** (agent `plan`): the operator minus every write and close — a
  planning session reads and communicates, never plants.

Refusal texts are lane-specific: only a plan session hears "plan mode".

A cross-lane call never reaches the impls: the tool result IS the
honest refusal (a pointer to the right lane), audited
`lane-refused`. The agent rides each message (the attached TUI sends
`agent` per message POST); the turn's agent wins over the session
row's, and the pick **sticks** (the session row updates — the mode
toggle survives restarts of the conversation). Kill switch:
`MEMBRANE_LANE_GATE=0` (runtime-read) restores the flat surface. The
launch-mode stdio MCP child (an external opencode's config) keeps the
full surface — the per-call agent is unknowable there; the native
serve is the enforcement point.

Listener-owned watches deliver differently: `entity_watch(owner=
"watch:<eid>")` routes the boundary crossings into the events pool —
the listener receives its own watch's firing as its next turn's
message (the same event-input flow every loop turn rides). Operator
tracks keep the banner ALERT delivery; `entity_tracks` is owner-aware.

## Intent lane (2026-10-06)

Intentions steer the idle mind and are planted by the model: the
`intend` tool / `membrane: intend that …` frame create-or-pin a shared
Goal entity and pin it on the seat's active set; `intents` reads the
board; fulfillment is explicit (`membrane: fulfill #N`). An entity
script may plant too — its result carries `{intent: …}` and the engine
plants through the same lane (night.py's apply). The plant is an
EVENT (`intent-planted`) a thinking loop receives as its next turn
(plan or ignore) — the operator creates, the thinker receives and
executes. While the seat is paused, planting still works and is inert
by construction: every lane that would react is down too.

## The executor (2026-10-06): the queue + the ISA pipeline

`tool:executor` (seeded at boot) is the seat's exec organ. The model's
exec door:

- **`entity_link(executor, commands)`** — link command entities to the
  executor and run them; a spec that resolves to nothing is created on
  the fly (the spec IS the command line; `create=False` refuses). The
  **`executes` edge (a relations triple) IS THE QUEUE**: live = owed
  work, **superseded = processed** (the unlink-on-done: success AND
  failure stamp done after the residue lands — processed ≠ succeeded;
  re-linking = the deliberate re-queue, **no auto-retry**). The queue
  caps at 32 owed edges (a landed run never counts).
- **Residue** (`run_rc/outcome/head/tail/ms`) always lands on the
  COMMAND's own doc — every later reuse reads the freshest truth. The
  link response carries every command's post-run state (the sync
  rule).
- **Composition IS inheritance**: a command entity's ISA parents
  (`entity_inherit`) are its pipeline. `entity_link` runs the node's
  whole ancestry **post-order** (parents first), MEMOIZED per eid (a
  diamond's shared ancestor runs once), then the node itself — fed
  the parents' outputs through substitution: `{{parent.head}}` /
  `{{parent.tail}}` (nearest parent), `{{parent:<eid>.head}}` (named
  fan-in — multiple parents are wait-all, the child's command names
  what feeds where). A Tool **without** a command but **with** parents
  is a composition node: it runs its subtree and carries the last
  parent's residue as its exit (a pipeline header). ISA edges on
  non-command entities (Components/Goals) stay classification — never
  execution. Stop-on-failure: a failed step halts the walk (`&&`
  semantics); the child marks `failed_step`/`failed_reason`.
- **The caps**: 12 nodes per pass (a deeper ancestry reports
  `continued: True` and the background reaper finishes it next beat);
  depth 16 (the ISA walk's own MAX_CHAIN).
- **Reading the queue**: `entity_read` on the executor (or any node)
  returns the value **after** processing + the queue split:
  `pending` (owed, live edges) / `done` (processed, with residues and
  the re-link count `n_times`). `entity_unlink` supersedes live edges
  manually (withdraw owed work).

The composition loop: author small command entities once (build,
test, scan), wire ISA parents into pipelines REUSING them, link, and
every step's residue stays readable for the next composition. The
model-facing teaching: doctrine.md + the `entity_add`/`entity_watch`
tool descriptions.

### The durability plan (the seat's own design, delivered)

Three homes, one rule — **facts durable, states volatile, wakes
derived**:

- **The receipts** (the symmetry rule): every supersession carries a
  RECEIPT written on the edge row — `{op: done|expired|withdrawn, ts,
  rc, outcome, why}` — the relations journal persists it (a reload
  replays the receipts). A cancellation is as durable as the debt;
  the phantom-debt exhibit (46 live no-receipt edges surviving boot
  after boot) dies here.
- **Expiry-with-residue**: an owed edge past its TTL (24h,
  `EXEC_EDGE_TTL_S`; a re-link bumps the clock honestly) expires —
  stamped + received — never re-run; the boot's book-balance runs it,
  so does every NightClock pass. Re-link = the deliberate re-queue.
- **Boot-derived wakes**: the boot reads the open intents and
  re-queues an `intent-planted` event per unclaimed goal (a
  `intent_planted_ts` stamp guards the double-fire). The volatile —
  lanes, pools, loops — stays RAM; the DURABLE board re-derives them:
  plant a goal, restart, come back tomorrow — the listeners wake on
  the board's authority.
- **The claim receipts**: a listener's landing for a goal stamps the
  GOAL's doc (`landings[]`, capped 8) — the fulfill-by evidence; the
  operator fulfills on receipts, never on vibes.
- **The residue honesty**: a shell run's outcome reads the RC as the
  ground truth (a zero-exit run that *mentions* 'error' in its output
  keeps its `ok` class — the `rc=0 error` residue bug is dead).

## The gate and the replay A/B

`membrane/efgate.py` runs on every real turn. It finds the earlier user
message nearest the ask and its answer (the last model message before the
next user message). Only a person's messages count as earlier asks: job
receipts (`[membrane] job #`, `your job #`), task notifications
(`<task-notification>`) and `[membrane-watch]` / `[membrane-intercept]`
blocks also arrive as user turns, so each user event is tagged with its
`author` at capture, and an older event with no tag is read by the same
prefixes (#429). The same rule picks the earlier asks behind PAST ANSWERS. The answer is replayable only when all of these hold:

- the similarity is ≥ 0.92;
- the asks' specific entities agree (Jaccard ≥ 0.8);
- the other near asks got a similar answer;
- the answer is trusted and was never re-asked;
- the ask's pronoun resolved;
- **the staleness guard**: no observation, code change or conflict on the
  ask's entities is newer than the answer.

Replay is **not served** by default. Every eligible turn is logged as an
A/B pair instead: the replay next to the model's actual answer, their
similarity, and later the idle judge's verdict (`agree / partial /
disagree`). `membrane review --ab` reports the pairs. `--replay-ab P`
serves the replay on a fraction P of eligible turns.

## Storage

Couchbase scope `membrane`, nine collections: `events`, `sentences`
(content-addressed embeddings, shared by all origins), `terms` (seeds),
`entity_states`, `entity_deltas`, `relations`, `claims`, `annotations`
(replay A/B, trust), `origin_stats` (backgrounds, the coherence null, the
applier cursor and lease, the backfill cursor). Composite indexes cover the
scans the field runs; a vector index on `entity_states.state` and
`sentences.vec`, with a brute-force fallback. Writes to one entity go
through CAS; one applier per origin holds a lease.

Vectors are stored rounded to 6 decimals: a live entity is ~18.8 KB, a
sentence ~10.5 KB at 1024 dims. Seeds are float16 and running sums float32,
because float16 drifts on sums of thousands of unit vectors. In memory every
value is kept at its stored precision, so a restart never changes a result.

`MemBackend` (RAM) serves tests and lab paths; an unreachable cluster
degrades to it and never raises at boot. The bucket ladder (create, or
reuse a bucket left behind by another seat when the RAM quota is spoken
for) lives in `membrane/substrate.py`.

## Sharing

Each seat (origin) writes only its own events and states; reads merge.
`merged(eid)` is the seed plus the mass-weighted mean of every seat's
displacement; two seats that moved an entity in opposing directions
**diverge** (shown as `! seats diverge`). The working set is the seat's own
nearest states plus other seats' states it does not hold. The same claim
from two or more seats becomes a **commons** claim (`consensus`, or
`disputed` when a seat contradicted it), promoted at night. The old global
field's curated notes were converted into commons claims once. `membrane
move --efield` copies the collections between clusters or dumps them.

## The background mind

Each applied live event is a stimulus. The thinker digests it (its entities
as tags, an observation as the fn stamp), and user messages vote on the
dreamer's quarantined timeline branches. The dreamer proposes from the
model's recent sentences and vets a candidate by its surprise against the
entity states: on topic, but not already said. The NightClock (15 min) runs
the entity scripts; **births** (entities with mass ≥ 8 whose use drifted
from the everyday sense, announced once); **junk** (words said ≥ 24 times
that never took a direction); activity; the commons; and the coherence null
once a day. The capture worker's idle hook runs the claim judge and the A/B
judge on the idle local model.

## Building it from history

    membrane backfill            # = python -m membrane.backfill

The backfill writes the codebase first (code events and relations), then
the audit log in file order through the same intake. Sessions split at
30-minute gaps; records without timestamps take their line number as seq.
Each chunk is embedded and applied, and the byte offset is saved, so a run
resumes. A second run writes nothing. `finish()` numbers the events,
refreshes the coherence null and settles claims.

`membrane reembed --efield --embed-model M` moves the field to another
embedding model: every live event is re-embedded and re-applied.

## Evidence (e17–e25)

| experiment | finding |
|---|---|
| e17 | Whole-message recall buried answers: the restart-time answer ranked 21st; a real answer was MMR-dropped as a "duplicate" of an unrelated note at cos 0.91; a compact note contradicted the code (the port case). |
| e18 | A message's embedding can be rebuilt from its entities' states well above a random-entity null; tracking each entity by the **sentences that mention it** beats whole-message drift (68% vs 59% distinguishability). |
| e19–e21 | Filters for "which candidates are entities" gain little over random same-size sets; mass (agreeing evidence) is the stable property. So: every word is an entity, and the statistics decide what it means. |
| e23 | Coherence of a word's residuals separates project terms from filler; the everyday seed plus the project's sentences give specialization and activity. |
| e24 | Tool output mixed into conversation text poisons every word statistic. Split into discourse and observations, 77% of discourse entities have observed evidence. |
| e25 | Action and test claims can be checked mechanically (test-count claims match their run in ≥ 83%); the port note's contrary evidence existed only in the code, hence code events; mxbai is order-blind for relations (0.975), VSA role binding is not. |

## The entity-scripts covenant (2026-10-03)

Everything is an entity; the model sees entities; a script is a **part** of
its entity. The script's only ways outward are its own shape:

- **render** — the banner fragment (the heartbeat's visible line)
- **position** — the patch may move the entity in the field (unit-normed)
- **script_data** — its own data lane
- **`{run: true}`** — the engine execs the entity's own `command` prop
- (field-watch lane only) **links / unlink**

`membrane.speak()` is REMOVED from the script API (a legacy call strikes);
script `delete` is refused — retirement is `entity_edit`, the model's move.
Arbitrary prop-writes refuse (`patch_refused` in the apply's return).

**An error is an entity**: a raising or over-budget script bubbles as
`membrane::error:<fingerprint>` — a doc dropped at the errored entity's
position, refreshed while the error recurs (`n_hits`, `last_ts`), never
refreshed when fixed (so it drifts back in time on the updated axis by
itself). The event pool never hears engine lanes: what the model's
watchers watch is the model's own choice.

## Script budgets (the resource matrix)

| trigger | budget | pacing | gravity? |
|---|---|---|---|
| tick (heartbeat cron) | 0.25s hard (abandon + strike) | authored `tick_every` | yes: heat ×1–8 |
| mention (digest) | 0.25s | the digest's 10s | yes |
| watch (autonode listener) | 10s + the 4-thread pool, abandon+strike | event arrival (5s cooldown) | no |
| getter (read-back) | 1.0s | the read's 5s LRU | no |
| thought / dreamed | 1s / 2s | the hum / 60s idle | no |
| consolidation (nudge) | 2s | the 15-min pass | yes (every_n × gravity) |
| change (field-watch) | 0.25s + gravity + freeze | crossing + hysteresis | by design |

A tick is the hum's beat — one at a time, on the heartbeat thread; slow
ticks stall the pulse, hence the hard budget. Watchers run on the bounded
pool away from the pulse. Over-budget = the fire is ABANDONED (the script's
thread lands out-of-band; the per-entity lock holds the next fire) and the
overrun STRIKES — a slow-success counts, 3 strikes disable.

## Heat, gravity, freeze (the limiter)

Every entity carries `entity_heat` (0→∞): the max of its field-churn EMA
(updates/s) and its scripts' CPU EMA (`process_time` ms per run, so a
sleeping script never reads hot). Effects, continuous:

- **gravity**: the effective tick interval = authored `tick_every` ×
  (1 + k·heat), ceiling ×8 — hot entities tick rarer, cold exactly as
  authored. The consolidation `script_every_n` stretches the same way;
  the overrun gate reads the EFFECTIVE interval (a stretched cron can't
  wedge).
- **freeze** (heat over `MEMBRANE_HEAT_FREEZE`, default 1.5): the
  entity's field-watches PAUSE (`frozen_ts` stamped, visible) and ticks
  sit at the gravity ceiling; **melt** after `MEMBRANE_HEAT_MELT_S`
  (60s) under the bar — a suppressed burst then delivers as ONE grouped
  event (n_changes + span).

Knobs: `MEMBRANE_HEAT_COLD` (0.3), `MEMBRANE_HEAT_FREEZE` (1.5),
`MEMBRANE_HEAT_MELT_S` (60), gravity ceiling ×8, churn half-life 20s.


## The field-watch lane (2026-10-03): the entity watches itself

The model plants up to **3 field-filters per entity** (`entity_edit` with
`watch_fields=[...]`): the filters read the entity's OWN field state —
never the incoming streams. Fixed verbs, model-defined args:

| verb | arg | fires when |
|---|---|---|
| `pos` | sim distance | the position moved further than arg (chord distance from the last stamped position) |
| `align` | floor | coherence/net-align crossed DOWN through the floor |
| `vel` | speed | ring-velocity crossed UP past arg |

**Crossing semantics**: one fire per transition; return re-arms (the
stamps live in the entity's props — `wf_stamp_pos/align/vel`).

The field's flush IS the event source: every dirty pass checks the
filters and delivers ONE `field:changed` event (kind/entity/verb/
n_changes/span) to the ordinary event pool — the drainer admits it like
any watch fire (3 slots, the loops' turns, the verdict stamps
`watch_last_decision`). The entity carries `change_script` — the reaction
runs under the same engine covenants (0.25s budget, gravity, freeze,
3-strikes); its verdict obeys the whitelist (position move, links for
this lane, render).

**Gravity's grouping**: a hot entity's changes do not each fire — they
accumulate into a burst; when the heat subsides (or `WF_MAX_LATENCY_S`
= 120s passes — a forever-hot entity delivers a grouped event every 2
minutes, never silence), ONE event delivers (n_changes, span_s). Heat
over the freeze bar (`MEMBRANE_HEAT_FREEZE`, default 1.5) freezes the
filters entirely (`frozen_ts` stamped — visible in the entity's props);
melt happens after `MEMBRANE_HEAT_MELT_S` (60s) under the bar, and the
held burst delivers. Watching is the model's choice; the meter is the
field's law: hot means slower, frozen means quiet, both visible.

**Planting**: `entity_edit(eid, watch_fields=[{verb, arg}, ...])` —
refuses a 4th filter, unknown verbs, non-numeric args; empty list
clears. Attach/detach announce to the pool like every sensor's arming.


## Ollama's serial queue (the operator's note, 2026-10-04)

Ollama serves its models **one call at a time** (`OLLAMA_NUM_PARALLEL`
defaults to 1). Membrane's lanes all call the same Ollama: your turn's
model, the embedder (every capture), the claim/A-B judges, the
watcher's reads. A single slow call (a cold 14B load, a cloud relay's
hang) queues EVERYONE behind it — the turns read 30-115s "stuck", my
concurrent probes time out, and the room feels dead while the queue
drains.

**The honest fix is Ollama's env** (the operator's hand, in the app's
launch env):

    OLLAMA_NUM_PARALLEL=2      # the turn + one ambient lane live
                               # together; 3 = roomier, more VRAM
    OLLAMA_KEEP_ALIVE=30m      # the model stays loaded (the cold
                               # load's cost stops recurring)

Measured on the M2 Pro/32GB with qwen3:14b resident (9GB): a probe
behind a turn: 30-115s; alone: 1.2s (granite4). The queue is the
latency.
