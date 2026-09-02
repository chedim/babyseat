# Session changes for the ollama-membrane fork port (Sep 2026)

Everything below landed on membrane `main` (uncommitted tree as of this
writing). The fork in `../ollama-membrane` shares the gate/field DNA but has
its own capture/inject path (`membraneRecallInject`, fork doctrine), so when
porting, map each change to the fork's equivalent surface. Nothing here
depends on opencode specifics except where noted.

## 14. The "I" track (WHERE WE ARE) + entity tracks + split authority

**Split authority with the fork (the hollow-world fix).** The old full
stand-down left BOTH seats silent when the fork's own recall was thin: this
seat injected nothing, the fork had nothing. The covenant is now SPLIT —
the fork owns CAPTURE (this seat's learn/capture stays off; no second
writer), but this seat keeps RECALL (a read-only injection is not a
writer). Wire: `_v1_chat`'s `ctx = self._memory_block(...)` is unconditional;
on `fork_owns_field` the block gets a provenance marker ("recall from the
operator's field; read-only"). Fork mapping: when the fork is present,
Python-side learn/capture stays off, recall stays on.

**The "I" track — the WHERE WE ARE header.** Every chat turn carries a
computed position header (zero model calls, `opencode._where_we_are`):
- `focus:` — the momentum EMA vs the nearest open commitment (cos >= 0.45
  names the commitment; else "mid-conversation").
- `open commitments:` — up to 2 open intentions (the "I said I would"
  track; closure is only ever the operator's explicit `membrane: fulfill`).
- `field:` — size + trusted % + pending human reviews.
- `track:` lines — the model's OWN entity tracks (below).
Deliberately NOT a persona: the model's "I" is its position and
commitments. The header answers "where am I"; memories answer "what do I
know". Fork mapping: prepend the same computed block in the fork's
`membraneRecallInject`.

**Entity tracks (entities.py EntityStore, EVENT-based).** The model's
banner lane, managed via MCP:
- `membrane_watch_entity` (key, boundary?, label?) — a track shows the
  entity's live safe-cell count in WHERE WE ARE every turn (constant
  tracking); a boundary turns it into an event trigger: `enter N` fires
  once when the count rises to N+, `exit N` fires once when it falls
  below. STATE MACHINE semantics: one alert per transition; re-entry
  re-arms. No boundary = count-only track. Key forms: `files:router.py`,
  `models:glm-5.3-flash:cloud`, `symbols:<qualname>`, `shell:git`,
  `ha:sensor.x`.
- `membrane_unwatch_entity` (key or kind prefix) — remove.
- `membrane_list_tracks` — the model's view of its own banner (key,
  boundary, inside/outside state, pending alerts).
- Proxy endpoints the tools relay to: POST `/membrane/entity-track`,
  POST `/membrane/entity-track/remove`, GET `/membrane/entity-track/list`.
  Tracks persist in `<field>.entities.json` (`tracks` key).
- The state machine is transition-validated: [enter,0,exit,re-enter] ->
  alerts [ENTERED, -, EXITED, ENTERED]; a level-check version (the v1
  "op N" design) was replaced because a static threshold re-fires every
  turn.
- NOT ported from cortex (deliberate): geometric nudging (stored vectors
  warped by tracked forces) and `consolidate_long_term_vectors`. The
  membrane-shaped substitute is momentum skew (warps the QUERY, never the
  memory). If ported later: idle-cortex experiment, re-ask rate as the
  honest gauge.

**The totality view (the surface log).** Every chat turn records its full
API surface into a ring (`srv._surface_ring`, cap 12): the query, verdict,
route, sent blocks (recall / where-we-are / peer / watch / intentions /
review / ledger — each with char count + preview), reply head, tool-calls
flag, learned index. Readers: `membrane: surface` (in-chat, pre-gate) and
`/membrane/state`'s `surface` key. The operator's answer to "what does
membrane send/receive from the model".

## 15. The vague filter (entity-as-content, the echo-junk fix)

Greeting/ack cells ("ok, restarted, welcome back!") passed the old
token-count check and became recall noise (a restart-shaped query surfaced
them at sim 0.80; the model then echoed its own context — the "gibberish"
incident). `_is_vague` now:
- treats a named referent as content (the entity extractor's harvest — no
  second word list; the same test recall uses),
- keeps the ? / digit / path / Capitalized / info-stem saves,
- screens long-but-empty turns: if EVERY word is a stopword or an ack
  token ("all right, we are back!"), the turn is vague regardless of
  length; one content word (>3 chars, not stop/ack) makes it learnable.
Validation set: 17/17 (greetings vague, real instructions learnable).

## 9. Boot speed: N1QL -> KV hydrate + keys snapshot (5.7s -> 2.8s)

The couchbase boot hydrate streamed every doc through the query service's
JSON parser (~0.4ms/row -> 5.7s at 5.8k docs). Three fixes in
`membrane/field.py` `load_couchbase`:

1. **id-only N1QL scan + batched KV `get_multi`** — the query service only
   returns `meta().id`; payloads come from KV (the bulk path). One big
   `get_multi` (all keys; ~1s), chunked-500 fallback on failure.
2. **`.value` not `content_as[dict]`** — the transcoder already decodes to
   a dict; `content_as[dict]` re-ran the serializer on every row (7.7k
   `json.loads` for 6.4k docs). Iterate `got.results` and use `res.value`
   directly.
3. **keys snapshot** — `<field>.cb-keys.json` written by `save()` whenever
   the live key set changes (atomic tmp+rename); boot reads it and skips
   the ~2s N1QL id-scan entirely (falls back to the scan when absent/stale).

Same treatment for the ephemeral tier (id-scan + `get_multi`, 1000/batch).
Measured: 5.7s -> 2.8s at a field 12% LARGER.

## 10. The entity layer (membrane/entities.py + entity_synthesis.py)

The shared symbolic layer with cortex-experiment — the missing precise
retrieval axis (95% of cells were capture junk; cosine alone sat at 0.7-mush
for term queries).

- **Deterministic tagging** (`extract_entities`, $0, capture time): files /
  dotted symbols / model tags / urls / shell verbs / HA ids stamped into
  `extra["entities"]` on every new cell (`runtime._learn`). AST-grounded by
  `codegraph.refine_entities` when the server has a graph: prose noise
  dropped, bare names promoted to full qualnames (`route` ->
  `router.Registry.route`), non-repo files dropped.
- **Entity-boosted recall** (`rag._memory_block`): the query's entity keys
  invert the field (`entity_index`: `kind:value` -> safe cell idxs);
  hits enter the candidate pool at pseudo-sim 0.99 (term evidence, floor
  0.65) and merge with vector top-k through the same MMR/echo screens.
  Measured: a query naming `ling-3.0-flash-fin:free` reaches 37 exact cells
  (vector recall alone: 0 usable hits).
- **Idle-cortex synthesis** (`entity_synthesis.synthesize_from_cluster`):
  after each compaction, the SAME cluster derives abstract entities
  (decisions/failure-modes/components, max 5, JSON-validated against the
  shared class vocabulary) on the LOCAL idle cortex — cortex's cron-job
  pattern at compactor cadence, zero chat-turn cost, zero cloud cost.
  Wire: `rag._cycle_inner` step 4 -> `srv.entity_sink`.
- **EntityStore** (three homes, one contract): RAM + `<field>.entities.json`
  (atomic) + `cortex-core._default.entities` (the cortex QuantumEntity
  collection, doc id `membrane::<kind>:<slug>`, MERGE-on-collision: union
  properties/links/derived_by — never a second doc). Read-through at first
  embedder resolution pulls cortex's entities into the same layer.
  Wire shape: `{meta.type: "QuantumEntity", type: "entities",
  vector_data, data: {class, properties}, state_space.position.
  semantic_embeddings}` — the SAME mxbai-embed-large space as cortex.
- **Shared class vocabulary** (`entities.CLASSES`): Component, Decision,
  FailureMode, AIModel, Tool, Endpoint, Config, Goal, Sensor, Actuator —
  the mergeable contract between the two systems' extractors.

Fork mapping: the fork's capture path tags cells the same way; its
`membraneRecallInject` gains the entity-union step; the fork's idle loop
calls `synthesize_from_cluster` with its own `_ollama_chat`.

## 11. Idle-cortex split (`--idle-cortex-model`, default granite3.3:8b)

Background loops (compactor distillation, entity synthesis) distill on a
LOCAL model by default — a stray stuck background seat can never burn cloud
tokens (the three-seats incident). `--compact-model` pins BOTH lanes (the
chat-stamped cortex + idle); `--idle-cortex-model` overrides just the idle
lane. The judge/cortex keep following the operator's chat selection (bounded
1-per-turn). Wire: `srv.idle_cortex_model` in rag.py + opencode.py; the
compactor calls `srv.idle_cortex_model`.

## 12. The autoselect lanes (@-free) + hard operator picks

- Sentinels renamed `autoselect` / `autoselect-local` (pickers mangle `@`;
  @-spelled forms stay accepted aliases via `normalize_lane`/`LANE_TOKENS`).
- A specific model selection is a HARD pick: `route()` returns that record
  (reason `operator pick`); routing only happens on lane selections. The
  whole route block is gated on `autoselect`.
- The lane's floor policy rides as a PARAMETER (`force_local`) — the
  registry's `prefer_local` is never mutated (the stashed version leaked:
  a raise mid-route left it flipped).
- The sentinels must be advertised in BOTH `/v1/models` AND the generated
  opencode.json `models` map (opencode's picker reads the config map, NOT
  /v1/models).
- `--cloud-budget` default `0.0` = the free-tier covenant: rungs priced
  <= 0.1 stay reachable, paid rungs need an explicit budget (both `route()`
  and `_ladder_under()`).
- `context_length` on ModelRecord (from provider listings / `/api/show`
  for the fork) + `context_holds` + `Registry.estimate_tokens` — a HARD
  filter in route/ladder/failover.

## 13. Empty-answer failover

OpenRouter free tiers sometimes answer HTTP 200 with an empty body — which
the ladder counted as success, so a flaky rung kept winning and the user saw
silent turns. `_resp_has_content` (content OR tool_calls OR reasoning OR
finish_reason=tool_calls; unparseable = empty) gates the buffered path:
empty -> `note_outage` (5min cooldown) -> `_next_rung`; exhausted -> the
honest field-only banner. Streaming path can't retry (bytes already tee'd)
but cools the silent rung down for future turns.

## 1. Pure observer by default (`--replay-sim` 0.97 -> 0.0)

**Decision:** membrane must never answer the user from memory. Its job is
state + history + context, not response synthesis. Replay (serving a stored
`ex.action` as the actual chat answer with no model call) produced stale,
uninformative answers and hid live-model improvements.

- `membrane/opencode.py` `--replay-sim` default is now `0.0` (was `0.97`).
  `0` = pure observer: every turn reaches the model; memories ride along as
  injected context only.
- **Settle-replay observer guard**: the auto-settle loop (`repeat-k` in
  `runtime.gate_turn`) maps a stuck unlearned repeat to verdict
  `settle-replay`, which the proxy served verbatim. The proxy now rewrites
  `settle-replay -> escalate-with-hint` when `replay_sim <= 0` (observer):
  the near match rides as a HINT, the model still answers. See the
  `settle-replay and replay_sim <= 0` block in `_v1_chat`'s delivery section.
- The `act-replayed` / `settle-replay` delivery paths remain for operators
  who opt back in with `--replay-sim 0.97`.

Fork mapping: wherever the fork replays stored actions (its `/api/chat`
absorb path), gate it behind the same `replay-sim > 0` check.

## 2. Zero-dollar default cloud budget (`--cloud-budget` None -> 0.0)

**Decision:** background/idle membranes must not be able to burn paid tokens
by default (the three-stuck-seats incident). A **$0 budget is a free-tier
covenant, not a blackout**: rungs priced <= 0.1 (the `:free` pins) stay
reachable; paid rungs require an explicit `--budget`.

- `membrane/opencode.py` `--cloud-budget` default `0.0`.
- `membrane/router.py`: in BOTH `route()` and `_ladder_under()`, the
  exhausted-budget branch special-cases `cloud_budget <= 0`:
  `clouds = [r for r in clouds if r.price() <= 0.1]` instead of dropping all
  clouds. Free pins keep the cloud escalation alive; paid rungs drop out.

Fork mapping: `FREE_TIER_PRICE = 0.1` in router.py + the budget check in
`route()`/`_ladder_under()`.

## 3. Empty-answer failover (the "free rung answered 200 with no body" bug)

OpenRouter free tiers sometimes return HTTP 200 with an empty body. The
ladder counted that as success -> the flaky rung kept winning -> the user saw
three silent turns in a row.

- `membrane/opencode.py`:
  - new `_resp_has_content(resp_bytes)` (module level): a 200 is a real
    answer only when it carries content, tool_calls, finish_reason
    `tool_calls`, or reasoning content. Unparseable = empty.
  - buffered path: routed turns now LOOP (bounded 6 attempts): on empty,
    `router.note_outage(model, "empty answer (200, no content)")` (5 min
    cooldown), walk `_next_rung`; ladder exhausted -> `_serve_field_only`
    honest banner instead of silence. Non-routed turns keep verbatim
    passthrough.
  - streaming path: bytes are already tee'd, so no failover — but the silent
    rung gets `note_outage` + stderr log, so NEXT turns skip it.
- Effect: `inclusionai/ling-3.0-flash-fin:free` cools down on first empty
  answer and traffic moves on.

Fork mapping: the fork's NDJSON/stream synth path needs the same
"contentless 200 = rung down" rule.

## 4. Context-window HARD filter (router)

The router used to send oversized requests to small-window models (400s from
the backend, or silent truncation).

- `membrane/router.py`:
  - `ModelRecord.context_length: int` (persisted in `to_doc`/`_load_state`;
    provider listings carry it via `auth.fetch_models_info` ->
    `(id, is_free, ctx_len)` triples).
  - `Registry.context_holds(rec, ctx_tokens)` — hard filter; 0 on either
    side = conservatively routable (filter only fires on real data).
  - `Registry.estimate_tokens(messages, reserve=2048)` — ~4 chars/token over
    content + tool_calls JSON, + completion reserve.
  - `route()` and `ladder()` take `ctx_tokens: int = 0`; every candidate
    passes `context_holds`. Failover (`_next_rung` in opencode.py) estimates
    tokens from the outgoing `up["messages"]` and passes them in.
  - The delegate path estimates from the task text.

Fork mapping: the fork registers Ollama models, not provider listings —
pull `context_length` from the model's `/api/show` (or the Modelfile) at
refresh time instead of the listing row.

## 5. Specific model selection = HARD pick (router only routes on lanes)

**Decision:** selecting a concrete model in the picker must be law. The
router routes ONLY when the operator selected a lane sentinel.

- `membrane/router.py`:
  - `normalize_lane(ceiling)` — canonicalizes `autoselect` /
    `autoselect-local` / `@autoselect` / `@autoselect-local` / `@explore`.
  - `route()`: a selected name that exists in `self.records` returns THAT
    record (reason `operator pick: <name>`), never a re-pick. Only unknown
    names degrade to the old price-ceiling semantics. Lane sentinels
    (`AUTO_LANES`) route with no ceiling; `@explore` farms evidence.
  - `route()`/`ladder()`/`_floor()`/`_ladder_under()` take the lane's floor
    policy as a PARAMETER (`force_local` / `lane_floor_local`) — **the
    registry's `self.prefer_local` is never mutated** (the stashed version
    had a leak: a raise mid-route left the registry flipped to prefer-local
    forever).
  - Sentinels are @-free (`autoselect`, `autoselect-local`) because pickers
    mangle `@`; @-spelled forms stay accepted aliases (`LANE_TOKENS`).
- `membrane/opencode.py`: the whole route block is gated on `autoselect`
  (lane selected AND router present); `/v1/models` and the generated
  opencode.json `models` map both advertise the sentinels (owned_by
  `membrane-router`). NOTE: opencode's picker does NOT read `/v1/models` —
  any new selectable model must be added to the config map too.

## 6. The monologue banner (thinking channel) + flow + toggle

- One vitals line from gate-time verdict stamps (no second SVD):
  `[membrane] route=<v> sim=<s> field=<n> eff_rank=<r> free_energy=<fe> safe=<pct>% novelty=on|off(<rr>)`
  (eff_rank derived from `spectrum_entropy` via exp; novelty = the rank-ratio
  gate signal).
- `gave N slice(s) to the LLM: "..."` — topics of injected memories (parsed
  from the ctx block by `_ctx_gave_summary`).
- `learned #idx: <situation clip>` / `learn: dedup'd into #N (no new cell)` /
  `learn: repaired demoted #N` / `learn: variety-gate skip` / `learn:
  skipped (vague)` / `learn: kept ground cell #N` / `learn: candidate #N
  dedup-promoted` — every `_learn` branch in `runtime.py` appends a
  `gate_notes` entry; the banner splits learn-notes from other gate notes.
- Toggle: `membrane: monologue [on|off|toggle]` canonical frame, pre-gate in
  opencode.py (zero model calls, audited `monologue-toggle`); srv.gate_monologue
  flips live. TUI command `.opencode/command/monologue.md` sends the frame.

Fork mapping: fork doctrine says it stamps its own [membrane] lines — port
the vitals + gave/learned lines into whatever the fork streams as reasoning.

## 7. Richer injects (this session's quality pass)

Field census that motivated it: 95% of cells are capture machinery (tool
calls/results/reasoning — learned for evidence, excluded from recall);
~5% (283 cells) are recall-able prose. Injects were sparse and
context-poor.

- `membrane/rag.py` `_memory_block`:
  - Sim stamped on EVERY entry: `Q: ...\nA: ... (sim 0.72)` so the reader
    calibrates trust per memory.
  - Contentless situations (short fragment, long action) flip to
    action-first: `Memory (sim 0.71, [src: ...]): <action> [situation:
    <frag>]` — "would that make sense?" as a bare Q told the reader nothing.
  - Block header now explains the sim semantics ("high sim = directly
    relevant, low = background only").
- Doctrine (in fork terms: whatever system text describes recalled
  memories) unchanged otherwise — the `Recalled exchanges:` guidance in the
  doctrine should gain the same sim-calibration sentence.

## 8. Free-tier expert pinning (auth.py)

`fetch_models_info` returns `[(id, is_free, ctx_len)]`; `is_free_model`
detects `:free` suffix or pricing==0. `router.add_cloud_models(...,
free_ids=...)` pins free rungs to `min(price, FREE_TIER_PRICE=0.1)` — they
become the first escalation above locals under the $0 budget. Persisted-rung
reconciliation refreshes stale price pins for the provider's own rungs.

## Port checklist

1. `--replay-sim` default 0.0 + settle-replay observer guard.
2. `--cloud-budget` default 0.0 + free-tier covenant in budget branch (both
   route() and ladder()).
3. `_resp_has_content` + empty-answer failover (buffered + stream cooldown).
4. `context_length` on ModelRecord + `context_holds` + `estimate_tokens` +
   threading through route/ladder/failover.
5. `normalize_lane` + hard-pick + parameter-passed lane floor (no
   prefer_local mutation) + @-free sentinels + config-map advertising.
6. Monologue: vitals line + gave/learned + `membrane: monologue` toggle.
7. Inject format: sim stamps + action-first for fragmentary situations +
   sim-calibration doctrine sentence.