# Cliffhanger fields: the design (issue #994)

The design of cliffvm's field language (getters, setters, packs), BUILT in the cliffvm repository (`cliffvm:src/`) and used by every pack of the release; the sections below keep the reasoning. The syntax a model writes is taught by `Help:c1f`, `Help:packs` and `Help:upgrade`
(`membrane/apps/barest.c1f/help/`), each code block of which is a test. The reference of what the host does is docs/cliffvm.md.
Syntax source: github.com/chedim/cliffhanger README (read 2026-11-09). Where the README is silent
the doc says so ("MEMBRANE EXTENSION") instead of inventing cliffhanger syntax.


## 1. The syntax (the syntax pass, 2026-11-09)

Membrane's syntax, made consistent, plus `ELSE` and `ALWAYS` from the spec. Cliffhanglets (pieces of
cliffhanger code: setters and getters) are stored in `.c1f` files (decided 2026-11-09). Besides the built-in goal
graph, packs load at runtime from `membrane/packs/*.c1f` and from `Pack` entities in the field
(`Pack:<name>`, the text in `text`: `entity_write {spec: 'Pack:<name>', prop: 'text', value: <c1f>}`), which can be edited live (docs/cliffvm.md, "Pack entities").

**Packs, imports and ownership (decided 2026-11-09).** A pack starts with its header:
```
PACK inflight 1
IMPORT goalgraph 1
IMPORT dom 1
```
- **A pack owns the classes it declares setters or getters on,** and the pack loaded first keeps them. The built-in
  goal graph (`PACK goalgraph 1`) is always first.
- **Declaring on a class another pack owns needs an `IMPORT` of that pack;** otherwise the pack is refused with the
  reason (`declares on Task, which belongs to pack goalgraph: add IMPORT goalgraph 1`).
- **Load order follows imports.** A pack whose import isn't loaded, or is loaded with another major version, waits
  inactive with the reason, and activates when the import arrives. A field holds one version of each pack.
- **Writes:** a setter may write entities of its pack's classes, of imported packs' classes, or of classes no pack
  owns. Writing another pack's class without importing it is refused with a receipt.
- **Creation:** a write to an entity that doesn't exist creates it when its id is `<Class>:<key>` and the class is
  the pack's own or imported (`('DomNode:inflight:' + this.eid).text = ...`). Its getters compute as a birth.
- **Reads** (getters, conditions) may read any entity. It's parsed by
`cliffvm:src/syntax.rs`, and the goal graph ships in it (`cliffvm:packs/goalgraph.c1f`):

```
-- a comment
Task.status on-setter replan-task:
  WHEN this.status == 'done' or this.status == 'failed':
    this.goal.replan = 'task ' + this.eid + ' ' + this.status
  ELSE WHEN this.status == 'stuck':
    this.goal.replan = 'stuck: ' + this.eid
  ELSE:
    this.note = ''
Goal.replan on-setter plan:
  ALWAYS: membrane::llm.call = {'about': this.eid, 'prompt': 'plan the goal: ' + this.replan}
```

- **A setter** starts at column 0: `<Class>.<field> on-setter <name>:`. It runs after a write to
  that field (the `on` phase, rule 7).
- **Branches:** `WHEN <expr>:`, then any number of `ELSE WHEN <expr>:`, then an optional `ELSE:`.
  The first true branch runs, and only it. `ALWAYS:`, or bare statements under the header, is a
  branch with no condition.
- **Statements:** `<target>.<field> = <expr>`, after the branch's colon or on lines indented under
  it. `this` is the entity written; another target is an expression (`this.goal`).
  `membrane::llm.call = <expr>` is the LLM call effect.
- **`this`** is the one name for the entity written. It replaces `$THIS` (getters), `x` (setters)
  and `?t` (triggers). `x` still works, so scripts written before the rename run unchanged.
- **Expressions:** the VM's expression language: `==`, `!=`, `<`, `and`/`or`/`not`, `in`,
  `x.prop`, `'a' + b`, `{k: v}`, `clock`.
- **Comments:** `--` outside quotes.
- **Getters:** `<Class>.<field> getter: <expr>`, the expression on the header line or on indented
  lines under it. The engine records what an expression reads while it evaluates. Those reads are the
  getter's inputs, and a write to one of them recalculates it (rule 2), lower levels first: a getter
  that reads another getter's field sits above it. Values compute once at start as a rebuild (stored,
  no setter runs). After that a changed value is a write and fires setters (rule 4). From the goal graph:
  ```
  Task.finished getter: this.status == 'done'
  Decision.finished getter: this.status == 'closed'
  Task.open_blockers getter: len([b for b in referencing(this, 'blocks') if not b.finished])
  ```
  `referencing(x, 'prop')` is the entities whose `prop` holds x. `[e for v in xs if cond]` and `len()`
  are in the expression language for getters like this.

Kept from the spec (github.com/chedim/cliffhanger README): `WHEN` / `ELSE WHEN` / `ELSE`,
`ALWAYS`, `--` comments, writes as `target = value`. Not taken: adjacent `WHEN`s merging with OR
(use `or`), `$`-variables and bash bodies (use `this` and expressions), XPath (decided 2026-10-26).

**Migration.** The old WHEN/WHERE/WRITE triggers translate into this form
(`translate::translated_goalgraph_setters`, printed with `syntax::print`). `tests/syntax_994.rs`
holds the text pack equal to the translated triggers. A trigger with no field form is refused, never guessed.

## 2. The rules (1-9)

1. **Fields.** Everything is a field on an entity; entities are instances of classes; a field is
   declared once on a class (name, type, getter, setters).
2. **Getters are cached and eager.** A getter's value is computed once when its pack loads (and at start, a rebuild: no setter and no effect runs), then recalculated by the engine
   when an input it read changes, never on a read. A field with a getter holds its last computed value; a read
   returns the cache, never writes and never fires anything. Loading or replacing a pack recomputes only the getters it changed or added
   (`Fields::set_getters`); an idle seat evaluates no getter.
3. **Own name = previous cached value, never an input.** Inside a getter the field's own name is
   its previous value: `x = (x or 0) + 1` is an accumulator. A getter's own field is never one of
   its inputs: reading its own name does not subscribe, so the getter recalculates only when a
   field other than itself changes. Example: `x = (x or 0) + 1` on `clock.tick` adds 1 per tick
   (it depends on `clock.tick` only) and does not run again because `x` changed.
4. **A recalculated value is a write through the setters.** When inputs change the getter runs
   and its result is written like any write. That write fires its dependents (rule 5) inside the
   current round (rule 8).
5. **Every change fires; an unchanged value stops the wave.** A write whose stored value differs fires the triggers/getters that
   depend on that field. A write of the same value is no change and fires nothing: a getter that recomputes to the value it already had wakes none of its readers (early cutoff).
6. **Setters.** A field has several setters, run in the order added. A write to the field's own
   name from inside its setter stores directly and bypasses the setters. Three jobs: effects
   (chat, exec, llm), guarding (refuse/alter), write-through computed values.
7. **Two phases (decision 5).** `set` setters run before the store, in added order, and may change
   or refuse the value (a refusal stops the chain, nothing is stored). The value is stored once.
   Then `on` setters run in added order, see the stored value, and do effects and writes to
   other fields.
8. **One round per outside write; loops.** Each outside write (rule 9) starts one round. In a
   round each downstream getter recalculates at most once, in dependency order. The edge that
   closes a cycle reads the previous value of its input. The round ends when every downstream
   getter has run once; the next outside write starts a new round. So cycles terminate and need
   no refusal inside getters. Examples: Russell's `R = not R`-style self-reference cannot
   subscribe to itself (rule 3), so R flips once per write to another field, never loops;
   `a = b + 1, b = a + 1`: a write to a runs b once (from the new a), then a's closing edge
   reads b's previous value, so a is not rerun and the round ends. A setter chain that returns to
   a field already written in the chain is still refused and reported (the existing loop rule,
   `writes.source` chains).
9. **Events come only from outside, as writes.** A sensor, a person or a watcher writes a field.
   Time is the clock tick, itself a write; there is no `every`. A timed rule reads the clock field.

## 3. The five decisions of 2026-11-09

1. **Sensors are pushed, never pulled on read.** Written from clock/watcher; a read never runs
   code. The doctrine line "getter script that reads its real source, so reading returns the world
   as it is now" and the SCHEDULED READS paragraph become "keep it fresh" (as fresh as the last
   tick or event). Read-triggered getter scripts become tick/watch-driven inputs.
2. **Freeze delays effects only.** On a hot entity, heat/gravity/freeze pace the `on` setters'
   effects (chat, exec, llm). Getters always recalculate at once.
3. **A setter may write any field the writer is allowed to write.** Each target field enforces
   through its own `set` guards. This widens the 0.7 entity-scripts covenant.
4. **A rebuild never runs effects.** Recompute after migration, lost cache or a new getter is a
   silent restore. Accumulators come from the saved cache or from #971 journal replay.
5. **Two-phase setters**, as rule 7.

Cold start: new world = effects run once; warm restart = none; rebuild = silent (decision 4).

## 4. The goal graph

The built-in goal graph (`PACK goalgraph 1`, `cliffvm:packs/goalgraph.c1f`; the engine loads it first, `Fields::goalgraph`) is the first pack written in this syntax: the getters `finished`, `open_blockers` and `due` and the
setters that ready a task, arm a wait and ask a goal to replan. The file is the source of truth (a copy here would drift). `cliffvm:src/translate.rs` turns the old WHEN/WHERE/WRITE triggers into this form and
`cliffvm:tests/syntax_994.rs` holds the text pack equal to the translated triggers. A counting getter (`open_blockers`) replaces decrement-on-event: it is idempotent and replay-safe.

## 5. The field engine in the browser

The same Rust engine (getters, setters, `.c1f` packs, imports) is compiled to WebAssembly (`scripts/build_wasm.sh`,
binding `cliffvm:src/web.rs`) and runs in the babyseat client (`membrane/apps/barest.c1f/ui/fieldvm.js`, loaded by `index.html`).

- **Following the seat:** the page starts from `GET /ui/fieldvm` (the seat's packs and every entity of a class they
  declare on), then applies each `field.write` and `field.pack` event from `/ui/serve/event`. When the stream drops it
  reconnects and takes a fresh snapshot. Toward the seat the page is read-only: what its packs write stays in the page.
- **View packs:** packs importing `dom` wait on the seat and run in the page. `DomNode:<key>` entities are elements:
  `tag` (allow-listed), `text` (textContent only, never HTML), `parent`, and allow-listed attributes (`href` http(s)
  only). A click writes `clicked`. A round's DOM writes are applied together, once per frame.
- **DOM v2 (W1, #1143):** `DOM_ALLOW` in `fieldvm.js` is the ONE table of what a pack may put in the page: tags (the
  old ones plus `input textarea select option form label button`), SVG tags (`svg g circle line path text rect`, made in
  the SVG namespace), attributes (`value placeholder disabled type name for tabindex rows checked selected multiple`, the SVG geometry names, and
  the `aria-*` / `data-*` prefixes). Refused, with a visible reason (`data-refused` on the element, `refused` {name:
  reason} on the DomNode): `style`, `src`, `srcdoc`, `formaction`, every `on*`, a `href` that is not http(s), and a tag
  outside the table (made a div). Events are written back as entity writes: `input`/`change` -> `value` (the text),
  `submit` -> `submitted` (time; the default navigation is cancelled), `keydown` -> `key` `{key, shift, ctrl, composing, time}` (`composing` = `isComposing`: an IME's Enter)
  (Enter without Shift in a textarea is `preventDefault`ed: that is how a chat sends), `focus`/`blur` -> `focused`
  (bool), `scroll` -> `scroll` `{top, height, client}`, `click` -> `clicked`. The other direction: a pack's write of
  `value`, `disabled` or `focused` (a `focus()` / `blur()` call) updates the element; an event whose value or flag the
  field already holds writes nothing, and a `value` equal to the element's own is not set (the caret and the user's
  typing stay). Page elements (`link`) get the same events. Test: `tests/js/fieldvm_dom2.test.mjs` (headless Chrome).
- **Shell gaps closed (w60, #1143):** a pack's write of `scroll` `{top: n}` or `'end'` scrolls the element (after the round's other writes, so
  the rows written in the same round are there; the measured `{top, height, client}` is written back, so the field holds what is true; an
  event's own `{top, height, client}` is no command). `before` (a sibling DomNode eid) puts a node above that sibling when it is made; writing
  it again moves the node; any other write never reorders. The shell's explicit drivers, called by the mount (nothing starts by itself):
  `host.run_clock(ms)` / `host.stop_clock()` (one `setInterval` that ticks the field; `host.timers` is the seam a test fakes) and
  `host.track_hidden()` (`Conn:page.hidden`, a bool, at start and on `visibilitychange`). Test: `tests/js/fieldvm_shell.test.mjs`.
- **The page's own DOM:** `link(document.body)` puts every page element (open shadow roots included) in the field as
  `DomNode:page:#<id>` (`DomNode:page:<n>` without an id) with `page=true`, its tag, parent, id, own text and the
  allow-listed attributes. A new element is born whole, a change is written (only what differs from the field, so a
  pack's own write never echoes), a removed element leaves the field (`Fields::remove`: what read or referenced it
  recalculates). Packs read page elements, put their nodes in them (`parent = 'DomNode:page:#chat'`) and write to them;
  writing `text` replaces the element's content.
- **Fetch (W2, #1143):** the page's VM has the seat's `Fetch` class (the pack text of `membrane/packs/fetch.c1f`, `FETCH_PACK`). A pack writes
  `Fetch:<id>.request = {id, url, method?, headers?, body?, timeout_s?}`; the shell makes ONE `fetch()` and writes `request_result` back in the
  seat's shape: `{id, status:'ok', rc:0, head:'<code> <text>', http_status, headers (lower-case names), body, started, finished, ms}`; a failure is
  `{id, status:'error', rc, head:<reason>, started, finished, ms}` (rc 2 refused before sending, rc 1 network failure, abort, timeout). A non-2xx is
  a normal result with its `http_status`. Same origin only (`location.origin`, credentials `same-origin`, so the admin cookie goes with it; any
  other origin is refused with a reason); verbs GET/POST/PATCH/DELETE; headers `content-type` and `accept` only (never authorization or cookie);
  `body` text only. No page-side timeout unless the request has `timeout_s`. `abort = true` on the entity aborts the one in flight. The same
  request id written again on the same entity is not fetched twice.
  **Stream (W3):** `request.stream = true` reads the body as SSE (bytes decoded as a stream, frames split on the blank line across reads; a
  comment, an empty frame and a frame with no `data` are ignored; an unterminated tail at the end is dropped). Each frame is a page-local
  entity `Chunk:<fetch eid>:<n>` = `{fetch: <fetch eid>, id: <request id>, n, data, done: false}` (n from 1; a multi-line `data` is joined with
  `\n`; the SSE `event` name is not kept, the seat's Chunk has no such key). The end is a last Chunk `{n: n+1, data: '', done: true}` (with `error`
  when the stream failed or was aborted), as the seat's `fetch.rs`, then `request_result` as for a plain fetch but with no `body`. A closed
  connection is a failure receipt (`status:'error'`); there is no auto-reconnect. Chunks are fed as mirrors (never fetched, never sent) and all are
  kept; the count is `n` of the last one. Test: `tests/js/fieldvm_stream.test.mjs`.
  What the seat's own snapshot and events carry (`feed(..., mirror=true)`) is never fetched again. "Toward the seat: read-only" now means the
  page may CALL the seat's routes this way; it still never writes the seat's field.
- **Caching:** the loader asks for the wasm by its content hash (`fieldvm_version.js`), since the seat caches `.wasm`.
- **Tests:** `tests/js/fieldvm_core.test.mjs` (the wasm engine in Node) and `tests/js/fieldvm_link.test.mjs` (the page
  link in headless Chrome over the DevTools protocol).
  `tests/js/fieldvm_clock.test.mjs` covers the eval clock: time decides no value, but stats read it, and on wasm32 std's `Instant` panics, so `expr.rs` reads `Date.now`
  there. `cliffvm:tests/wasm_check_1143.rs` keeps `cargo check --target wasm32-unknown-unknown --no-default-features --features wasm`
  green (it skips when the target is missing); the shipped `fieldvm_core*` files are rebuilt with `scripts/build_wasm.sh`.

## 6. The language, 2026-11-10: setters and getters support the same forms

- **Bare ifs** in any expression: `'on' if this.level > 0 else 'off'` (chains: `a if c else b if d else e`).
- **Arms** (alternative definitions of one field; the first whose condition holds wins):
  - getters, one line each: `Light.word getter WHEN this.level > 0: 'on'` ... `Light.word getter: 'off'` (the arm without
    WHEN is the default and comes last; two defaults, or an arm after the default, are refused at load);
  - getters, block form (as setters): `Light.word getter:` then indented `WHEN c: e` / `ELSE WHEN c: e` / `ELSE: e`;
  - setters: `WHEN` / `ELSE WHEN` / `ELSE` / `ALWAYS`, as before. Arms compile to bare ifs.
  - A default arm that reads its own field keeps the value: `X.f getter: this.f`.
- **FOR**:
  - in a setter body, a block: `FOR t IN this.listed:` with indented writes, run once per item, the list read when
    the FOR is reached (it sees what the writes above it made); nested blocks; an item that is an entity binds as
    one (`FOR t IN referencing(this, 'server'):` then `t.listed = ...`); None runs zero times; anything else that is
    not a list stops the setter with a `refused` receipt;
  - in any expression, a list: `FOR b IN this.listed: ${b}['title']` is `[${b}['title'] for b in this.listed]`.
- **`${x}[name]`** reads a dict's key and follows an entity's id (`${this.server}['state']`), tracked like
  `x.name`; `this.eid` is an entity's id (a reference is written as an id, never as an entity).
- **`x[i]`** (2026-11-12): a list by position (`xs[0]`, `xs[-1]` from the end), a dict by key (`d['a']`), an entity by property
  name (`e['name']`, the same as `e.name`). A miss is none, never an error, and a none base stays none. No slices (use
  `head`, `pluck`). It counts as one node toward the 64-node cap.
- **Entity values** (2026-11-12): a getter or setter may store an entity, or a list or dict holding entities. It is kept as
  a reference, `{"$ref": "<eid>"}`, and reads back as the entity (`this.open[0].name`); the stored list follows the
  entities as they change.
- **Host built-ins**: a getter may call a function the seat provides, on live rounds only (a silent rebuild or birth
  never calls out); an error in one is the getter's error (a `refused` receipt, the field keeps its value). Effects
  are never getters: they stay writes to adapter branches. The seat's slow ones (`recall`, `rerank`) run off the
  engine like `sql(...)`: the getter reads None until the answer lands, and the banner waits for them without the
  field's lock (a slow recall never stalls another chat). Each keeps its answer for its getter until
  that getter asks with other arguments, so a chain in one getter settles; each banner build starts fresh.

