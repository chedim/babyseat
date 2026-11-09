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
| 12 | autonode.py: DEFAULT/OLD_FILETREE_WATCH_SCRIPT | script text | MIGRATE | script stored on entity |
| 13 | night.py: _CONCEPT_DRIFT_GETTER | script text | MIGRATE | getter script on entity |
| 14 | night.py: DEFAULT_CLOCK_TICK_SCRIPT | script text | MIGRATE | tick script on clock entity |
| 15 | chat_io.py: _GETTER | script text | MIGRATE | getter script on chat entity |
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
| 31 | ch.py / ch_native.py / native/ (trigger engine, VM) | feature | STAY | trigger engine and loop rule (Q1) |
| 32 | jwtauth.py / auth / owner checks | feature | STAY | owner auth (Q1) |
| 33 | adapters.py: EFFECTS (exec/chat/toys/llm/seat/methods) | feature | STAY | effect adapters (Q1) |
| 34 | server.py / opencode.py / ollama_api.py / mcp_server.py / ui_serve.py | feature | STAY | network surfaces |
| 35 | llm.py / claude_agent.py runtime / quota.py | feature | STAY | model I/O and quota enforcement |
| 36 | ha_bridge.py / peers.py / voice.py transport | feature | STAY | external I/O adapters |
| 37 | restarts.py / update.py / safestderr / storekind | feature | STAY | process lifecycle, runs before field is up |
| 38 | thinker.py | feature | BLOCKED:per-entity heartbeat/scheduler primitive callable from a cliffhanger trigger | loop driver; needs scheduling primitive without GIL script |
| 39 | toybox.py | feature | MIGRATE | toy kinds table (#518-525) plus toy methods; poke/add already method-shaped |
| 40 | watch_agents.py | feature | BLOCKED:LLM-loop spawn + triage lane primitive as a trigger effect | loop host, not just rules |
| 41 | lanes.py | feature | BLOCKED:routing/intercept primitive in trigger engine (#956 calls-as-writes) | 8.3k lines; split after #956 |
| 42 | night.py (consolidation core) | feature | BLOCKED:batch/vector-op primitive in cliffhanger (heavy compute off tick budget) | passes need numeric primitives |
| 43 | goalgraph.py planner/worker logic | feature | BLOCKED:goal-spawn effect + task-state transitions as triggers | triggers migrate now (row 9) |
| 44 | self_loop.py / self_super.py / predict.py / ch_blurp.py logic | feature | BLOCKED:replay harness (#971) for parity | self's guesses need journal replay gate |
| 45 | dreamer.py / forewarn.py / consequences.py logic | feature | BLOCKED:batch/vector-op primitive | numeric passes |
