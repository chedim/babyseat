# Chat keys: the session id is in the message's key (operator 2026-11-14, #1143)

## Why

A message used to be `membrane::Chat:message:<ms>-<n>` with its session as a property in a ~160 KB body (and again in a ~1 KB `:turn` row). "The messages of
session X" then needed a body read (`json_extract`), or the turn-row workaround, and the step budget grew with the store. Now the session is part of the
key, so the question is a key-prefix range of the `(cls, k)` index: index-only, no body, bounded by the page.

## The keys

| what | key |
|---|---|
| a session header | `membrane::Chat:session:<sid>` (unchanged) |
| a message of a session | `membrane::Chat:session:<sid>:message:<id>` |
| a message of the main chat (no session) | `membrane::Chat:main:message:<id>` |
| its children | `<message>:body`, `:turn`, `:turn:response`, `:turn:response:body`, `:turn:tool:<tool id>`, `:turn:round:<n>`, ... (as before, below the message) |
| the TIME ROW of every message | `membrane::Chat:main:time:<id>:main` or `membrane::Chat:main:time:<id>:session:<sid>` (kind `ChatLine`, one property `chat` = the same <where>) |

- `membrane::` is the namespace the chat packs always wrote; kept. The class of every key is still `Chat` (the eid class, as the constructor rule says), so the `cls = 'Chat'` leg of the index still selects them.
- `<id>` = `<ms>-<n>`: 13 digits of arrival time in milliseconds, `-`, the request's number PADDED TO 8 DIGITS (the width the opencode row ids already use: `msg_<ms><n8>001`). Inside one chat, key order IS time order: the 13-digit ms part sorts lexicographically (until year 2286), ties by the padded n. Unpadded, `...-9` would sort after `...-10`.
- Main chat = the `Chat:main` entity's children: `Chat:main:message:<id>`. It is NOT a reserved session name: a session named `main` would be `Chat:session:main` and never collide; and `Chat:main` is already the chat entity (`last_turn`) and a hidden prefix.
- A sid is `ses_<digits>` for sessions this seat makes, any string the previous store used for imported ones (the previous store holds 1,605 sessions, 28-40 characters, no colon). A sid with `:` is REFUSED: the session routes 404 it (`ss_eid` is None), a history request or a POST naming one is a 400, the importer skips such a session. No other escaping: the range queries are `k >= prefix AND k < prefix-with-';'` (the character after `:`), never `LIKE`, so `_` and `%` in a sid mean nothing special.
- Retiring `Chat:session:<sid>` retires every message below it by the colon path (host.rs). DELETE /session/<sid> is that one write plus the session's time rows (found by the key's tail, no body).

## Order across sessions: the time rows

The page shows ONE chat: every turn of every session in time order. With the sid in the key, order across sessions is not key order. So every message
is born with a second, tiny row `Chat:main:time:<id>:<where>` (`<where>` = `main` or `session:<sid>`; it sits under `Chat:main`, which the map already hides). The history
without `sid` is a range over `Chat:main:time:` (newest first, LIMIT page + 1): the row's key names the message (`<where>` is the chat path, `<id>` the id). It is a
new mechanism (one extra tiny entity per message); it is the cheapest one that keeps the cross-session read index-only and bounded.

## Who builds, parses or matches a message eid

- packs/chat.c1f: the constructors (`cm_at` setters), `cp` / `mid` / `sid` getters (`cp` = the key below `membrane::Chat:`, `mid` = the id, `sid` read from the key and never stored), the one-message GET (`/membrane/chat/message/<id>[?sid=<sid>]`: the id is inside its chat), the history (`hs_*`).
- packs/chat_turn.c1f, chat_oai.c1f: every write below a message is `membrane::Chat[this.cp]:turn...`; `Command:chat:<cp>`, `Fetch:oai:<cp>:...` are named by `cp`. `Boot.interrupted` reads the turn keys.
- packs/door_session.c1f: the session list, create, delete, status (the sid is cut out of the running turns' KEYS), the message POST, `GET /session/<sid>/message` (`ml_*`), the one row, abort, the event producers.
- packs/legacy_import.c1f: writes `membrane::Chat:session[this.cur]:message[id]` directly (the sid needs no filter) and the time row.
- packs/ha.c1f: the metrics read the turn keys (`membrane::Chat:%:message:%:turn`).
- Help text updated: help/semtext.md, help/legacy.md. Not changed: hidden prefixes (`Chat:session:`, `Chat:main` cover every new key; the default entry `Chat:message:` matches nothing on a new store and is kept for old ones), `Chat:main.last_turn`, the banner, the page's JS (ids are opaque `<ms>-<n8>` strings, `before` is whatever the route sent).
- The page's `GET /membrane/chat/messages`: with `sid` = one session, `sid=` = the main chat, no `sid` = every turn (time rows). Unchanged for the page.

## The two routes' queries (tests: native/tests/chat_keys_1143.rs)

```
SELECT k FROM c_field WHERE cls = 'Chat' AND k >= ?1 AND k < ?2
  AND instr(substr(k, length(?1) + 1), ':') = 0 ORDER BY k DESC LIMIT ?3
```
`?1` the prefix, `?2` the end of the prefix range or the cursor key, `?3` page + 1. The `instr` leg skips the message's children (they share the range). EXPLAIN QUERY PLAN: `SEARCH c_field USING COVERING INDEX c_field_cls (cls=? AND k>? AND k<?)`, no temp b-tree, no table (body) access. The step work is the page's.

## Existing stores (decision, nothing built)

Native seats on the OLD key form are dev seats only (Throwy 4096, the Memo demo 4097, tests). Options:
1. Fresh stores: start a new store and let the lazy importer rebuild the history from the previous store (read-only). Cost: native messages on the dev seats (test chatter) are lost. Nothing to build. RECOMMENDED.
2. A one-off migration pass (SQL over `c_field`: rewrite each old key, the `message` prop of turns, the time rows; run with the seat stopped). Cost: a script to write and prove, and journals/`last_turn` references to rewrite too.
3. The seat reads BOTH forms. Cost: every route and setter carries two shapes forever.

## Measured (private seat, fresh store, 200 sessions opened through the lazy importer from the read-only previous store; release build, shared machine, medians of 5, ms)

| request | before (old keys) | after |
|---|---|---|
| `GET /session/<biggest>/message?limit=40` | 418 | 90 |
| `GET /membrane/chat/messages?limit=20&sid=<biggest>` | 61 (turn-row filter) | 41 |
| `GET /membrane/chat/messages?limit=20` (every turn) | 45 | 39 |
| `GET /membrane/chat/messages?limit=20&sid=` (main) | 47 | 33 |
| `...&before=<next>` (every turn) | 48 | 42 |
| `GET /session/status` | 77 | 81 |
| `GET /session` (1,605 sessions) | 310 | 295 |
| 200 opens (lazy import), wall seconds | 151 | 106 |

`GET /session/<sid>/message` with no `limit` is the whole session (1,360 messages = 2,720 rows: 5.3 s before, 4.2 s after): building the rows is the cost, not finding the keys. Measured on port 4398 (4098 was held by another seat).
