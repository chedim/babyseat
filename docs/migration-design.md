# Data migration of the release swap: v0.7.53 seat to the cliffvm seat (design note, #1143, 2026-11-14)

Status: BUILT. Only the chat history of the 0.7 Python seat is migrated (operator scope decision, 2026-11-14); the old field is kept as a read-only source the model pulls from on demand (`Help:legacy`). The cutover is automatic (`legacy_cutover.c1f`, Phase 1b) over the history import (`legacy_import.c1f`, Phase 1). The earlier whole-field design (convert on touch, reverse path, converter versions, eid-less rows) was never built and has been removed; it is in git history. Sizes are `sum(length(body))` (characters, not bytes); class counts are exact for the 24 largest segments, approximate beyond.  Sources: `PY53` = `git show v0.7.53:membrane/<file>` (the
oracle tag), `N/` = `native/`, `P/` = `membrane/packs/`. Numbers marked **[copy]** were measured on a READ-ONLY COPY of a real seat's store:
`rosetta-check/.membrane/field.sqlite/field.sqlite3.good-121525` (a 2026-11-11 12:15 snapshot of the Rosie seat, 615 MB, WAL empty), copied to the
session scratchpad and opened with `immutable=1`. No live store was opened; the main seat's directory was only sized (`du`: 14 GB, includes its backups).

## Phase 1b: the automatic cutover (built 2026-11-14, #1143, `membrane/packs/legacy_cutover.c1f`, tests `native/tests/legacy_cutover_1143.rs`)

Operator: "history cutover: automatic". A user upgrading from the Python seat starts the new seat once and finds the history in the ONE chat. At the start the seat finds the old store, declares `SqlSource:legacy` (read-only, immutable), and imports in the
background, newest session first and, inside a session, the newest questions first, in small batches whose entities leave the engine at once. Resumable (the per-session ledgers), once (`Legacy:cutover.state` done), silent when there is no old store.
- **Search order** (first hit wins; each place holds `field.sqlite/field.sqlite3`): the fact `legacy_path` (a file or a folder; named, it is the only place, a miss is an error with the path) or a source already declared `legacy`; then `<seat_data_dir>`, `<seat_data_dir>/.membrane`,
  `<seat_cwd>/.membrane` (the Python seat's cwd folder, `cli.py DATA_DIR`), `<seat_home>/membrane/.membrane` (`restarts.py` default), `/config/membrane/.membrane` (the Home Assistant component's seat folder). The old `MEMBRANE_DATA_DIR` env is not seen by c1f: the launcher passes it as `legacy_path`.
- **Which sessions** (kinds on the ledger, `session_kind`): `chat` = old `agent` build or speaker, imported; `absorbed` (title "absorbed claude session") and `background` (every other agent) are `skipped`: no ChatSession, no messages, no time rows. The progress counts chats only.
- **What the user sees**: the history route carries `import` (a plain line, or the reason of an error) while it runs; the chat view shows it above the composer and keeps "Load older turns"; the Settings Import group has the read-only row "Earlier conversations".
- **Engine findings** (measured on the real store, 2026-11-14): (1) a name bound above a FOR is copied for every statement of its body (`Fields::run_writes`/`scope`): the page setter no longer binds `rows`/`tools`/`texts` (385 GB of allocation in 45 s before, a page took seconds);
  (2) `Fields::unload` retained over every bookkeeping table per entity: `unload_many` does one pass per batch; (3) the in-memory journal kept every journalled query receipt (megabytes a page) for the life of the process: a line over 256 KiB is kept without its value in RAM (the stored line is whole).
- **Measured, Memo's real store (1,605 sessions, 5.5 GB, opened in place immutable), release build, private seat on port 4521, fake claude**: 17 chat sessions, 2,178 turns imported (=ledger and store counts), 1,588 sessions skipped; door up 0.9 s, recent history visible after about 6 s (the headers),
  the whole import 108 s (batch 5), peak RSS about 610 MB, turn start 0.03 to 0.9 s and answered 0.02 to 2.7 s during the import, store 612 MB; a SIGTERM at 9 of 17 sessions and a restart finished it from the ledgers (9 of 17 on the first status), no duplicates.

## Phase 1: history import (built 2026-11-14, #1143)

**Scope (operator decisions 2026-11-14, fixed).** ONLY the chat history (sessions and messages) is migrated into the new field. Nothing else is converted: no entities, no recall data, no vectors, no goals, decisions or options, no
second-phase converter. The old store is kept as a READ-ONLY file, declared as `SqlSource:legacy` (the Sql driver, `docs/cliffvm.md` "The Sql driver"), and the MODEL pulls from it on demand (`Help:legacy`). The old store is never written;
the reverse of the whole migration is switching the seat back (nothing to build). No model runs during the import.

**Where it lives.** `membrane/packs/legacy_import.c1f` (all rules, c1f; in `membrane/apps/barest.packs`), `membrane/packs/legacy_persons.c1f` (the Person entities of named owners: a seat with persons only), `Help:legacy`.
Tests: `native/tests/legacy_import_1143.rs` (host level, a small fixture in `native/tests/legacy_fixture/`), `native/tests/legacy_routes_1143.rs` (the seat binary, the page's routes against `tests/golden/session/`, and the ignored
real-store measurement). No new builtin, no host hook.

### What is imported, and the mapping (old row to new entities)

| old (`<store>`, tag v0.7.53) | new | notes |
|---|---|---|
| `c_sessions` row `ses_<hex>` or `xcl_<uuid>`, `{s: {id, title, owner, time: {created, updated}, ...}}` (`server.py:287-330`) | `membrane::Chat:session:<sid>` ChatSession `{stitle, born, upd (epoch ms, as they were), owner, legacy_src: {conv, sid, at}}` | the id is KEPT (a bookmark still opens); the rest of the old row (directory, slug, agent, tokens, summary, version) is not carried: the page's view reads the new seat's own facts |
| (the same row) | `membrane::Legacy:session:<sid>` the LEDGER `{state, converter, old_rows, rows, cursor, unmapped, unmapped_rows, dropped_parts, imported_at, reason}` | one per old session, made with the header |
| `c_messages` user row `{n, sid, mid, row: {info, parts}}`, key `<sid>\|<mid>` (`server.py:486-520`) plus the assistant rows whose `info.parentID` is its `mid` | ONE `membrane::Chat:session:<sid>:message:<created ms>-<n padded to 8>` (docs/chat-keys.md) ChatMessage `{text (the user text parts joined by newlines), at (created/1000), sid, pick (info.model.modelID), tool_ids, tools_run, aborted, legacy_src: {conv, sid, mid, at, n, asst_at, asst_done, answers}}` and its `:body` SemText | the id is derived from the user row (`info.time.created` and `n`): no alias table; `n` is unique in the real store (1,137 of 1,137) |
| the answering assistant rows (in order of `n`) | `...:turn` ChatTurn `{message, queued_at, sid, state (done, or error: see below), ended (the last `time.completed`), usage {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens} (SUMS over the answering rows), cost (sum)}` | `started` is NOT written (see below); `queued` is never written, so no turn starts |
| the text parts of those rows, in order | `...:turn:response` ChatResponse `{text (joined by a blank line), error}` and, when there is text, its `:body` SemText | a turn whose last answering row has an `info.error` is `error`; its response is the text, else the error message |
| each `tool` part | `...:turn:tool:<callID, else the part id>` ChatTool `{name, input (state.input), result (state.output, else state.error)}` | the tools in call order are `tool_ids`; a tool in `error` status shows as completed with its error text as the output (the page has no error status for a tool) |
| a user row nobody answered (9 of 477 [copy]) | a ChatTurn in state `error` whose response says `(no answer was recorded in the previous store)` | visible, never a half-made message |
| an assistant row whose parent is not a user row of its session (9 [copy]) | NOT a message: the ledger `unmapped` count and `unmapped_rows` `[{mid, n}]` | a visible note on the session, never silently dropped |
| a part that is not text, tool or a step marker (19 `reasoning` parts [copy]) | NOT carried: the ledger `dropped_parts` counts them | `step-start` and `step-finish` carry only numbers that are in the turn's usage |
| the user row's non-text parts (none in the copy) | not carried | counted nowhere yet: none exist |

The invariant the tests keep: for every session `rows` (old rows consumed: user rows plus their answering rows) `+ unmapped = old_rows`; on the real copy 1,128 + 9 = 1,137.
`mention` is NOT written on any imported body: the seat's mention rules run only where something writes `mention` (the chat does, on a NEW message), so the history makes no Word, Identifier or Path entities. Turning it on later is one write per body
(Decision 3). The SemText bodies are made (the message text and the answer a second time, as a native message has them).

### Owner mapping (migration decision 9, applied)

The imported session keeps its old `owner` text as data (`owner` on the ChatSession and the ledger's `owners`): a session with no `owner` (126 of 130 [copy]) is the admin's, a named one keeps the name as written (the slug of the Python seat's `speakers.slug`: lowercase, words joined by `-`).
The release has no users (docs/auth.md), so the owner is information only and no person is made by the cutover; `legacy_persons.c1f` (not in `barest.packs`) is kept for a seat that has persons.

### The ledger, and how a run goes

`Legacy:seat` `{source: 'legacy', converter: 1 (the version stamp, written by the pack), running, import, list, run_total, run_done, run_error, run_left, refused}`. Two writes, each a driver-style request with an `id` (a new `id` is a new request):
- `Legacy:seat.list = {id}`: the HEADERS of every old session (one query, ONE pass over the rows: key, body, the row count of the session's key range, the unmapped assistant rows; see "The cost rule" below): a ChatSession and a `pending` ledger each. The page's `GET /session` lists all sessions at once. A session that already has a ledger is skipped (idempotent).
- `Legacy:seat.import = {id, sessions: [sid...] | 'all', page: <n>}`: the headers, then the sessions NOT `done`, one at a time, `page` user rows per query (with what answered them: tools, texts and sums gathered by the query), ONE cascade per page:
  the page's entities and the ledger's `rows`, `cursor` (the last `n`), `state` are one commit. A stop between two pages loses and repeats nothing; naming the session again resumes at its `cursor`. `done` is never imported twice.
  `page` has NO default: a request without it is refused with the reason in `Legacy:seat.refused`. A page that fails (SQLite's words in `reason`) makes the session `error` and the import goes on to the next; a headers query that fails ends the import with the reason.
  A start of the seat ends a run the last process left `running` (the ledger says where it was).
- Progress: `Legacy:seat.run_left`, `run_done`, `run_error`, `cur` (the session being read); the ledger entities (`entity_query kind:LegacySession` or `Legacy:session:<sid>`).
- `started` is not written on the imported turns: a turn that has `started` makes the session-event setter (`oc_fin`, `door_session.c1f`) emit stream events and rewrite the session's `upd` when it is read. Without it the assistant row's `time.created` is the user row's (a 40 to 60 ms difference in
  the copy); the true times are in `legacy_src`.

### Manual import and rollback (the automatic cutover does steps 2 to 5 by itself)

1. Announce; `GET /session/status` on the old seat all idle (the restart rule). Wait for the Python message store's flush (`FLUSH_MIN_S = 5.0`, `server.py:663`) plus a margin.
2. Stop the Python seat (a clean exit checkpoints the WAL, `sqlitedb.py:286`). The immutable source ignores a WAL: the source MUST be a snapshot of a stopped store.
3. Snapshot: `cp -c` (APFS clone) `<old>/field.sqlite/field.sqlite3` to `<.membrane>/legacy.v0753.sqlite3`, `chmod a-w`, record `shasum -a 256` and the row counts of `c_sessions` and `c_messages`.
4. Start the new seat with the snapshot declared: `--fact sql_sources='[{"name": "legacy", "path": "<.membrane>/legacy.v0753.sqlite3", "mode": "ro"}]'` (a source the model rewrites later is kept: the fact only seeds a source that is not there; `Help:legacy`).
5. Write `Legacy:seat.list = {id: 'c1'}` (the page lists every session at once), then `Legacy:seat.import = {id: 'c2', sessions: 'all', page: <n>}`. Watch `run_left`; read `GET /session` and a few sessions.
6. Verify: `shasum` of the snapshot equals step 3 (the source was never written); every ledger `done` (`run_error` 0); for each session `rows + unmapped = old_rows`; the counts in this note (130 sessions, 1,137 rows, 477 messages).
7. Rollback at any time: stop the new seat, start the Python seat on the original `.membrane`: it was never written. Messages born on the new seat after the swap are not in the old store (the reverse is not built: Decision 7 of the earlier design, still open for a later round if wanted).
8. After the swap is stable, the snapshot stays (the model reads it); the ledger stays (an `error` session retries by naming it).

### Measured (2026-11-14, the Rosie copy, debug build, a machine at load average about 10, port 0, no `claude`, source read-only)

`LEGACY_COPY=<copy> LEGACY_PAGE=<n> cargo test --features bin --test legacy_routes_1143 measure -- --ignored --nocapture` (read-only on the copy: its size is unchanged and no WAL appeared; the target is a NEW store in a temp folder, not a copy of a seat's store, because no
store of the new format exists for this seat yet).

| | page 20 | page 100 |
|---|---|---|
| headers of all 130 sessions (the `list` write to all 130 in `GET /session`) | 5.8 s | 5.8 s |
| import of all 130 sessions (messages) | 123.6 s | 126.0 s |
| sessions done / error | 130 / 0 | 130 / 0 |
| old rows / consumed by messages / noted unmapped | 1,137 / 1,128 / 9 | the same |
| dropped parts (reasoning) | 19 | 19 |
| rows served back by `GET /session/<sid>/message` for all sessions | 954 rows, 11.6 MB, 43.3 s for 130 requests | 954, 11.6 MB, 43.4 s |
| first open of the biggest session (226 rows, 223 old rows) right after the import | 0.88 s | 0.89 s |
| RSS of the seat process, base / peak | 13 MB / 721 MB | 13 MB / 739 MB |
| growth of the target store | 123.1 MB (`c_field` 109.3 MB, `c_journal` 12.1 MB, the two indexes 1.2 MB) | 123.1 MB |
| the previous store | unchanged (size) | unchanged |

Reading: the page size changes nothing measurable between 20 and 100 (the cost is the per-message entities and the query scans, about 1 s per session in a DEBUG build on a loaded machine; a release build was not measured). 477 user rows became 477 messages, 660 assistant rows are 471 answers
(the answering rows of one question are merged), 9 unmapped. The growth is 10 times the 12.7 MB of source text: the rows hold each text twice (message and its SemText body, as a native message does) and whatever the entities carry beside; not investigated further here.
`c_journal` is 12 MB: the page receipts are journalled with their rows. The first open is not a lazy import: every session was already imported.

### The cost rule, and the real-store measurement (2026-11-14, #1143)

The first cut was quadratic in a session's size, which the Rosie copy (130 sessions, 226 rows at most) hid and the real main-seat store (1,605 sessions, 25,465 old message rows, the biggest sessions 8,024 / 3,595 / 2,808 rows) showed: the header import ran 4+ minutes at 100% of one core and had not finished. Three causes, three rules:
1. **Headers are one pass.** The unmapped assistant rows (the `orphans` of a header) are found by a primary-key lookup per assistant row: its parent is the user row whose key is `<sid>|<info.parentID>` (`x.k = s.k||'|'||parentID`, checked on the real store: all 25,465 rows have `k = sid|mid`), not by a `NOT IN (SELECT ...)` re-evaluated per row. The orphan note stays on the pending ledger at the header, as before (no change of the mapping, the ids, or `rows + unmapped = old_rows`): on the real store the new header query returns byte-for-byte the same `nrows` and `orphans` as the old one for the 1,602 sessions the old one can finish on (the three biggest never finish on it).
2. **A page reads the session once.** The page query materializes the page's user rows (`pu`, at most `page`) and then ONE pass over the session's rows keeps the answering rows of those mids (`ans`); the per-question sums, tools and texts are read from that small set, not from a pass over the whole session per question. A page costs about one pass over the session (the test: under 20 passes), not one pass per question.
3. **A FOR copies the names bound above it, per turn.** A setter that binds a list of every session and then loops over it copies that list once per session (engine: `Fields::run_writes`). The headers setter therefore writes its loop list inline in the FOR line. (The engine copy itself is not changed here; it is listed under Risks.)

Measured on the real store (read-only, immutable `ro` source, `native/tests/legacy_import_1143.rs` `measure_the_headers_and_the_biggest_session_of_a_real_store_read_only`, `LEGACY_REAL=<store file> LEGACY_PAGE=20 cargo test --release --features bin --test legacy_import_1143 measure_the_headers -- --ignored --nocapture`; release build; the seat that owned the store was stopped, another reader of the file was open; target = a fresh private store; two runs of the final code agree):

| | before | after |
|---|---|---|
| headers query, all 1,605 sessions (driver receipt) | did not finish in 4+ min (the correlated orphan subquery) | 0.26 to 0.46 s |
| `list` write to all 1,605 `ChatSession` + ledger entities, in total | 107 s with the header query already fixed (quadratic FOR copy) | 7.5 s |
| first page (20 questions) of the biggest session (8,024 rows), the query alone (sqlite3 CLI, same file) | 29.4 s | 0.13 s |
| the whole biggest session imported page by page (page 20) into a fresh store | not measured: about 200 pages of the 29 s and more, so well over an hour | 39 to 45 s (8,020 rows consumed + 4 unmapped = 8,024 old rows, 484 dropped parts) |
| RSS of the process, peak | | 925 to 949 MB (the headers alone: 9 to 232 MB) |
| growth of the target store for that one session | | 158 MB |

The 1,605 sessions hold 24,033 of the 25,465 message rows (181 unmapped assistant rows among them); the other 1,432 rows belong to no session of `c_sessions` and are not imported. The whole-store import was NOT run on the real store (one session only, as asked): from the numbers above it is a few minutes of page queries and a store growth of roughly the same order per row, not measured. The tests that keep the rules (debug build, a synthetic old store with one session of thousands of questions and many small sessions; the bounds are TEST bounds between linear and quadratic, not product constants): headers cost at 2,000 vs 500 questions (ratio < 8; was 19), a page query costs under 20 passes over a 3,000-question session (was 206), the import of one big session at a fixed page grows with a ratio < 8 for 4x the questions, writing the headers of 600 vs 150 sessions (ratio < 8; was 10.5).

### Risks

0. **Engine: a FOR copies the names bound above it, once per turn** (`Fields::run_writes`: `binds.to_vec()` per turn, and again at the start of each recursive call). Any setter that binds a big list and loops over it is quadratic in its length; the headers setter avoids it by writing the list inline in the FOR line, and a page setter binds at most `page` rows. Making the binds shared (not copied) is an engine change, not made here.
1. The immutable source reads a snapshot: pointing it at the live file of a running Python seat reads stale or torn data (use the step 3 snapshot).
2. A message page is evaluated by one setter (an evaluation step budget); the largest real row (a user row with 61 answering rows) imported at page sizes 20 and 100 in the measurement. A setter that FAILS (a budget trip, say) would leave the import `running` with no ledger step, and the run would not end by itself (a start of the seat does): listed as a risk, not covered by a test.
3. Ids: the page's `before=<old message id>` of a bookmark does not match (the message ids are derived, `legacy_src.mid` holds the old one).
4. `time.created` of an imported row can differ by 1 ms from the old one (`at` is seconds as a float; the exact value is in `legacy_src.at`).
5. Message order is by the old `n` within the import and by `at` then `n` on the page: 19 pairs in the copy have a time that contradicts `n`; the page orders them by time.
6. Store growth: see Measured.

## 1. What the Python seat stores (a)

Store: ONE SQLite file `<.membrane>/field.sqlite/field.sqlite3`, WAL (`sqlitedb.py:104-106`, dir name `STORE_DIRS` `sqlitedb.py:295`), one table per collection
`c_<coll>(k TEXT PRIMARY KEY, body TEXT)` (`sqlitedb.py:51,149`; the same table naming the cliffvm packs already read, `P/store.c1f:7-13`). The collection list is
`SEAT_COLLECTIONS` (`storekind.py:40-42`). Body shapes differ by writer:

| collection | rows [copy] | MB [copy] | key | body | writer (PY53) |
|---|---|---|---|---|---|
| `sessions` | 130 | 0.08 | `ses_<24 hex>` | `{"s": {id, slug, title, owner, directory, model, agent, tokens, cost, time, summary, absorbed, path, projectID, version}}` | `server.py:287-330, 448` |
| `messages` | 1,137 (477 user, 660 assistant) | 12.6 | `<sid>\|<mid>` | `{sid, mid, n, row: {info, parts}}`, `n` = order; parts types `text tool step-start step-finish reasoning` (6,912 parts) | `server.py:486-520` |
| `entities` | 10,749 | 39.5 | `membrane::<kind>:<rest>` | `{"o", "d": {class, properties}` + `state_space`, `vector_data` (a 1024-float embedding) on 10,748 rows `}` | `entities.py:372-405` |
| `files` | 3 | 1.4 | `file:<name>` | `{name, d}`: `bg-usage.json`, `router.json` (1.42 MB), `serve-absorb.json` | `datafiles.py:18-21` |
| `meta` | 1 | 0 | `migrated` | the last store move's note | `cblstore.py` |
| `events` | 8,307 | 84.0 | `v:<origin8>:<hash>` | `{o, d: {session, role, stream, text, tool, args, result_*, mentions, speaker, ts ...}}`: the semantic field's event log | `efstore.py:27` |
| `entity_states` / `entity_deltas` / `relations` | 5,634 / 44,965 / 28,038 | 87.5 / 42.0 / 48.3 | `e:` / `d:` / `r:<origin8>:<hash>` | per-word/file/symbol state, motion ledger, subj-rel-obj edges | `efstore.py:27` |
| `sentences` / `terms` / `chunks` | 15,207 / 6,553 / 24,732 | 45.0 / 18.6 / 84.1 | | sentence text + vec, term vec, chunk rows | |
| `claims` / `annotations` / `origin_stats` | 226 / 8,064 / 1 | 0.15 / 53.0 / 0.95 | | | |

Outside the database, in `<.membrane>/` (seen in directory listings, not opened): `field.sqlite/vectors/sentences.f16` + `.keys` (the float16 sentence matrix, 57 MB,
`sqlstore.py:54-60`; the file may GROW under a reader), `settings.json` (options, `options.py:2-9`), `models.json`, `goal-selection.json`, `jwt.secret`
(`jwtauth.py:281`), `seat-token`, `origin`, `store.json` (the kind choice, `storekind.py:33`), `doctrine.md`, append-only logs (`audit.jsonl`, 15.9 MB on the main seat),
`voice/`, `self/`, the OLD journal dir `field/` (a backup since the SQLite move, `storekind.py` doc) and `.field.sqlite.writer.lock` (an `flock`, `cblstore.py:145`).

Size drivers: events, entity_states, chunks, annotations, relations (each 48-87 MB) and every entity row carrying its 1024-float vector (about 3.7 KB per row).

**What maps to cliffvm entities** (all as `c_field` rows, lazy class unless said):

| old | new | note |
|---|---|---|
| session (`sessions`) | `membrane::Chat:session:<sid>` ChatSession (`stitle born upd`, `docs/cliffvm.md:468`) | + `owner` as `Person` link (section 5) |
| message pair (`messages`) | user row to `ChatMessage` (`text at sid`), assistant row to `ChatTurn` + response + one `ChatTool` per `tool` part | old ids kept (`msg_<hex>`); new ids are `msg_<ms><n>00x` (`cliffvm.md:471`) |
| `Component:*`, `node`, `symbols`, `files`, `shell`, `error`, ... (9,620 `class=Component`) | the same eid under its class | the bulk (8,352 are `node:*`); candidates for "not converted" (Decision 8) |
| `Decision` (11), `Goal` (22), `Task` (101), `Config` (15), `Banner` (25), `Command` (15), `Message` (392), `Tool`, `Verb`, `Help`, `Register` | same class names where a pack declares them (`decisions.c1f`, `goalgraph.c1f`, `banner.c1f`) | decisions' edges live in `relations`/`entity_deltas`/`c_raw` (read in place, `release-parity.md:270`) |
| persons | REGENERATED: `there is no Person in this release (docs/auth.md) | the old person rows are `Component:chat:<slug>` (5 [copy]) and `Config:voice:*`; they are touched by slug, not listed |
| options, models | one read of `settings.json` / `models.json` into `Settings:seat` | `fs` driver exists |
| `files` rows | `bg-usage` + `serve-absorb` carried; `router.json` regenerated (Decision 11) | dropping `serve-absorb` makes the seat absorb old opencode sessions AGAIN (duplicates) |
| recall tables, vectors | NOT converted; read in place (Decision 10) | `release-parity.md` D8 |
| old journals `field/`, logs, `.corrupt-*` copies | dropped (archived) | |
