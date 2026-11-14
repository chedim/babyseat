# Help:legacy - the previous store, kept as a read-only source

The seat you run now replaced an older one. That older seat's whole store is kept as ONE SQLite file and declared as the source `legacy` (`SqlSource:legacy`, opened read-only). It is a snapshot taken when the seats were swapped: it
never changes, and nothing you do here writes to it. Read it with `SqlQuery` entities (Help:sql says how a source, a query and a run work); bring a piece of it into the field when it is useful.

If `SqlSource:legacy` has no path yet, the seat was started without it: write `SqlSource:legacy.path = '<the file>'` (mode `ro` is the default) and the same queries work. A launch can also declare it with the fact
`sql_sources='[{"name": "legacy", "path": "<the file>", "mode": "ro"}]'`; a path you write yourself is kept across restarts (the fact only seeds a source that is not there).

## What is already in the field: the chat history

The chat history (every session and every message) was imported into the field: the sessions are `Chat:session:<id>` entities with their messages, and the page shows them like any session. The import is `Legacy:seat`
(`Legacy:seat.import = {id, sessions: 'all', page: <n>}`; progress on the seat and a ledger entity `Legacy:session:<id>` per old session, with `state`, `rows` and a note of the rows that had no place). Read the ledger
before you read the source: a session that says `done` is already whole in the field.

## What the file holds

Every table is `c_<name>(k TEXT PRIMARY KEY, body TEXT)`: `k` is the row's key, `body` is JSON text. Read inside the body with SQLite's `json_extract(body, '$.path')`.

| table | rows hold | size class |
|---|---|---|
| `c_sessions` | one per chat session: `{"s": {id, title, owner, directory, time: {created, updated}, ...}}`, key = the session id; `owner` is missing for the operator's own sessions | about 100 rows, small |
| `c_messages` | one per message row: `{n, sid, mid, row: {info, parts}}`, key = `<session id>\|<message id>`; `n` is the order, `info.role` is `user` or `assistant`, an assistant row's `info.parentID` is the user row it answers; `parts` are `text`, `tool` (with `state.input` and `state.output`), `reasoning`, `step-start`, `step-finish` | about a thousand rows, megabytes |
| `c_entities` | one per entity of the old field: `{e: <id>, d: {data: {class, properties}}}`; the class is in `d.data.class`, the properties in `d.data.properties`; many rows also hold a long embedding in `d.vector_data` | ten thousand rows, tens of megabytes |
| `c_files` | a few whole files kept as rows: `{name, d}` (usage numbers, the router table) | a handful of rows |
| `c_events`, `c_sentences`, `c_terms`, `c_entity_states`, `c_entity_deltas`, `c_relations`, `c_claims`, `c_annotations`, `c_chunks`, `c_origin_stats` | the old recall data: what was said, word and sentence rows, per-entity states and their motion ledger, edges between entities, annotations | the bulk of the file, hundreds of megabytes |
| `c_meta` | one note about the last store move | one row |

An entity id of the old field is `<namespace>::<class or kind>:<rest>`. Many start with a lowercase KIND instead of a class (`node:js:ab12...`, `msg:...`, `result:...`, `files:...`); for those the class is in the row's `d.data.class`.
A float16 matrix of sentence vectors lives in a folder beside the file (`vectors/`), outside the SQLite file; it is not readable with SQL.

## Look things up

List the sessions, oldest first (a small file is made for each example here; yours is the real one):

```c1f-run
-- sqlite old.sqlite = CREATE TABLE c_sessions (k TEXT PRIMARY KEY, body TEXT); CREATE TABLE c_messages (k TEXT PRIMARY KEY, body TEXT); CREATE TABLE c_entities (k TEXT PRIMARY KEY, body TEXT); INSERT INTO c_sessions VALUES ('ses_1', '{"s": {"id": "ses_1", "title": "Plan the trip", "time": {"created": 1790000000000, "updated": 1790000100000}}}'), ('ses_2', '{"s": {"id": "ses_2", "title": "Ada notes", "owner": "Ada", "time": {"created": 1790000200000, "updated": 1790000300000}}}'); INSERT INTO c_messages VALUES ('ses_1|msg_a', '{"n": 1, "sid": "ses_1", "mid": "msg_a", "row": {"info": {"role": "user"}, "parts": [{"type": "text", "text": "book the ferry for Friday"}]}}'), ('ses_2|msg_b', '{"n": 2, "sid": "ses_2", "mid": "msg_b", "row": {"info": {"role": "user"}, "parts": [{"type": "text", "text": "remember the lighthouse"}]}}'); INSERT INTO c_entities VALUES ('ns::Decision:ferry', '{"e": "ns::Decision:ferry", "d": {"data": {"class": "Decision", "properties": {"name": "ferry on Friday", "summary": "take the early boat"}}}}');
-- write SqlSource:legacy.path = "old.sqlite"
-- write SqlQuery:old-sessions.source = "legacy"
-- write SqlQuery:old-sessions.sql = "SELECT k, json_extract(body, '$.s.title') AS title FROM c_sessions ORDER BY json_extract(body, '$.s.time.created')"
-- write SqlQuery:old-sessions.run = {"id": "1", "args": []}
-- receipt SqlQuery:old-sessions.run_result.rows = [{"k": "ses_1", "title": "Plan the trip"}, {"k": "ses_2", "title": "Ada notes"}]
-- reads SqlQuery:old-sessions.rows = [{"k": "ses_1", "title": "Plan the trip"}, {"k": "ses_2", "title": "Ada notes"}]
```

Find the messages that hold a word (`?` carries the pattern as a value, so a quote in it is only a quote):

```c1f-run
-- sqlite old.sqlite = CREATE TABLE c_messages (k TEXT PRIMARY KEY, body TEXT); INSERT INTO c_messages VALUES ('ses_1|msg_a', '{"n": 1, "sid": "ses_1", "mid": "msg_a", "row": {"info": {"role": "user"}, "parts": [{"type": "text", "text": "book the ferry for Friday"}]}}'), ('ses_2|msg_b', '{"n": 2, "sid": "ses_2", "mid": "msg_b", "row": {"info": {"role": "user"}, "parts": [{"type": "text", "text": "remember the lighthouse"}]}}');
-- write SqlSource:legacy.path = "old.sqlite"
-- write SqlQuery:old-find.source = "legacy"
-- write SqlQuery:old-find.sql = "SELECT json_extract(body, '$.sid') AS sid, json_extract(body, '$.row.info.role') AS role, body FROM c_messages WHERE body LIKE ? ORDER BY json_extract(body, '$.n') LIMIT 20"
-- write SqlQuery:old-find.run = {"id": "1", "args": ["%lighthouse%"]}
-- receipt SqlQuery:old-find.run_result.status = "ok"
-- reads SqlQuery:old-find.changes = 0
```

Read one entity row by its id:

```c1f-run
-- sqlite old.sqlite = CREATE TABLE c_entities (k TEXT PRIMARY KEY, body TEXT); INSERT INTO c_entities VALUES ('ns::Decision:ferry', '{"e": "ns::Decision:ferry", "d": {"data": {"class": "Decision", "properties": {"name": "ferry on Friday", "summary": "take the early boat"}}}}');
-- write SqlSource:legacy.path = "old.sqlite"
-- write SqlQuery:old-entity.source = "legacy"
-- write SqlQuery:old-entity.sql = "SELECT json_extract(body, '$.d.data.class') AS class, json_extract(body, '$.d.data.properties.name') AS name, json_extract(body, '$.d.data.properties.summary') AS summary FROM c_entities WHERE k = ?"
-- write SqlQuery:old-entity.run = {"id": "1", "args": ["ns::Decision:ferry"]}
-- receipt SqlQuery:old-entity.run_result.rows = [{"class": "Decision", "name": "ferry on Friday", "summary": "take the early boat"}]
```

To list the entities of one class, ask the JSON (`WHERE json_extract(body, '$.d.data.class') = ?`). A table with ten thousand rows answers in well under a second; put `max_rows` on a run that could return a great many.

## Bring a piece into the field

Nothing is copied unless you copy it. When a row is useful, write an entity from it: read the row (above), then write the entity with the values you want to keep, under a name that says where it came from. The entity is
yours: it follows the field's rules from then on, and the old row stays as it was.

```c1f-run
-- sqlite old.sqlite = CREATE TABLE c_entities (k TEXT PRIMARY KEY, body TEXT); INSERT INTO c_entities VALUES ('ns::Decision:ferry', '{"e": "ns::Decision:ferry", "d": {"data": {"class": "Decision", "properties": {"name": "ferry on Friday", "summary": "take the early boat"}}}}');
-- write SqlSource:legacy.path = "old.sqlite"
-- write SqlQuery:old-entity.source = "legacy"
-- write SqlQuery:old-entity.sql = "SELECT json_extract(body, '$.d.data.properties.name') AS name, json_extract(body, '$.d.data.properties.summary') AS summary FROM c_entities WHERE k = ?"
-- write SqlQuery:old-entity.run = {"id": "1", "args": ["ns::Decision:ferry"]}
-- reads SqlQuery:old-entity.rows = [{"name": "ferry on Friday", "summary": "take the early boat"}]
-- write Note:ferry.title = "ferry on Friday"
-- write Note:ferry.text = "take the early boat"
-- write Note:ferry.from_legacy = "ns::Decision:ferry"
-- reads Note:ferry.text = "take the early boat"
-- reads Note:ferry.from_legacy = "ns::Decision:ferry"
```

A getter can do the same on a schedule of your own: it reads `SqlQuery:<name>.rows` and a setter on it writes the entities (Help:packs shows getters and setters). The messages of a session that is still `pending` in its ledger can be
imported with `Legacy:seat.import = {id, sessions: ['<session id>'], page: <n>}`; the batch and the headers are described at the top of the `legacy_import` pack.
