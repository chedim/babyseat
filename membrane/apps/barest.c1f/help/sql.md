# Help:sql - a second SQLite file, read (and written) as entities

A SQLite file other than your own store is a `SqlSource`; a statement on it is a `SqlQuery`; running the statement is a write. No builtin is involved: you write entities
and read the receipt back, as with `Command` and `Fetch`. Every example below is run by a test.

## The two entities

`SqlSource:<name>` has `path` (a file; a relative path is the seat's folder, `~` is home) and `mode`: `ro` (the default) or `rw`. A `ro` source is opened read-only with SQLite's
immutable setting: SQLite writes nothing beside it and takes no lock, so the file stays as it is for as long as the seat runs (a file that another program changes meanwhile is
read as it was when the source opened; replace the file, then rewrite `path` to open it again). An `rw` source accepts INSERT, UPDATE and DELETE and the file must exist already.
The seat's own store is the field itself and is never a source: a path that leads to it (a symlink or a hard link too) is refused with the reason in the receipt.

`SqlQuery:<name>` is a prepared statement: `source` is the NAME of a SqlSource (`'lex'` for `SqlSource:lex`) and `sql` is one statement with `?` for each value. Write them once. The first run
prepares the statement and the connection keeps it, so running it again costs no parse (`prepared` and `reused` in the receipt count both).

## Run it and read it

`SqlQuery:<name>.run = {id, args: [...], max_rows}` is ONE write. `id` names the run; the SAME `id` as the latest run is the same run (nothing runs a second time), a new `id` runs again.
`args` are bound to the `?` and are never spliced into the statement text: a quote or a semicolon in a value is only a value. `max_rows` (a whole number, optional, no default) stops reading after that many rows.
The source is opened by the first run and opened again by the next run after `path` or `mode` was rewritten. Queries on one source go one after the other; a long query never holds the field.

The receipt is `SqlQuery:<name>.run_result`: `{id, status, rc, rows, changes, ms, started, finished, head, prepared, reused}` and `truncated: true` when `max_rows` cut the rows.
`status` is `ok` (rc 0) or `error`: rc 1 when SQLite refused the statement (its own words are `head`), rc 2 when the run was refused before it started (no such source, the seat's own store, a bad `max_rows`, a file that will not open).
`rows` is a list of dicts, one per row, keyed by column name; a BLOB reads as null. `changes` counts the rows an INSERT, UPDATE or DELETE touched (0 for a read). Only the latest receipt stays on the entity.
The entity reads it for you: `rows`, `changes`, `ok`, `rc`, `truncated`, `status` (`running` while the run is in flight).

When a run failed, `rows`, `ok`, `rc` and `changes` read as Errors that say why (`x is Error`, `why(x)`), and so does `SqlSource:<name>.opened` when the file would not open; write a fixed `path`, run again with a new `id`, and they read as values.

```c1f-run
-- sqlite words.sqlite = CREATE TABLE syn (w TEXT, s TEXT); INSERT INTO syn VALUES ('big','large'),('big','huge'),('small','tiny');
-- write SqlSource:lex.path = "words.sqlite"
-- write SqlQuery:syn.source = "lex"
-- write SqlQuery:syn.sql = "SELECT s FROM syn WHERE w = ? ORDER BY s"
-- write SqlQuery:syn.run = {"id": "1", "args": ["big"]}
-- receipt SqlQuery:syn.run_result.status = "ok"
-- receipt SqlQuery:syn.run_result.rows = [{"s": "huge"}, {"s": "large"}]
-- reads SqlQuery:syn.rows = [{"s": "huge"}, {"s": "large"}]
```

The same sequence, as you write it with `entity_write`: `SqlSource:lex.path = '/path/words.sqlite'`, then `SqlQuery:syn.source = 'lex'`, then `SqlQuery:syn.sql = 'SELECT s FROM syn WHERE w = ?'`,
then `SqlQuery:syn.run = {id: '1', args: ['big']}`, then read `SqlQuery:syn.rows`. Another word is a new `id` with other `args`.

## Bound values

```c1f-run
-- sqlite words.sqlite = CREATE TABLE syn (w TEXT, s TEXT); INSERT INTO syn VALUES ('big','large');
-- write SqlSource:lex.path = "words.sqlite"
-- write SqlQuery:syn.source = "lex"
-- write SqlQuery:syn.sql = "SELECT s FROM syn WHERE w = ?"
-- write SqlQuery:syn.run = {"id": "2", "args": ["big' OR '1'='1"]}
-- receipt SqlQuery:syn.run_result.rows = []
```

## Writing

Set `mode` to `rw` and the same entities write. The receipt's `changes` says how many rows moved.

```c1f-run
-- sqlite notes.sqlite = CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT);
-- write SqlSource:scratch.path = "notes.sqlite"
-- write SqlSource:scratch.mode = "rw"
-- write SqlQuery:add.source = "scratch"
-- write SqlQuery:add.sql = "INSERT INTO note (body) VALUES (?)"
-- write SqlQuery:add.run = {"id": "w1", "args": ["first"]}
-- receipt SqlQuery:add.run_result.status = "ok"
-- receipt SqlQuery:add.run_result.changes = 1
-- reads SqlQuery:add.changes = 1
```

On a `ro` source the same INSERT comes back as `status: error` with SQLite's own words (a read-only database) in `head`, and the file is untouched.

## Many rows

`max_rows` keeps a large answer small: `{id: '3', args: [], max_rows: 100}` reads at most 100 rows and the receipt says `truncated: true` when there were more. Without it, every row is read.

## Sources at launch

A launch can declare sources up front with the fact `sql_sources`, a list: `--fact sql_sources='[{"name": "lex", "path": "/path/words.sqlite", "mode": "ro"}]'` makes `SqlSource:lex` as if you had written it
(`mode` is optional). Writing the entities by hand needs no fact.
