# Native-to-field inventory (#980)

Classes: MIGRATE, STAY (native, with reason), BLOCKED:<primitive>.
Source: issue #980 body + survey of origin/main (membrane/*.py). Draft; item list is the issue's examples plus seed/literal sites found by grep. Feature-module rows are classified at module level; per-function split is a follow-up.

| # | Item (file: symbol) | Kind | Class | Reason / primitive |
|---|---|---|---|---|
| 1 | night.py: seed_clock_entity | seed | MIGRATE | plain entity seed; ship in seed pack |
| 2 | night.py: seed_executor_entity | seed | MIGRATE | plain entity seed |
| 3 | night.py: seed_interfaces | seed | MIGRATE | plain entity seed |
| 4 | fstree.py: seed_classes | seed | MIGRATE | class entities |
| 5 | autonode.py: seed_filetree_entity | seed | MIGRATE | entity seed; watch script rides with it (see 12) |
| 6 | language.py: seed (SEED_ADJECTIVES) | seed | MIGRATE | adjective table is data |
| 7 | language.py: seed_executor | seed | MIGRATE | entity seed |
| 8 | self_proc.py: seed | seed | MIGRATE | entity seed |
| 9 | goalgraph.py: TRIGGERS + seed | literal | MIGRATE | field trigger definitions already run on the field |
| 10 | graphbanner.py: SEED + seed | literal | MIGRATE | needs versioned seed pack (Q3: edited rule kept forever strands fixes) |
| 11 | entities.py: JUNK_ANCHOR_SEEDS, _seed_junk_anchors | literal | MIGRATE | anchor texts are data; embedding of them stays native |
| 12 | autonode.py: DEFAULT/OLD_FILETREE_WATCH_SCRIPT | script text | MIGRATE (Q2 entity script) | script stored on entity; missing primitive: file-event trigger in cliffhanger. Never imported from a peer (Q5) |
| 13 | night.py: _CONCEPT_DRIFT_GETTER | script text | MIGRATE (Q2 entity script) | getter script on entity; missing primitive: vector/numeric read op in cliffhanger |
| 14 | night.py: DEFAULT_CLOCK_TICK_SCRIPT | script text | MIGRATE (Q2 entity script) | tick script on clock entity; missing primitive: tick-store write from a cliffhanger trigger |
| 15 | chat_io.py: _GETTER | script text | MIGRATE (Q2 entity script) | getter script on chat entity; missing primitive: read-through getter in cliffhanger |
| 16 | router.py: seed_imprints / ensure_seeded | seed | MIGRATE | imprint texts are data; vector compute native |
| 17 | lanes.py: _INTERCEPT_SEEDS / _ANTI_SEEDS / _seed_topics | literal | MIGRATE | phrase tables are data |
| 18 | ch_blurp.py: SKILLS, TOY_ORDER, TOY_UNDER | literal | MIGRATE | skill tree (#518-525 skill tree table) |
| 19 | toolset.py: tool surface/SCRIPT_KINDS | literal | MIGRATE | #518-525 tool surface table |
| 20 | decision_map.py: STATUSES/VAR_TYPES/EDGE_KEYS | literal | STAY | validation invariants of the card model; changing them breaks the engine |
| 21 | eids.py: KINDS / entity_block CANON_KINDS | literal | STAY | id grammar, bootstrap-level |
| 22 | entities.py: CLASSES | literal | STAY | class set the store depends on |
| 23 | entity_block.py / conv_banner.py header and MORE strings | prompt text | MIGRATE | #518-525 prompts table |
| 24 | claude_agent.py: hints, CEILING/CONTINUE messages, DEFAULT_MODELS | prompt text | MIGRATE | prompts/model rungs tables |
| 25 | efgate.py: AB_JUDGE_PROMPT | prompt text | MIGRATE | prompts table |
| 26 | consequences.py: HORIZONS, SNAP_KEYS, WRITE_TOOLS | literal | MIGRATE | dials; read by native code at call time |
| 27 | attention.py: ALERT_EXEMPT; claims.py windows/verbs | literal | MIGRATE | dials/data tables |
| 28 | night.py: SCRIPT_* limits (timeout, failures, retry) | literal | STAY | script sandbox budget; cannot be governed by a script it limits |
| 29 | scripts.py / jobs.py script runner | feature | STAY | executes field scripts; bootstrap |
| 30 | efstore.py / efield.py / cblstore / cblite / ann / vsa (store, journal, vectors) | feature | STAY | store and journal; bootstrap (Q1) |
| 31 | ch.py / ch_native.py / native/ (trigger engine, VM machinery; never migrate the VM) | feature | STAY | trigger engine and loop rule (Q1) |
| 32 | jwtauth.py / auth / owner checks | feature | STAY | owner auth (Q1) |
| 33 | adapters.py: EFFECTS (exec/chat/toys/llm/seat/methods) | feature | STAY | effect adapters (Q1) |
| 34 | server.py / opencode.py / ollama_api.py / mcp_server.py / ui_serve.py | feature | STAY | network surfaces |
| 35 | llm.py / claude_agent.py runtime / quota.py | feature | STAY | model I/O and quota enforcement |
| 36 | ha_bridge.py / peers.py / voice.py transport | feature | STAY | external I/O adapters and dependency drivers (Q1) |
| 37 | restarts.py / update.py / safestderr / storekind | feature | STAY | process lifecycle, runs before field is up |
| 38 | thinker.py | feature | BLOCKED:per-entity heartbeat/scheduler primitive callable from a cliffhanger trigger | loop driver; needs scheduling primitive without GIL script |
| 39 | toybox.py | feature | MIGRATE | toy kinds table (#518-525) plus toy methods; poke/add already method-shaped |
| 40 | watch_agents.py | feature | BLOCKED:LLM-loop spawn + triage lane primitive as a trigger effect | loop host, not just rules |
| 41 | lanes.py | feature | BLOCKED:routing/intercept primitive in trigger engine (#956 calls-as-writes) | 8.3k lines; split after #956 |
| 42 | night.py (consolidation core) | feature | BLOCKED:batch/vector-op primitive in cliffhanger (heavy compute off tick budget) | passes need numeric primitives |
| 43 | goalgraph.py planner/worker logic | feature | BLOCKED:goal-spawn effect + task-state transitions as triggers | triggers migrate now (row 9) |
| 44 | self_loop.py / self_super.py / predict.py / ch_blurp.py logic | feature | BLOCKED:replay harness (#971) for parity | self's guesses need journal replay gate |
| 45 | dreamer.py / forewarn.py / consequences.py logic | feature | BLOCKED:batch/vector-op primitive | numeric passes |

## Counts
45 rows: MIGRATE 25 (of which 4 as Q2 entity scripts, rows 12-15), STAY 13, BLOCKED 7 (each names its primitive).

## Design question outcomes
- **Q1 decided:** native = store/journal, trigger engine, loop rule, owner auth, effect adapters, VM machinery and dependency drivers. The VM is never migrated.
- **Q2 decided:** cliffhanger by default; an entity script only where a primitive is missing, each recorded as a missing primitive (rows 12-15).
- **Q3 decided:** versioned seed pack; release changes must not be stranded by a seat edit (row 10).
- **Q4 open:** owner-only writes, ledger and revert-to-shipped; only the write-gate/revert design waits on a decision.
- **Q5 decided:** peer import is script-free (#226); core programs arrive only from the release.

## Recall policy rows (r1 of the recall-policy goal)

Policy steps of `recall_answers` (entity_block.py), `recall_banner.py`, `recall_rerank.py` and `recall_lines` (conv_banner.py). Field target = the method or entity data that would hold the step. Class: FIELD (migrates) or NATIVE (stays).

| # | Item (file: symbol) | Field target | Class |
|---|---|---|---|
| R1 | entity_block.py: recall_answers (orchestration: lanes, fuse, row cut) | method `recall.answers` on class `recall` | FIELD (calls the native primitives below) |
| R2 | entity_block.py: direct/vector lane (nearest sentences of earlier asks, k*36 width) | method `recall.lane_direct` | NATIVE: vector search over the store (row 30) |
| R3 | entity_block.py: word lane (lexical.py BM25 on rare words) | method `recall.lane_word` | NATIVE: BM25 index |
| R4 | entity_block.py: entity lane (ENTITY_POOL_MAX newest events of named entities) | method `recall.lane_entity` | FIELD: entity pool is graph data; pool cap is a dial |
| R5 | entity_block.py: lane-choice rules (person lines join direct lane only; _title_like exclusion) | entity data `recall.lane_rules` | FIELD |
| R6 | entity_block.py: rrf (reciprocal rank fusion) | method `recall.fuse` | FIELD (needs batch primitive: sort/merge of lists) |
| R7 | entity_block.py: best-similarity-among-lanes for rows found twice | method `recall.fuse` | FIELD |
| R8 | entity_block.py: annotate_store_keys | method `recall.annotate` | NATIVE: store keys |
| R9 | recall_banner.py: BANNER_N, POOL, NO_TOPIC_MIN, FACT_CHARS, RECENT_TURNS, HISTORY_TURNS, DEDUPE_JACCARD | entity `recall:dials` | FIELD: literals as dials |
| R10 | recall_banner.py: content_words, _stem, has_topic (no-topic rule) | method `recall.topic` | FIELD (string ops; vector/batch primitive for bulk) |
| R11 | recall_banner.py: history_texts (previous turns added to query) | method `recall.query_text` | FIELD |
| R12 | recall_banner.py: recent_ns (skip current chat turns as echo) | method `recall.echo_skip` | FIELD |
| R13 | recall_banner.py: with_speakers, _names | method `recall.speakers` | FIELD |
| R14 | recall_banner.py: features (sim, inv_rank, bm25, overlap) | method `recall.features` | FIELD (batch primitive: per-row numeric map) |
| R15 | recall_banner.py: DEFAULT_WEIGHTS / LEGACY_WEIGHTS | entity data `recall:weights` | FIELD: fitted data |
| R16 | recall_banner.py: load_weights / save_weights | entity property read/write | FIELD |
| R17 | recall_banner.py: score (logistic over features) | method `recall.score` | FIELD (batch primitive: dot product / sigmoid) |
| R18 | recall_banner.py: rank, pick (cut to BANNER_N, skip set, prev-set carry) | method `recall.pick` | FIELD |
| R19 | recall_banner.py: fact_line, line_of, _sig/_alike (nearest sentence, clip, Jaccard dedupe) | method `recall.render_line` | FIELD (batch primitive: set-similarity) |
| R20 | recall_banner.py: build, render (per-step timing in state) | method `recall.build` | FIELD |
| R21 | recall_rerank.py: scores/serve/_predict/_load/warm (ONNX cross-encoder inference, lazy load) | none | NATIVE: model inference |
| R22 | conv_banner.py: recall_lines (banner hook, session cache, never breaks banner) | trigger on banner build calling `recall.build` | FIELD (hook) |

Rerank policy (when to rerank, `rank(rerank=True)`, fall back to scores when the runtime is missing) is FIELD data on `recall:dials`; only inference (R21) is native.

Native-only parts: vector search and store (R2, row 30), BM25 index (R3), store keys (R8), reranker inference (R21).

Batch/vector primitives needed (BLOCKED until they exist in cliffhanger): (1) vector nearest-sentences query, (2) BM25 query, (3) list sort/merge by key (rrf), (4) per-row numeric map over a row set (features), (5) dot product plus sigmoid over a row set (score), (6) set-similarity (Jaccard) over a row set (dedupe).
