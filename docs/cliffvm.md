# cliffvm: the field VM as a standalone app

`cliffvm` is the cliffhanger field VM as a process of its own: no Python, no pyo3. The command name is working and
PINNED/undecided; it lives in ONE place, the `[[bin]] name` in `native/Cargo.toml` (the tests read
`CARGO_BIN_EXE_cliffvm`: one more line in `native/tests/cliffvm_process.rs`, and a literal in `cli.rs`'s `build_id` fact).

**The barest seat has no security, by operator decision (2026-11-12).** No JWT, no shared secret, no login, no persons, no
Host/Origin check. The door answers every request: in this mode every request is `authed` with the subject `owner`. The door
opens on 127.0.0.1 unless `--bind` says another address (`--bind 0.0.0.0` opens it to the network: configuration only). The old
JWT code is still in the tree (a later cleanup removes it); the binary neither requires nor uses it.

**The app is a folder of packs (or a pack file).** `main()` parses arguments into Boot facts and runs the apps given; every route, tool,
turn and view is a `.c1f` pack in one (the shipped `membrane/packs/*.c1f` are the app). Several apps are LAYERS and the FIRST is the
root of trust (docs/c1f-layers.md). The Rust side is only the host: the loop, the
drivers, the store, the socket. A decision a pack could make is not made in Rust: if it shows up, it is listed below.

## Build and run

```
cd native
cargo build --release --no-default-features --features bin     # target/release/cliffvm
cliffvm --version
cliffvm run <app> [<app>...] [--store <sqlite>] [--port <n>] [--bind <addr>] [--ui-dir <dir>]
```

- Features (`native/Cargo.toml`): `store` = rusqlite + tls (the native store, the drivers, the door); `python = [pyo3, store]` (the
  seat's `membrane_vm.so`, unchanged: `sh native/build.sh`); `bin = [store]` (the app). A Python extension is not a program, so the
  app is built with `--no-default-features --features bin`. Tests of the app: the same flags.
- `run <app>...`: an APP is a folder `<name>.c1f/` or one pack file (the loader tells them apart). The `.c1f` files of a folder (its
  whole tree) are packs, loaded in dependency order (a pack after the packs of that app it imports; of those ready, the alphabetically
  first path next); every other file is DATA at its path (the door serves an app's `ui/` files). Several apps load in the order given and
  each is a layer above the one before it: a pack whose name is already loaded is skipped (the lower layer's stays).
  **Trust is by position, never by name**: the packs of the FIRST argument are the root of trust (privileged: docs/c1f-layers.md,
  "Privilege"); a later app has no privilege until a privileged pack grants it (`Pack:<name>.privileged = true`; boot.c1f grants
  `door_tools`). A pack named `boot` in a later app is not trusted. The shipped packs are a flat folder, so the shipped line is
  `cliffvm run membrane/packs/boot.c1f membrane/packs`: the single file is the first app (the root of trust), the folder a layer above it
  that skips `boot` (already loaded). The boot plan (`boot.json`, `boot.c1f`) is looked for beside the first app (in its folder, or
  the folder a pack file is in). After the packs: computes the getters once (a rebuild
  runs no setter), runs the boot plan, tells the facts, then `Boot.started`, then loops
  until SIGTERM/SIGINT. `--port` becomes the fact `door_port` (0 = any free port); the pack `door.c1f` answers with
  `Http:door.listen`; once it is up the binary prints `cliffvm: door listening on <host>:<port>`.
- Facts the arguments tell (names as `membrane/packs/facts.json`): `door_port`, `ui_dir`, `app_dirs` (the folders of the apps given, the upper
  layer first; the door serves each one's `ui/` files, an upper layer's file shadowing the lower's; `--ui-dir` stays as the LOWEST layer), `seat_version`, `build_id`, `seat_cwd`,
  `seat_home`, `seat_name` (Bob), `seat_named`, `seat_aliases`, `seat_voice_out`, `seat_language`, `seat_blurp`, and the whole set
  as the dict `facts` (as `boot_started` writes it). There is no `door_secret_file`.
- `--store`: one SQLite file (default `./cliffvm.sqlite3`), WAL: the entity rows (`c_field`: eid -> {cls, props, writers}), the
  journal (`c_journal`), and what `sql(...)` in packs reads (read-only connections of the same process).
- The boot plan: this host runs none of the plan's steps (they are the Python seat's), so each is `skipped` and the plan only says
  when the boot is over. Then `started`.

## Run a seat

The barest usable agent seat: one command starts `cliffvm` on the shipped packs and the barest app, and the page is a graph viewer plus the one chat.

```
(cd native && cargo build --release --no-default-features --features bin)      # once; the launcher builds nothing
scripts/seat [--store ./.cliffvm/seat.sqlite] [--port 4096] [--open]
```

It runs `cliffvm run <the packs of membrane/apps/barest.packs, one file each> membrane/apps/barest.c1f --store <sqlite> --port <n> --fact chat_claude_bin=<claude>`
from the repo root and prints the URL. `CLIFFVM` names the binary (else `native/target/release/cliffvm`, else PATH); `CLAUDE_BIN` names the claude CLI
(else the one on PATH; with none, the seat starts and the chat answers with how to name it). `--open` opens the page on macOS. The launcher starts nothing else
(no Ollama, nothing under `~/.membrane`) and uses no port but `--port`.

**What you see** (`membrane/apps/barest.c1f/ui/`: `index.html`, `app.css`, `app.js`, `mcp.js`, `graph.js`, `chat.js`; plain static JavaScript served by the door
from the app folder, no build step, no dependencies; a later piece ports it to c1f view packs in the browser VM):
- Left, the GRAPH VIEWER: every entity as a node (a canvas force layout written for this page: repulsion, springs, centring, pan, zoom, node drag), coloured by class.
  Edges are ownership (`A:b:c` hangs under `A:b`, or under its nearest listed ancestor) and references (a `{"$ref": eid}` value or a string that is another listed eid, dashed).
  It polls every 3 s keeping positions, has a substring filter, a `requests` switch (the door's own Request entities are hidden by default), a list view, and a side panel with the
  selected entity's properties (a reference is a link). Keys on the canvas: arrows pan, `+`/`-` zoom, `0` fits.
- Right, the CHAT: the history is the chat's entities, so a reload brings it back; Enter sends, Shift+Enter adds a line; a sent message is polled every 700 ms (queued, working, answer) and
  shows the tools Claude ran with a short input. An answer that asks for something (no CLI named) is shown as a note.
- The page reads through `POST /mcp` (`entity_query`, `entity_read`) and `/membrane/chat/message`.

**`entity_query` additions for the viewer**: `offset` (paging), `kind!:<Class>` (all but a class) and `full` (rows carry the stored properties).
**Data** is the `--store` file (default `./.cliffvm/seat.sqlite`).

**The barest packs** (`membrane/apps/barest.packs`, one shipped pack name per line in load order; `scripts/seat` and `seat_process.rs` read it): `boot door fs proc fetch store door_tools chat chat_turn`,
each a single-file app (the first, `boot`, is the root of trust), then the app folder. The Python-era packs (`door_self`, `door_models`, `door_voice`, `door_state`, `door_decisions`,
`router`, `decisions`, `dev`, ...) stay in `membrane/packs/` for the Python seat and are NOT loaded here: a request carries none of their getters, and the seat seeds no
`Command:git`, `Command:gh` (dev.c1f) or `File:operator-ledger` (decisions.c1f).

**The request collector** (`membrane/apps/barest.c1f/collector.c1f`, an app pack: the Python seat does not load it). A door request is an entity `Request:<n>`; once it is ANSWERED (the
`respond` receipt has landed) it sets `dropped`, `kept_at` (the clock) and clears `body` and `headers`, so everything derived from them recomputes to something small (the door's `answer`
setter does not answer again once `dropped`); `request_keep_s` seconds after the answer (Boot fact, `--fact request_keep_s=<n>`, default 600: operator-approved) it writes `retire = True`
and the request is deleted. Also `entity_query` now selects the stored properties only when `full` is asked: they used to be in every query's rows, so each request held its
predecessors' whole state and the sizes doubled per request (a 100-row query reached megabytes and the engine stopped answering).
At start the collector retires the Request rows an earlier run left in the store (`Boot.old_requests`, setter `sweep-old-requests`).

**The help** (`membrane/apps/barest.c1f/help.c1f`, `help/c1f.md`, `help/packs.md`, `help/upgrade.md`): `Help:<topic>` entities whose `text` is the topic's markdown file (a getter reading it; `Boot.started` seeds
`Help:c1f`, `Help:packs`, `Help:upgrade` from the first of `app_dirs`). The model reads them with `entity_read` and lists them with `entity_query` `kind:Help`; the doctrine in `chat_turn.c1f` points at `Help:c1f` first. `help/banner.md` (`Help:banner`) teaches the banner.
`c1f` is the syntax (getters, setters, blocks, entity literals, constructors, nulls with reasons, the operators c1f has: the default is `or`, there is no `??`), `packs` is writing a pack through `Pack:<name>.text`
(ownership, rule B, layers), `upgrade` is what changed since 0.7.53 plus a SQL / Vue / JavaScript lookup table. **Every code block is a test**: `native/tests/help_1143.rs` loads each ```c1f block through the
loader and checks its `-- given / -- write / -- reads` lines (```c1f-error blocks must be refused with the text in `-- error:`), so the help cannot lie. Also `seat_process.rs` (the topics through the MCP door).

**The banner** (`membrane/apps/barest.c1f/default_banner.c1f`; the Python-era `banner.c1f` is not loaded here): `Banner:owner:main` (spelled `membrane::Banner:owner:main`, the chat's namespace) is the entity `chat_turn.c1f` reads into every turn, and it is
FULLY the model's: the seat never segments, orders or joins what the model puts there, and its `text` is a getter (cached, recomputed when what it reads changes), so the banner is live by nature. The static instructions (what the banner is for,
how to replace it) are in the system prompt (`chat_turn.c1f`) and `Help:banner`, never in the text. `Boot.started` births the instance once (`seed-banner`, guarded by `seeded`). The starting `text` is the barest cut of Memo's `RecallBanner`, assembled from
getters with dials as properties on the instance: KEPT the header (NOW, `CONVERSATION: main`) and the previous turn's error (read from the `ChatTurn` and `ChatResponse` entities); DROPPED recall, goals (OPEN), the ledger (DECIDED, STABLE), ABOUT / WORKING ON /
SINCE and the layout budget (no source of them on this seat; no marking convention for open/decided exists here). The model REPLACES it with a pack that imports `default_banner` and defines `Banner.text` (a runtime pack is the upper layer, its arm wins:
`docs/c1f-layers.md`). Writing `parents` on the banner to a class with its own `text` getter does NOT replace it: the entity's OWN class (`Banner`) is the nearest and its arm wins (one resolution rule, below; `banner_1143.rs` records it).
Tests: `banner_1143.rs`, `seat_process.rs` (`a_fresh_seats_banner...`, `a_pack_the_model_loads...`, the restart test: a model pack survives a restart and the banner is born once), `help_1143.rs` (every `Help:banner` example).
**Bare ids** (`door_tools.c1f`): `entity_read` and `entity_write` take `Banner:owner:main` for `membrane::Banner:owner:main` when only the `membrane::` row exists (an id with its own row, or no twin, is used as written); `entity_query name~:` stays a literal prefix.

**Restart restores the entities** (`host.rs` `load_stored_entities`, called by `cli.rs` after `load_stored_packs` and before the rebuild): every stored row arrives in the engine with its class and props
(the `parents` stamp is a prop), packs first, then the rest. A prop a getter wrote (`trigger:field:getter:*`, `rebuild`) is not read back: the rebuild computes it again, so a changed getter shows its new value. The classes in
`PER_RUN_CLASSES` (`Boot`, `Http`, `Request`, `Step`) describe the process, not the model's work: they stay stored text and the new run makes them again (the collector's start-up sweep still retires the old `Request` rows, so there is no
retention clock to carry over). No setter runs on a restore and nothing is rewritten. A message that was mid-turn when the seat stopped stays `running` (its Command had no receipt). Test: `seat_process.rs` `a_restart_restores...`.

**An entity of several classes**: its classes are the list in its `parents` (`cls:<name>` ids). Getters of every listed kind apply; a kind's setter fires when the ISA parent entity `cls:<name>` exists;
`entity_query kind:<K>` lists an entity by its own class or any `parents` kind. A `parents` write on an existing entity computes the getters of its kinds in that round (before, they bound only at birth or through a constructor).
Tests: `native/tests/multi_class_1143.rs`, `seat_process.rs` (`an_entity_carries_two_extra_classes...`, through a restart). **One resolution rule** (`resolution_1143.rs`): for each (entity, property) the getters of every class the entity carries form ONE ordered list of arms: its own class (the eid class), then `parents` in list order, then the implicit root `Entity`; within a class the top layer first (`docs/c1f-layers.md`). The first arm whose `WHEN` holds wins, a default (`ELSE:`) ends the search, a failing `WHEN` falls through to the next-nearer class. One node per (entity, property) runs the list (the nearest class's getter; `chain`, `arms_of`, `getters_of` in `fields.rs`). Levels are still ordered per class getter, so a fall-through arm that reads a higher-level getter settles in a later pass of the same round (its reads are its dependencies). The root pack `membrane/packs/entity.c1f` declares `Entity.summary` (id, classes, parents; no values). Setters are NOT changed by this rule (first write wins).

**Retiring an entity** (`native/src/fields.rs` `retired`, `host.rs` `drain_apply`): a write of `retire = true` to ANY entity, by any writer (a pack's setter: the ordinary ownership rules
apply; the model's `entity_write` as the privileged door), removes it: the engine forgets the entity (what read it recalculates), its stored row is deleted. It is derived when a setter wrote it,
so never journalled, and a replay re-derives it; an outside write of `retire` is journalled and replayed like any. Test: `native/tests/host_loop.rs` (retire + replay), `seat_process.rs`.

**Not there yet**: the UI as c1f view packs; tokens and persons (no security, one superuser chat); the chat's SSE stream (it polls); a message's class is `Chat` (its eid
names it), so the viewer finds chat entities by the `membrane::Chat:message:` prefix, not by a `ChatMessage` kind; the viewer lists at most 600 entities and reads the
properties of 6 per poll for reference edges (more as it keeps running); entity removal is the `retire` write above.
**Tests**: `native/tests/seat_process.rs` (the page and its files, a chat turn with a fake claude, the query options, `node --check` of every script).

## The host loop (`native/src/host.rs`)

Ported from `membrane/fields_live.py`, the minimum the door and the boot need:

- OUTSIDE writes (a door request born as `Request:<n>`, the boot's facts, a person's write) are heard, journalled, stored and given
  to the engine. A birth from outside goes in as an arrival (`Fields::arrive`: loaded, known, its getters dirty), so a setter on its
  writes fires, as the door always did; the host takes the door's requests itself (`Fields.inbound_by_host`), to journal them.
- The engine's output (`Fields::out`) is drained and applied to the rows with the writer stamped as `fields_live.stamp_writer` /
  `stamp_born_writer`: `trigger:field:<setter>` for the engine's own writes, `host:driver:trip` for a Trip and its birth (the
  python rule makes it `host:` + `driver:trip`, not `host:trip`), `host:door` for a request, `host:boot` for the boot.
- The clock: `Fields::tick` every second; the loop wakes at once when a driver thread finishes (query, file, command, request).
- The JOURNAL holds the outside writes only. Derived writes are never in it: replay re-derives them
  (`Host::replay`). The journal is kept in memory and in `c_journal`.
- Test (`native/tests/host_loop.rs`): a live run (door requests, a Trip with an injected clock) is replayed into a fresh engine and
  store and gives the same entities and rows (values and writers), and the same journal. A driver's receipt (`*_result`, `*_rc`) is
  an effect's answer and is not re-derived: a replay runs no effects, so the comparison leaves those props out.
- Process tests (`native/tests/cliffvm_process.rs`): the built binary on an ephemeral port serves a static file, boots through the
  real plan and answers a decisions route and a 501 route, stops on SIGTERM (hard timeouts, the child is always killed).

## Pack entities: the model loads its own packs (design, 2026-11-13)

- A **Pack** is an entity `Pack:<name>` whose `text` property holds c1f. The model writes it with the tool it already has:
  `entity_write {spec: 'Pack:mine', prop: 'text', value: '<c1f>'}`. No new tool, no new syntax. The name is what follows `Pack:`.
- Writing `text` loads (or replaces) the pack at once through `Fields::set_pack(name, text)`: its classes, getters and setters are live
  for the next write. Writing `text` to none or empty removes it (`remove_pack`; an entity is removed by the `retire` write).
- The host mirrors the outcome back as DERIVED writes (`trigger:field:pack`, never journalled): `loaded` (true when the pack is active),
  `error` (the loader's message, absent when fine) and `good_text` (the last text that loaded). The text write itself is the model's
  write (a Request's setter in the MCP door) and is replayed, so a replay loads the same packs in the same order.
- A refused text keeps the previous pack running: `set_pack` parses and checks (stale setter reads) BEFORE it replaces anything, and an
  import problem restores the old one. A refusal sets `error`; `loaded` stays what the running pack is. Restart: the packs folder loads
  first, then every stored `Pack:*` row (name order) from its `text`, falling back to `good_text` when the text is refused.
- The name and the text's `PACK` line: `set_pack` refuses a text whose `PACK <name>` line names another pack, so the id wins by
  refusal (the line is optional; the message says what it found). The same as `fields_live`.
- A Pack that imports a pack that is not there yet WAITS (kept, inactive) and activates when the import arrives; its `error` says
  what it waits for (Python leaves `error` empty for that and only for view packs waiting for `dom`; here the model needs to see it).
- Packs from the FOLDER stay files: loaded once at start, not entities, not reloaded. A Pack entity of the same name replaces the
  file's pack (it loads later).

**How the model loads a pack (as built).** One `entity_write {spec: "Pack:mine", prop: "text", value: "<c1f>"}`; read `Pack:mine.state`
(`loaded` / `error` / `waiting`) or `.error`. Rules the engine puts on what the pack can do:
- A class a pack declares is OWNED by it (rule A: only the owner defines its getters and setters; others IMPORT). A pack may WRITE only
  classes it owns or imports (rule B), unless it is PRIVILEGED (docs/c1f-layers.md, "Privilege"). `door_tools`, the pack behind the
  model's `entity_write`, is privileged: boot.c1f grants it at start (`Pack:door_tools.privileged = True`, `granted_by` records the boot
  setter). So the model's tool writes AND creates an entity of any class, including one a model-written pack owns:
  `entity_write {spec: "Lamp:desk", prop: "power", value: "on"}` makes `Lamp:desk` and its setters and getters run. No roundabout needed
  (the old way, a setter on the pack's own `Pack.mode` that makes the Lamp, still works). An UNPRIVILEGED pack (the model's packs are
  not, unless a privileged writer grants it with `entity_write {spec: "Pack:mine", prop: "privileged", value: true}`) is still held to
  rule B, and cannot write `privileged` at all (a setter, constructor, computed name or chain of setters: refused with a receipt).
- A pack that is removed (text emptied, or the Pack entity's text none) stops running and the values its getters left on entities are
  CLEARED (derived writes of none, journal-neutral; a property another pack still computes stays). A Pack entity with NO text (a grant
  for a pack loaded from a FILE) never removes that pack; only a written empty text does.
- The engine's rounds apply: a setter that writes the same property twice in one round is refused (`refused` on the entity).
- Test: `native/tests/pack_entities.rs` (the binary, the MCP door: load, hot reload, parse refusal, stale-setter refusal, PACK-line
  refusal, restart with a broken text falling back to `good_text`), `native/tests/host_loop.rs` (outside writes, journal, replay,
  removal, a pack waiting for an import), `native/tests/privilege_1143.rs` (rule B, grant, revoke, granted_by, cleanup, replay) and the two
  direct-write tests in `pack_entities.rs`.

## What `fields_live.py` does that the Rust loop does NOT yet do

- **Mirroring the Python entity store**: there is none here; the rows are the host's own table. No `est.docs` hear/mirror, no
  `mirrored()` by ISA kinds (`parents: ["cls:..."]`), no class-of-removal memory, no mirrored removal of entities (`f.remove`, #1054; the `retire` write, see the barest seat, does it for the engine and the store): on the barest seat the collector retires answered Requests; elsewhere a
  Request entity stays forever.
- **`silent_birth`** on engine-born entities (`{"born": cls}` is stored with no props).
- **Packs from entities, the rest**: `pack_op` (list/show/load), the shipped-pack rule, `_view_classes` (packs importing `dom` that
  run in pages), `Pack` entities with the Python name `membrane::pack:<name>` / a `c1f` property / any class holding c1f (here only
  `Pack:<name>.text`, see "Pack entities"; the privilege grant is the one other Pack property the host reads, `privileged`). Pack FILES are loaded once at start: no mtime rescan. A Pack
  entity can not be deleted (its text emptied removes the pack). Entities other than Packs are restored by `load_stored_entities` (see "Restart restores the entities").
- **Banner instances per owner and chat** (the barest seat has the one `Banner:owner:main`: see "The banner"), the dials (`recall:dials`), the class switch (writing `class` does not reassign an instance), facts per turn, `sql_forget`,
  the 3 s banner time limit, `mark_delivered` / `mark_seq`.
- **LLM call plumbing**: `Fields::calls` (the planner's `membrane::llm.call`) are drained and dropped; no LLM adapter.
- **Host callbacks** (`_Host.call`: `pronoun`, `recall`, `rerank`, the banner's built-ins): `NullHost` knows none; `resolve`,
  `dispatch`, `vel`, `align`, `live` answer nothing.
- **Adapters**, the worker pool, wait checks, replan, Blurp's learner and the store's write listeners that watch writes.
- **Presence**, peers, the `/event` hub (`publish` of `field.write` / `field.pack` / `field.remove` to pages), the browser VM's
  snapshot (`snapshot()`), the page feed.
- **Seat facts**: `seat_facts` reads the person's settings file, the router, the minds, the backend, cloud models,
  `agent_writes`, `nerve_pause_off`, `ledger`, `router_on`, `minds`, `seat_stt`, `seat_project`; the binary tells only the list above.
  `answer_card` (the board's own door) is a pack's now, but nothing calls it here.
- **The store as the seat's**: no `Store:field` seed entity, no `SQL_ARRIVED` queue item (the loop's waiter does that job), no
  `set_store` of a seat file other than its own; the seat's collections (`c_<coll>` of the Python entity store) are not the host's
  table (`c_field`): the two do not read each other.
- **Plan state is not journalled**: `plan::set` writes engine-side (writer `boot`); a boot re-runs the plan each start.
- **Error handling**: a failing write does not count `errors`, nothing is logged per write; a pack that fails to load is printed
  once on stderr and the last good one is not kept.
- **Shutdown**: SIGTERM stops the loop and interrupts running queries; there is no flush beyond SQLite's own, no `stop(srv)`.

## The barest agent seat: the MCP door and the chat (#1143, operator 2026-11-12)

The app is packs only: `door_tools.c1f` (the model's tools), `chat.c1f` (the one chat), `chat_turn.c1f` (the turn). Rust added
only three small things: a write with a computed property name (`<target>.[expr] = v`, `fields.rs`), the kind getters of a constructed
entity (a kind stamp came after the birth), and `cliffvm run ... --fact name=value` (any Boot fact, JSON or text). Run it with the
shipped packs (`boot fs proc fetch store door door_tools chat chat_turn`) plus the barest app folder (its `default_banner.c1f` births the banner) and
`--fact chat_claude_bin=<path to the claude CLI>` (no default: without it a message is answered with an error and nothing runs).

**Routes** (no persons, no auth, one chat `membrane::Chat:main`):
- `POST /mcp`: MCP over plain HTTP, JSON-RPC 2.0, one JSON answer per request: `initialize` (echoes the client's `protocolVersion`, else
  2025-03-26), `notifications/*` and any request with no id (202, no body), `ping`, `tools/list`, `tools/call`; `-32700` / `-32600` / `-32601`
  errors; a GET is a 405 (`allow: POST`: there is no SSE stream). The tools are the FIELD ops only, nothing else:
  - `entity_query {q, k, count}`: `kind:<Class>` and `name~:<eid prefix>` joined by `&` (anything else is a tool error), rows `eid (Class)`,
    k default 20 at most 100; reads the store's rows (`c_field`).
  - `entity_read {spec, prop}`: the stored properties of an entity, or one property in full (a getter's value too).
  - `entity_write {spec, prop, value}`: one property, by a computed-name write; what reacts runs (a setter, a driver verb such as
    `Command:x.run = {id, args}`); the receipt is read back as `<verb>_result`. The class must be one `door_tools` owns or imports
    (door, fs, proc, fetch, store) or one no pack owns yet; a refusal is in the reply. A write to an entity that does not exist and whose
    class no pack owns is dropped by the engine (the reply cannot tell: read it back). `door_tools` is PRIVILEGED (granted by boot.c1f
    at start), so it is not held to that class rule: it writes and creates any class, including one a model's pack owns.
  The #1101 `call` tool, aliases and the enter/exit context stack are out (skipped by decision).
- `POST /membrane/chat/message {text}` -> 202 `{id}` (`<epoch ms>-<request n>`); the message is `membrane::Chat:message:<id>` (kind
  ChatMessage: text, at, tools_run), written `queued` after its birth, which starts the turn.
  `GET /membrane/chat/message/<id>` -> `{id, text, state (queued | running | done | error), response, tools, turn: {state, started, ended}}`: poll it.
  THE TURN is the entity `membrane::Chat:message:<id>:turn` (ChatTurn: `state` written running, then done/error; `status` a getter over it, `queued` while
  there is none: a first value written with the birth and the start's write would be two writes in one round, which the loop guard refuses; `started`, `ended` = the clock; `queued_at`; `message`).
  It owns, by the colon path, the answer `...:turn:response` (ChatResponse: text, error, rc) and each tool Claude ran `...:turn:tool:<tool_use id>` (ChatTool: name, input, result).
  **A parent owns its children** (`host.rs` `drain_apply`): `retire = true` on an entity also retires every entity whose eid starts with `<eid>:` (engine and store), so retiring a turn retires its
  answer and tools, retiring a message its turn. Tests: `host_loop.rs` (`retiring_an_entity_retires_what_its_path_owns...`), `seat_process.rs` (`a_turn_is_an_entity...`).
  The page (`ui/chat.js`) draws each turn as one group: the message, the answer, the tools folded under it, the turn's seconds.
  The UI's own message route is opencode's `POST /session/<sid>/prompt_async` (`membrane/ui/panel.js`): a richer shape (parts, agent,
  model); this door does not speak it (open question below).

**The turn** is one run of the model's CLI through the `Command` driver, in the mode the Python rung used (`claude_agent.py`):

```
env CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 <chat_claude_bin> -p --output-format stream-json --verbose --include-partial-messages
    --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{"membrane":{"type":"http","url":"http://127.0.0.1:<port>/mcp"}}}'
    --allowedTools 'mcp__membrane__*' --setting-sources "" --no-session-persistence --max-turns <chat_max_turns>
    --system-prompt <the barest doctrine + the chat's banner> -- <the message>
```

Built-in tools off, no user MCP config (the config is an inline JSON string, generated by a getter from `Http:door`'s real port), no
settings files, allowed tools exactly the membrane server's, one continuous run, the system prompt is the barest doctrine
(`docs/doctrine-barest-draft.md`, the operator chose it for now) plus `Banner:owner:main`'s text (the banner, see "The banner"), the prompt is the message ALONE: no history. `--` ends the options (the
list-valued `--allowedTools` / `--mcp-config` would swallow a prompt). `--bare` is NOT used: per `claude --help` (2.1.323) it never
reads OAuth or the keychain, only an API key, so it cannot use the operator's CLI login. The stream-json stdout is read by c1f
(`json()` per line): the `result` line's text is the answer, else the assistant's text in order; `is_error` or a failing exit with no
result line is an error answer (with stderr); `error_max_turns` keeps what was said and says it stopped at the ceiling. Boot facts:
`chat_claude_bin` (no default), `chat_max_turns` (default 2048 in the pack: a THRESHOLD for the operator to approve; it is the old
rung's per-answer tool-call ceiling).

**Tests**: `tests/specs/door_tools.json`, `chat.json`, `chat_turn.json` (pack behaviour), `native/tests/chat_process.rs` (the binary
through the MCP door; the whole chat with a FAKE `claude` that records its argv, calls the door's `/mcp` itself, waits while the test
sees the message `running`, then prints stream-json; a second message proves no history is sent; a failing CLI and an unnamed one),
`dynamic_prop_1143.rs`, `cli_facts_1143.rs`, `constructors_1143.rs`. No real `claude` is ever run by a test.

**Verify on a real run** (none of this was run: the operator forbade real runs):
- `--max-turns` is NOT listed in `claude --help` of 2.1.323 (the docs list it): confirm the CLI accepts it and what it counts (turns, not
  tool calls). If it does not, the ceiling has no flag and needs another (`--max-budget-usd` is listed).
- `--mcp-config` as an inline JSON string, `"type": "http"`, with `--strict-mcp-config`; that the CLI sends `initialize` then
  `notifications/initialized` then `tools/list` to a plain-JSON server and uses the `inputSchema` as given; tools show up (not deferred
  behind tool search) and are callable as `mcp__membrane__<tool>` under `--allowedTools 'mcp__membrane__*'` with no permission prompt.
- `--tools ""` really leaves no built-in tool; `--setting-sources ""` loads no settings and no CLAUDE.md; whether anything else
  (hooks, plugins, skills from the login's home) still loads. `--restricted` (not used) is the stronger switch; it also removes command-running
  tools and WebFetch unless named.
- the CLI's login works without `--bare` and with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` through `env`.
- `--system-prompt-snapshot` (default on) and `--no-session-persistence` together: one run, no resume.
- the stream-json shapes read here (`system`/`init`, `stream_event`, `assistant` and `user` messages with `tool_use` / `tool_result`
  blocks, the `result` line with `subtype`, `is_error`, `result`) match the real CLI's.
- a message that starts with `/tmp/...` is refused by the Command driver as an argument under a temporary folder (the driver's rule
  looks at every argument starting with `/`).

**Not built: the SSE stream** (`GET /membrane/chat/<id>/events`, the response as it grows). It needs two things this host lacks: the
`Command` driver keeps stdout until the process ends (its receipt lands once: nothing grows), and the door answers one response per
connection (`Request.respond` writes the whole answer and closes; there is no open-ended `text/event-stream` response nor a verb to
append a chunk and one to close). The smallest design: `Command` writes each stdout line to a growing `run_lines` property as it
reads it (a partial receipt), and a `Request.stream` / `Request.send` / `Request.end` triple on the door. Until then the page polls
`GET /membrane/chat/message/<id>`.

**Open design questions**: (1) the UI's message route: DECIDED 2026-11-13: the opencode session API, simplified, for now (not built yet; this route stays until then); (2) the system prompt: DECIDED 2026-11-13: the barest
doctrine draft (`docs/doctrine-barest-draft.md`) plus the banner, for now; (3) `entity_write` writes any property of any class the door imports
(a prop like `cmd` or `run` is a program): the barest seat has no security by decision, so the guard is only the import list; (4) the
tool spec is an eid, not a name fragment as in the Python tool; (5) a kind's SETTER needs an ISA parent entity (`cls:chatmessage`):
`chat_turn` puts its setters on the class `Chat` instead.

## Open design questions

1. **Effects on replay.** A driver receipt is outside in nature (a connection answered, a file read) but stamped derived
   (`trigger:field:driver:*`) by the python rule. Replay here runs no effects and the comparison ignores receipts. Should receipts
   be journalled (outside), or should replay run the effects again?
2. **Who owns the entity rows.** The Python seat's store is the `EntityStore` over `db.rs` (a pyclass); the host has its own table.
   Converging needs `db.rs`'s core split from its Python binding (a refactor, not done).
3. **Request entities**: the barest seat's collector retires them (`retire`); a seat without the collector keeps them.
4. **Threading**: one engine behind one lock, the loop on the main thread, the door's accept and connection threads in `http.rs`.
   Enough for the door; a heavier app may want the loop and the engine's rounds separated.
5. **The command name** (`cliffvm` is working; `CARGO_BIN_EXE_cliffvm` in the tests and the `cliffvm-<version>` build id are the
   other places it appears).
