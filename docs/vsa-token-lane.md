# The VSA lane — entity keys and relations

*Status: the lane two this document describes is built on; the token-lane
recurrence it came from was retired with the exemplar field (git history
is the archive — see the last section).*

## What the VSA machinery is now

Every entity and every role on the field gets a **hypervector** derived
deterministically from its NAME — not from the embedder:

- `eids.entity_key(eid)` = the hash-token binding of the canonical id
  (`vsa.hash_token_vec("ek|" + eid)`, D=8192);
- `eids.role_key(role)` = hash-random (never the semantic codebook —
  near-equal strings produce near-equal vectors, which cannot encode
  order; that was the E25 lesson, below).

Deterministic keys mean every seat computes the same vectors for the same
entities with zero shared state: relations compose identically
cross-origin, which is what makes them shareable in the commons.

## Relations = composites (membrane/relations.py)

A (subject, relation, object) triple is encoded as

    bind(role_subj, key(A)) ⊕ bind(role_rel, key(r)) ⊕ bind(role_obj, key(B))

a bundle whose parts stay recoverable by unbinding the role. Stored in the
`relations` collection per origin: `{subj, rel, obj, source, n, ts,
evidence event keys, the packed composite}`.

**Why VSA and not the embedder:** mxbai is nearly order-blind for
relations — "A calls B" vs "B calls A" at cos 0.975 (structure-awareness
0.06). With random role vectors, order structure comes back: the E25 run
measured 0.28 → 0.58 when the hash-random roles replaced the semantic
codebook roles, and the code graph's `calls` triples read structure
correctly (membrane's own lane 0.80). Read-back is
`unbind_role(composite, role)` → a noisy estimate of the role's payload,
then `cleanup()` against the known entity keys (nearest key wins).

**Sources** (built and maintained automatically):
- the code graph: `call:` relations per def pair, `defined_in` per file,
  superseded when a file's code events are replaced;
- observations: `tool:edit edited f:x`, `*_failed` on error outcomes.

**Readers:** the ENTITIES block's "top relations" lines, and co-mention
answers in `related_entities`.

## The canonical id space (membrane/eids.py)

`canon(kind, surface, graph)` — every mention lands on `kind:norm`:

| kind | example |
|---|---|
| `f:` | `f:membrane/router.py` (repo-relative file) |
| `s:` | `s:membrane.router.Router` (module-qualified; a bare symbol resolves through the codegraph defs to its qualname, else `s:?name`) |
| `c:` | `c:git` (discourse commands count only in backticks / after `$ `) |
| `port:`, `m:`, `u:` | ports, models, urls |
| `tool:` | `tool:edit` |
| `p:`, `w:` | phrases, words (plural-folded) |
| `ctx:` | `ctx:session:<sid>` (the RAM-only session context) |

Legacy overlay keys (`membrane::files:x`, `Membrane::Component:…`) map
through `_LEGACY_KIND` to the same canonical ids — the entity layer and
the field share one id space, which is why `forget #N`, the watch layer
and the overlay docs all key on `canon_eid`.

`mentions_of(sentence)` picks the entities a sentence moves: **specific
kinds win over phrases, phrases over words, maximal phrases**.

## The determinism covenants

- `vsa.DEFAULT_DIM = 8192`, all lanes seed-hash the same way: the same
  (dim, seed, scheme) on every seat yields the same vectors — shareable
  without a shared codebook.
- Degradation: the lane never requires the embedder or Couchbase; binding
  is pure numpy.
- Lab isolation: nothing under `tests/` or the lab paths persists into a
  real cluster's collections (the `_is_lab_field_path` guard).

## What the token lane was

Before the entity field, this document described the **VSA token lane**: a
per-turn hypervector "massage" (bag + recency roles + adjacent pairs,
packed ~1 KB/state) riding exemplar rows, with seven consumers (shape
archaeology, loop fingerprint, shape-aware dedup, the order-shaped ballot,
thinker convergence, the shape-poke, the streaming prefix seam) and the
measured properties (order 19% at 6 tokens, paraphrase nearer than
unrelated at 37%, tail dominance 18%-vs-43%).

The exemplar field went away outright (stage 17 of the rewrite;
git history is the archive). What survived is the **deterministic
binding**: the entity keys are the lane's `hash_token_vec` applied to
canonical ids, and its role-binding insight — random role vectors, not
semantic codebook ones — is exactly why the relations composites hold
order. The whole-message "one vector per turn" shape itself was abandoned
(e18: tracking entities by their mentioning sentences distinguishes 68%
vs 59%).
---

# The reactive watch layer (membrane as the model's only I/O)

*Built 2026-10-02: `membrane/watch_agents.py`, TDD-pinned in
`tests/test_watch_agents.py` + `tests/test_script_kinds.py`.*

## The covenant

The model perceives the world through ENTITY STATE, WATCH EVENTS, and
RECALL -- and acts through the MCP lane. No other I/O. The concrete
organs:

## 1. The `operator` entity (always watched)

Planted at boot (idempotent, the `tool:clock` pattern): one entity,
`membrane::operator`, kind operator -- the seat's OWNER. Fires on
EVERY inbound message: the facts stamp (messages counter, last_text
head, last_text_ts) + a position EMA drift toward the message's
sentence vector (the vec arrives free -- the field's apply computed;
zero embed calls). Its "loop" IS the main chat: the operator's
reactions land in YOUR conversation, not a side session. Kill switch
`MEMBRANE_OPERATOR=0`.

## 2. The watch ops (per-entity `watch_ops`)

Configured on any entity doc; the sweep evaluates on message
arrivals + the night/consolidation ticks (`sweep_watch_ops`):

- `{'op': 'away', 'rel': '<eid>'|'operator'|'self', 'y': <dist>}` --
  fire when the entity's chord distance to rel GREW by >= y between
  sweeps (the moved-out event);
- `{'op': 'near', ..., 'z': <dist>}` -- fire on the CROSSING to
  within z (the approach event; staying inside is silent -- re-arm
  on leave);
- `{'op': 'around', 'rel': '*', 'z': <dist>}` -- the sensor form:
  fire when ANY live entity's position is within z; a burst GROUPS
  into ONE event {entered: [eids], left: [eids], entered_n, left_n,
  within_n} -- the 3000-neighbors flood is one event (the grouped
  lists entered/left covenant).

Every op holds a HYSTERESIS cool-down (30s + a changed-geometry
requirement): a hovering entity never spams; sweeps in tests inject
`cooldown_s=0`. State rides the doc (`watch_state`, `watch_last_fire`)
-- persistence is the entity's own save.

## 3. The event queue + the loops (3 + 1)

`WatchEvents`: drop-oldest, cap 64 (a flood keeps the NEWEST -- stale
geometry is not worth reacting to). `LoopRunner`: **3 concurrent
non-operator thinking loops + 1 dedicated operator lane** (the
operator's admit NEVER fails: the main chat's reactive lane cannot
starve behind batch work).

Each fire = ONE thinking loop = a REAL turn against the seat's own
`/v1` (loopback, in-process): the gate, recall, injection, MCP tools,
capture all run -- nothing special-cased except the session/agent and
the task text. The model REACTS (tool calls through the MCP lane:
entity_edit / learn / speak / run) OR IGNORES (a plain verdict text)
-- the turn IS the choice. The verdict stamps the entity
(`watch_last_decision`) and the loop's exchange learns into the field
like any turn. The COMMON CONTEXT is the membrane memory itself (the
recall + the entity reads + the session:now) -- each loop starts
fresh by session, continuity lives in the field.

Kill switch `MEMBRANE_WATCH_LOOPS=0`: the loops never spawn; the
fires still land (the queue + the banner report them).

## 4. The frontend (the loops as agents)

- The loop session titles CARRY THE ENTITY ID: `watch:
  f:membrane/server.py` (the `loop_agent_name` contract) -- the
  operator's ask ("show in its name the id of the entity whose event
  it processes").
- `/agent` advertises the `watch` agent (mode: subagent) --
  `_agent_shapes()` (server.py).
- `/experimental/capabilities`: `backgroundSubagents: true` -- the
  TUI's background-task view arms.
- The loops' tool rounds + reasoning STREAM through the existing
  `message.part.updated` SSE events (the runner publishes every
  part) -- watching a loop = watching any session.
- The BANNER gains the `loops:` line: `2 running (watch:
  f:membrane/router.py, watch: w:gateway), 1 queued` -- the
  ambient mind's load visible on every seat.

## 5. The script engine's kinds + locks (the same-day ask)

Entity scripts split into TWO FAMILIES (night.py
`run_entity_update_script`):

- **CRON** (tick / consolidation -- periodic): *blocking per-entity*
  (one run at a time per entity, a WAIT never a skip) + the OVERRUN
  covenant: a run whose duration exceeded `tick_every_s` BLOCKS the
  next run and NOTIFIES the model's event pool (kind
  `script-cron-overrun`) -- a stuck cron is a decision, not a silent
  drip.
- **WATCHER** (watch / mention / thought / dreamed -- event
  triggered): may block until the event's work is done (the arrival
  is the cadence; no overrun notify); the same per-entity lock (one
  script per entity at a time across kinds).

ALWAYS: any script error notifies the model's event pool (kind
`script-error`) -- errors are events, never stderr dust (the pool:
`srv.events_pool.put`).

## 6. The prerequisite fixes this layer's sweep depends on

- **The deltas index** (`rebuild_delta_index` + the flush
  invalidator): O(1) `deltas_of` -- the night's per-entity drift scan
  was 63M row-checks per 15-min tick (the CPU bomb that froze the
  seats).
- **The dust cap** (`WORD_MIN_LEN=4`): 1-3-letter words get NO state
  (the 'w:showing/w:stay' birth flood); rare short terms ('peers')
  live; common 4+ words die by the boilerplate gate. `get() ->
  None` for dust; the applier skips.
- **No word overlay births**: the night's materialization skips
  `w:`/`p:`/`e:` kinds (words are field entities, not overlay
  subjects; the `[night] born: w:showing` flood stops).
- **The word-delta skip** (from the 472d8c9 fix): words carry no
  per-event deltas; the event-index delta (one row per event under
  `e:<event-key>`) carries the sentence-to-event route.
