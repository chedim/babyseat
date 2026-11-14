# Data migration of the release swap: v0.7.53 seat to the cliffvm seat (design note, #1143, 2026-11-14)

Sections 1 and the Phase 1 section below are the BUILT part (`membrane/packs/legacy_import.c1f`); sections 2 to 9 are the earlier whole-field design, kept for the record and **not built** (operator scope decision, 2026-11-14: only the chat history is migrated; the old field is kept as a read-only source the model pulls from on demand, `Help:legacy`). Sizes are `sum(length(body))` (characters, not bytes); class counts are exact for the 24 largest segments, approximate beyond. Step 4 of the agreed order (`docs/release-parity.md`, section 4). Sources: `PY53` = `git show v0.7.53:membrane/<file>` (the
oracle tag), `N/` = `native/`, `P/` = `membrane/packs/`. Numbers marked **[copy]** were measured on a READ-ONLY COPY of a real seat's store:
`rosetta-check/.membrane/field.sqlite/field.sqlite3.good-121525` (a 2026-11-11 12:15 snapshot of the Rosie seat, 615 MB, WAL empty), copied to the
session scratchpad and opened with `immutable=1`. No live store was opened; the main seat's directory was only sized (`du`: 14 GB, includes its backups).

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

A session with no `owner` (126 of 130 [copy]) belongs to the operator: `owner = 'Person:' + <the fact operator, else owner>`. A named owner is `Person:<slug(owner)>` with the slug of the Python seat's `speakers.slug`
(lowercase, runs of non-alphanumerics to `-`): here lowercase and words joined by `-`, `.` `_` `,` read as spaces (c1f has no regex: other punctuation stays in the slug, Decision 6). The Person entity is made by
`legacy_persons.c1f` (`{sub, name as written, imported: True}`) only when it is not there yet and only for a NAMED owner; the operator's own Person is born by the first verified request, as always. The barest seat has no persons: the
`owner` text is on the session and nothing else is made. The copy has 3 named owners (`Dima`, `Dmitrii Chechetkin`, `Roomata Estorsky`) and one session whose owner is `owner` (the operator's sub): that one makes `Person:owner`.

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

### Lazy import (built 2026-11-14, #1143)

The headers (`Legacy:seat.list = {id}`: every old session as a `ChatSession` plus a `pending` ledger) come at once; a session's messages are imported the first time it is opened. With the launch fact `legacy_page` (user rows per query; NO default) told, `GET /session/<sid>/message` of a session whose ledger is `pending` or `importing` writes the ledger's `open` (`Request.ml_ask`, a setter in `legacy_import.c1f`) and the reply WAITS (`ml_lazy` in `door_session.c1f`) until the ledger says `done` (or `error`: the rows that made it). A second open finds `done` and does nothing; a native session has no ledger and is never touched; without the fact nothing is opened lazily. The seat runs the wanted sessions one at a time with the same pages as the eager path (a getter, `waiting`, lists the ledgers with `open`; `beat` starts the first one when the seat is idle, a run that ends picks the next wanted session before the rest of an eager queue). `import sessions: 'all'` stays as the eager path. A session now ends on an EMPTY page (one query more per session), because the message list reads the store the moment `done` is written, and the last page's rows are in the store only after that cascade's commit. `RESIDENT Legacy`: the seat and the ledgers are always loaded.
Measured on the real store (read-only, a private seat, release build, page 50): see the numbers in the report of the change; tests `legacy_import_1143.rs` (`an_open_*`, `two_opens_*`) and `legacy_routes_1143.rs` (`the_first_open_*`, `without_the_legacy_page_fact_*`, `opening_a_native_session_*`).

### Cutover runbook, phase 1 (with rollback)

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

### Decisions (my pick in brackets; nothing is set without your word)

1. `page` (user rows per query): no default. Measured at 20 and 100; [20 for the cutover: it is only a number of rows per query, not a limit of anything].
2. Lazy import: build the two-line hook above [yes, after the cutover batch is proven], or keep the batch only.
3. `mention` on the imported bodies: default none [none]: turning it on makes up to 400 transient spans per text and a Word entity per new lemma for 1,137 messages (the cost of a native message times 1,137, one write each); it can be done later body by body.
4. Concurrency: one session at a time, one query at a time (the Sql driver serializes a source anyway) [keep]. 
5. The message in the turn of a question nobody answered: `(no answer was recorded in the previous store)` as an error response [keep], or an empty done turn.
6. `slug(owner)` here ignores punctuation other than `. _ ,` and whitespace [fine for the 3 real owners], or propose the builtin `slug(text)` / a regex.
7. `started` left unwritten on imported turns (see above) [keep], or a one-line guard in `oc_fin` for imported messages.
8. Keep the ids of the old messages reachable (`legacy_src.mid`) only as data [keep], or add a lookup route.
9. The page's `Person` for the operator when a session names the owner `owner`: `Person:owner` is made with `imported: True` [fine].

## 0. Important first (of the earlier whole-field design; the Phase 1 section above is the current plan)

1. **The old store stays a read-only file; the new seat reads it through a read-only `SqlSource` (the Sql driver) and converts an entity the first time something
   touches it.** A converted row is an ordinary `c_field` row, so lazy load, `updated` and unload need no change. The native piece is built (2026-11-14,
   `docs/cliffvm.md` "The Sql driver", `Help:sql`): the old store is `SqlSource:legacy` (`ro`: opened read-only with SQLite's immutable setting) and every read is a `SqlQuery`
   entity; no builtin. Left to build: a small ledger table (`c_legacy_done`). Decisions 1 and 2.
2. **Only about 11% of the bytes are "things a user sees"** [copy]: chat (sessions + messages 12.7 MB), entities (39.5 MB), files (1.4 MB) = 53.6 MB of 517 MB.
   The other 89% is recall data (events, sentences, terms, entity_states, deltas, relations, claims, annotations, chunks, 463 MB) plus a 57 MB float16
   vectors file. Recommend: convert the 11%, leave the 89% where it is and read it in place until native recall owns rows (Decision 10).
3. **Two things users WOULD notice if skipped:** the owner of a session (126 of 130 sessions have NO owner [copy]: they belong to the operator, Decision 9),
   and the login (reuse `.membrane/jwt.secret` as the cliffvm secret so existing tokens and cookies stay valid, Decision 12; not yet tested).
4. **Reverse is cheap because the source is never written:** reverse = (the untouched old store, copied) + (what was born or changed on cliffvm, rendered
   back into old rows). Lossy only where the old shape has no place (section 3). Cost 2-3 rounds.
5. **Total 12-18 rounds** without recall data (section 8); `release-parity.md` counted 10-16 for the same scope because it listed no reverse path and no re-conversion.
   Section 9 lists 18 decisions; I chose none.

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
| persons | REGENERATED: `Person:<sub>` is born by the first verified request (`docs/auth.md:26`) | the old person rows are `Component:chat:<slug>` (5 [copy]) and `Config:voice:*`; they are touched by slug, not listed |
| options, models | one read of `settings.json` / `models.json` into `Settings:seat` | `fs` driver exists |
| `files` rows | `bg-usage` + `serve-absorb` carried; `router.json` regenerated (Decision 11) | dropping `serve-absorb` makes the seat absorb old opencode sessions AGAIN (duplicates) |
| recall tables, vectors | NOT converted; read in place (Decision 10) | `release-parity.md` D8 |
| old journals `field/`, logs, `.corrupt-*` copies | dropped (archived) | |

## 2. Convert on touch, in cliffvm terms (b) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

**Three paths, never mixed:** `--store new.sqlite` (the seat's, writable), `SqlSource:legacy` (`path` = `<old>/field.sqlite3`, `mode` `ro`: the Sql driver opens a NEW connection read-only with the immutable URI and `query_only`; declared by
the launch fact `--fact sql_sources='[{"name": "legacy", "path": "<old>/field.sqlite3"}]'` or written by hand), and the vectors dir (untouched). **Immutable means the file must
not change while the seat runs**: it ignores the old store's WAL, so the source is a SNAPSHOT of the old store (the cutover's step 3 makes one), never the file the Python seat is
still writing; the rehearsal below reads a `cp -c` clone for the same reason (decision 19).
The pack `legacy.c1f` (c1f only, a family pack like `door_session`) holds EVERY rule; Rust provides the miss hook; the reads are `SqlQuery` entities.

```
-- legacy.c1f (sketch, not final syntax)
PACK legacy 1
LEGACY_CONV getter: 1                                   -- the converter version (Decision 4)
SqlQuery:legacy:session.source = 'legacy'                -- written once (a setter at boot)
SqlQuery:legacy:session.sql = 'SELECT body FROM c_sessions WHERE k = ?'
Legacy.want_session setter: SqlQuery:legacy:session.run = {id: this.sid, args: [this.sid]}   -- the same id never runs twice
Legacy.q_session getter: SqlQuery:legacy:session.rows            -- None until the receipt lands, an Error with the reason when the file is gone
Legacy.eid_of(old) = ...                                -- PURE: old key -> new eid (section 5)
('Chat:session:' + sid) = ChatSession(stitle: s.title, born: s.time.created, upd: s.time.updated,
                                      legacy_src: {coll:'sessions', k: sid, conv: LEGACY_CONV, sha: sha(body)})
```

**Touch = the miss.** `stored_loader` (`host.rs:77-82`) answers a reference to an eid from `c_field`. On a MISS, with a legacy source attached, the host births the
virtual entity `Legacy:want:<old key>` (an arrival, like a door request); the pack's setter runs a `SqlQuery` on `SqlSource:legacy` (a write; async: its receipt lands when the read ends), then writes the birth
of the real entity and the ledger row, in ONE round (one commit). The first reader sees `None` (as any getter waiting on an async read) and the getter recomputes
when the entity arrives. "The VM cannot make an entity from a read" (`semtext-and-legs.md:93`), so the birth is always a write by the pack, never by the reader.

**Granularity** (Decision 3): an entity by eid; a session as one unit (the session birth also births its message rows, because message order is `n` and a
history page needs the whole list); a list route (`GET /session`, history `before=`) is served from the legacy KEY index (`SELECT k FROM c_sessions`, no body) unioned
with `c_field`, and births only the items it returns.

**`updated` and `touched`.** `updated` is VM-provided, read-only; a pack cannot set it (`cliffvm.md:176`), so a converted entity gets the conversion time and the true
old time rides in a plain prop `legacy_at` (recommended; the UI sorts by `at`). Stamping `updated` from the old time would need a host parameter on the birth
round (`round_ts`, `host.rs:170`): Decision 6. Why it matters: the warm boot loads "newest `updated` first" (`host.rs:701-708`), so after a mass touch the converted rows would look hot.
`touched` is the RAM counter (`cliffvm.md:187`), not stored: a born entity starts like any loaded one.

**Lazy load and unload.** After the birth the row is in `c_field` and the legacy source is never asked again for that eid (the ledger says `converted`). An
unloaded converted entity reloads from `c_field` like any row. The warm boot never reads the legacy store: `warm_rows` counts `c_field` rows only. A boot with
10,749 legacy entities and `warm_rows=0` leaves `c_field` empty until something touches (test, section 7).

**The ledger** `c_legacy_done(src TEXT PRIMARY KEY, conv INTEGER, dst TEXT, at REAL, state TEXT)`, `state` in `converted | retired | skipped | failed`. Needed because a
RETIRED converted entity deletes its row (`host.rs` retire), and the next touch would resurrect it from the old store. Created by the host at open next to
`c_field`'s columns (`host.rs:239-244`; packs cannot run DDL, `sqlexec.rs:173`). It is also the counting source for the cutover check (section 6).

**Failure.** A conversion that cannot parse the old body writes `state: failed` and the reason (an Error, `c1f-error.md`), and the read answers the Error: loud, not
empty. A failed row is retried on the next touch.

## 3. Reverse path: "reverse grind" (c) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

`cliffvm legacy reverse --to <dir>` (a headless pack run, Rust only for the two connections): 1. copy the ORIGINAL old store directory to `<dir>` (APFS clone or
`cp`; the original is never opened for writing); 2. for each ledger row `converted` whose entity changed (`updated > at`), render the entity back to its old
shape and UPSERT the copy; 3. for each entity with no `legacy_src` (born on cliffvm: new messages, sessions, decisions) render a new old-shaped row; 4. for each
`retired` row delete the old row from the copy; 5. write `store.json` so the Python seat opens `<dir>`. The Python seat then runs on `<dir>` exactly as before.

Lossless: sessions, messages (with `legacy_src.body` kept, Decision 5, the old `{info, parts}` is returned byte-equal when nothing changed), entities with a class
the old store had, decisions, goals, files rows. Lossy (stated, tested):

| lost | why |
|---|---|
| c1f-only classes and props (`Request`, getters, derived Errors, receipts, `Person`) | no old table; persons come back from the token on the next login anyway |
| recall learned on cliffvm (anything native recall wrote) | the old `events`, `entity_states` ... are as of the cutover; the Python seat re-accrues |
| new messages' `step-start`/`step-finish`/`reasoning` parts, per-row `cost`, `tokens` detail | the c1f turn stores the tools and ONE answer text (`cliffvm.md:471`); rendered as `text`+`tool` parts only |
| message ids born on cliffvm | rendered as `msg_<24 hex>`; ids change shape, never collide |
| `updated` precision, writer stamps | the old rows have no writers map |
| tool output past 16 KB in the page (kept in `Command:x.output`) | rendered from the stored head/tail rule |

Retention (Decision 13): the reverse output is a new directory; the first reverse can be re-run (idempotent: same input, same output).

## 4. Converter version stamp and re-conversion (d) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

Every converted entity carries `legacy_src: {coll, k, conv, sha}` and the ledger row carries the same `conv`; `LEGACY_CONV` is a constant in `legacy.c1f` (starts at 1,
Decision 4). On a touch of an entity whose ledger `conv` < the pack's, or whose old body `sha` changed, the pack re-runs the converter and merges by the writers map
(`c_field` rows are `{cls, props, writers}`, `host.rs:25`): a prop whose writer is still `trigger:field:legacy:v<conv>` is REPLACED with the new converter's value; a prop any
other writer touched (the person, a setter, the model) is kept. A prop the new converter no longer produces is removed only when its writer is the old converter.
A bulk re-grind is `cliffvm legacy verify --reconvert` walking the ledger; the default is lazy (a re-conversion rides the next touch). Test: a v2 test pack changes one
mapped prop; the converter-owned prop changes, the person-edited prop and a prop added by a setter survive.

## 5. Legacy rows with no eid class (e) [copy: 10,749 entity rows] - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

The old eid's first segment is NOT always a class. Of the 10,623 `membrane::` rows: 37 (`Component`), 25 (`Banner`), 15 (`Config`), 15 (`Command`), 11 (`Decision`) carry a
real class in the key; the rest start with a lowercase KIND (`node` 8,352; `symbols` 429; `msg` 392; `result` 343; `files` 206; `shell` 173; `error` 116; `task`, `verb`, `goal`,
`models`, `urls`, `help`, ...) and the class is in the body, `d.data.class` (`Component`, `Message`, `Tool`, `Task`, ...); `classes.py:30` already strips a leading class
for display. cliffvm derives the mode from the eid's first segment (`cliffvm.md:153`), so the rule table (pure function `eid_of`, c1f):

| old key shape | count [copy] | new eid | note |
|---|---|---|---|
| `membrane::<Upper>:<rest>` | about 103 | unchanged | class in the key |
| `membrane::<lower>:<rest>`, `d.data.class` = C | about 10,508 | `membrane::C:<lower>:<rest>` (`membrane::Component:node:js:ab12...`) | same shape the Python seat mints for files (`Component:files:x.py`) |
| `membrane::<one word>` (`llm`, `name`, `operator`, `consequences`, `dormant`) | 12 | `membrane::Legacy:<word>` | no class, no second segment |
| no `membrane::` namespace: `entity::cortex::*`, `episodic::*`, `cron::`, `daemon::`, `memory::`, `module::`, `tool::` | 123 | `membrane::Legacy:<old key>` or not converted (Decision 8) | cortex-experiment era, `d.data.class` set |
| `d.data.class` missing | 1 | `membrane::Legacy:<old key>` | |
| `Store:field`, `About:seat` | 2 | not converted | rows the Python seat's own packs wrote |

`Legacy` is a lazy class, hidden from the default entity views; the old key lives in `legacy_src.k`. A touch by the OLD eid (a pack, Memo, a bookmarked `entity_read`)
computes `eid_of(old)` and goes the same way: no alias table (the function is pure and total).

## 6. Cutover runbook (f) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

The Python seat runs until the minute of cutover. **Rehearsal (any number of times, no downtime):** start cliffvm on a second port with `--store rehearsal.sqlite --fact sql_sources='[{"name": "legacy", "path": "<a clone of the old store>"}]'`; the source is read-only and
immutable, so the rehearsal reads a clone (`cp -c`) and never the file the Python seat is writing. Delete `rehearsal.sqlite` afterwards (Decision 14: the final store starts EMPTY).

1. Announce; `GET /session/status` all idle (restart rule). Wait for the Python message store's cadence flush (`FLUSH_MIN_S = 5.0`, `server.py:663`) plus a margin (Decision 14).
2. Stop the Python seat (clean exit: the store closes with `wal_checkpoint(TRUNCATE)`, `sqlitedb.py:286`; the writer lock is released). Check the lock is free.
3. Snapshot: `cp -c` (APFS clone) the store dir to `<.membrane>/legacy.v0753/`, `chmod -R a-w`; record sha256 + size of `field.sqlite3` and `SELECT count(*)` of every `c_` table into `legacy.v0753/ledger.json`. This is the golden ledger and the rollback source.
4. Start cliffvm on the SAME port with `--store <.membrane>/cliffvm.sqlite3 --fact sql_sources='[{"name": "legacy", "path": "legacy.v0753/field.sqlite3"}]' --secret-file <.membrane>/jwt.secret`. Door answers at once (the lazy boot: 0.12 s at 5,000 rows, `cliffvm.md:175`; `c_field` is empty).
5. Verify: (a) `sha256` of the legacy file equals the ledger (read-only proof); (b) `cliffvm legacy verify` reports per collection `old N = converted + skipped + dropped` using the dry-run touch of every key into a scratch store, and the sample of 20 sessions + 50 entities compared old vs new; (c) the recorded Python golden answers for `/session`, `/session/<sid>/message`, `/membrane/chat/messages` (`scripts/golden_session.py`) match by shape (`session_1143.rs` `shape_misses`); (d) the owner opens the page, sees the last session and its history; a Python-minted token is accepted.
6. Rollback (any time before the first new write matters): stop cliffvm; restart Python on the original `.membrane` (it was never written; only the lock moved). After new writes: `cliffvm legacy reverse` (section 3), then start Python on `<dir>`.

Downtime = steps 1-4. Estimate, NOT measured: seconds for the flush wait, under a second for an APFS clone (minutes for a non-clone copy of 0.6 to several GB: measure before promising), under a second to boot. So about 1-3 minutes, dominated by waiting for idle turns. Dual-run soak is optional and not needed for data (rehearsal covers it).

## 7. Risks and test plan (g) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

Risks: (1) a new read path to a live WAL file: only `READ_ONLY` + `query_only` on a stopped-or-WAL store, proven by sha256 before/after; (2) id shapes (`msg_<hex>` kept vs new numeric ids; the page's
ordering must use `at`/`n`, not the id); (3) the 5 s message flush (messages from the last 5 s) and a seat killed rather than stopped (the checkpoint did not run: copy `-wal` and `-shm` too, never delete them);
(4) a retired entity resurrected by a touch (the ledger); (5) the 126 owner-less sessions; (6) epoch units (`time.created` is ms, `1789580956232` [copy]) vs the c1f `at`; (7) a 100 KB+ message row (1 of 1,137 [copy], 32 parts) and the 1 MB+ `router.json`;
(8) disk: the snapshot is a clone (cheap on APFS) but the legacy file stays as long as recall reads it; (9) a Python-minted token the cliffvm verifier refuses (claims shape): test first; (10) a conversion rule written wrongly is a silent data change: hence the golden diff.

Tests (red first, `native/tests/legacy_1143.rs`):
- GOLDEN FIXTURE `tests/golden/legacy/` built by a script from the REAL COPY with text replaced by stubs (3 sessions, 6 messages, one row of each eid shape in section 5, a file row); the real copy itself is used only by an `#[ignore]` test reading `LEGACY_COPY=<path>` for counts and timing, never committed.
- read-only: sha256 of the legacy file identical after any run; an attempt to write through the `ro` source is refused by SQLite itself (a `SqlQuery` INSERT on `SqlSource:legacy` reads as an Error).
- lazy: boot over the fixture, `c_field` empty; a first touch makes exactly the rows of the unit; a second touch makes none (idempotent); touch order A,B equals B,A (dump compare).
- crash: kill -9 between touch and commit: no ledger row without the entity (one round, one commit).
- retire: retire a converted entity, touch again: stays gone.
- version: v1 then v2 (section 4), the three prop kinds.
- reverse: forward then reverse over the fixture equals the old store row by row for the lossless set; the lossy set equals the table in section 3 exactly.
- counts: verify's `old N = converted + skipped + dropped` on the fixture and, `#[ignore]`, on the copy (10,749 + 1,137 + 130 + 3).
- shape: the Python golden answers for the three session routes over a converted fixture.

## 8. Build plan, red-test-first rounds (h) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

| round | content | rounds |
|---|---|---|
| R1 | `SqlSource:legacy` + `SqlQuery` (the Sql driver, built); no-table answers empty, never hangs (the three fresh-store hangs of `release-parity.md`); `c_legacy_done` at open; fixture builder | 1-2 |
| R2 | `eid_of` + the section-5 rule table, pure c1f + a table test over the copy's key shapes | 1 |
| R3 | sessions + messages: touch by id, list from key index, history page, session/message routes; owner mapping | 2-3 |
| R4 | entities by class (`Decision`, `Goal`, `Task`, `Config`, `Banner`, `Command`, `Message`, `Component`), `Legacy` fallback, `Settings:seat` + files rows | 2-3 |
| R5 | version stamp, writers-aware re-conversion, retire tombstones | 1 |
| R6 | reverse grind (render back, copy + overlay, lossy list test) | 2-3 |
| R7 | `legacy verify`, counts ledger, golden shape diff, `#[ignore]` real-copy run | 1 |
| R8 | cutover script (`scripts/cutover`: idle check, stop, clone, ledger, start, verify), rehearsal on a copy, secret reuse test | 1-2 |
| R9 | recall tables read in place (attach), only if Decision 10 says so | 1-2 (the 4-6 of native recall data stay separate) |

Total **12-18** (R1-R8 = 11-16, R9 +1-2). Nothing here touches the live seat; the first real use is the rehearsal in R8.

## 9. Decisions for the operator (I chose none; my recommendation in brackets) - NOT BUILT: the earlier whole-field design (superseded by the 2026-11-14 scope: the old field is kept as a read-only source the model reads on demand)

1. Legacy access: DECIDED (2026-11-14) a second read-only SQLite file as entities, `SqlSource:legacy` + `SqlQuery` (no builtin, no `--legacy` flag) vs `ATTACH` on the main connection vs copying tables in.
2. The ledger as a host-created table `c_legacy_done` [yes] vs ledger rows as entities.
3. Touch unit: session = unit (all messages), entities by eid, lists from the legacy key index [yes] vs per message.
4. Converter version starts at 1; the stamp lives in `legacy_src.conv` and the ledger [yes].
5. Keep the old body on the converted entity (`legacy_src.body`, about +13 MB for messages [copy]) so reverse is byte-equal [yes] vs rebuild.
6. `updated`: conversion time + prop `legacy_at` [yes] vs a host parameter that stamps the old time.
7. New eid shape for lowercase-kind rows `membrane::C:<kind>:<rest>`, no alias table [yes] vs keeping the old eid and a `parents` class.
8. The 136 classless/cortex-era rows (123 + 12 + 1): `Legacy` class and lazy [yes] vs not converted.
9. Owner mapping: a session with no owner (126 of 130 [copy]) belongs to the operator; named owners by `slug(owner)` (`speakers.py:44`) to the matching `Person`.
10. Recall tables and the vectors file: read in place from the legacy file (it stays on disk) [yes for the first swap] vs convert; and how long the legacy file is kept.
11. `files` rows: carry `serve-absorb` and `bg-usage`, regenerate `router.json` (1.4 MB) [yes].
12. Reuse `.membrane/jwt.secret` as the cliffvm secret so tokens stay valid (needs the claims test) [yes].
13. Reverse: output dir name, the command name `cliffvm legacy reverse`, and the lossy list in section 3 accepted; retention of the output.
14. Cutover: the margin after the 5 s flush; the final store starts EMPTY (the rehearsal store is deleted) [yes]; the downtime budget (my unmeasured 1-3 min).
15. `warm_rows` for converted classes: unchanged default (1,000), the legacy source never warm-read [yes].
16. Message order key: the old `n`, then `at` (kept in `legacy_at`).
17. Keep the ledger's `retired` tombstones forever [yes] vs a purge after N days (a number).
18. The estimate: 12-18 rounds without recall data, accepted as the budget for step 4.
19. The immutable read-only source ignores the old store's WAL: the rehearsal and the cutover read a clone/snapshot of the old store, never the live file [yes], vs a `ro` mode that is not immutable (a later change to the driver, one more mode).
