# Cliffhanger fields: the design (goal 606594617c19, issue #994)

Status: DRAFT for operator review. Nothing here switches on a live seat without the operator's go.
Syntax source: github.com/chedim/cliffhanger README (read 2026-11-09). Where the README is silent
the doc says so ("MEMBRANE EXTENSION") instead of inventing cliffhanger syntax.

## 1. The syntax (the syntax pass, 2026-11-09)

Membrane's syntax, made consistent, plus `ELSE` and `ALWAYS` from the spec. Cliffhanglets (pieces of
cliffhanger code: setters and getters) are stored in `.c1f` files (decided 2026-11-09). Besides the built-in goal
graph, packs load at runtime from `membrane/packs/*.c1f` and from `Pack` entities in the field
(`membrane::pack:<name>`, the text in `c1f`), which can be edited live (`membrane/fields_live.py`).

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
`native/src/syntax.rs`, and the goal graph ships in it (`native/packs/goalgraph.c1f`):

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
holds the text pack equal to the translated triggers, and the pinned field checksum is unchanged.
A trigger with no field form is refused, never guessed.

## 2. The rules (1-9)

1. **Fields.** Everything is a field on an entity; entities are instances of classes; a field is
   declared once on a class (name, type, getter, setters).
2. **Getters are cached and eager.** A new getter never computes on load (traces start at `{}`);
   after load it recalculates when an input changes. A field with a getter holds its last computed
   value. A read
   returns the cache; a read never writes and never fires anything.
3. **Own name = previous cached value, never an input.** Inside a getter the field's own name is
   its previous value: `x = (x or 0) + 1` is an accumulator. A getter's own field is never one of
   its inputs: reading its own name does not subscribe, so the getter recalculates only when a
   field other than itself changes. Example: `x = (x or 0) + 1` on `clock.tick` adds 1 per tick
   (it depends on `clock.tick` only) and does not run again because `x` changed.
4. **A recalculated value is a write through the setters.** When inputs change the getter runs
   and its result is written like any write. That write fires its dependents (rule 5) inside the
   current round (rule 8).
5. **Every change fires.** A write whose stored value differs fires the triggers/getters that
   depend on that field. A write of the same value is no change and fires nothing.
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
   through its own `set` guards. This widens the entity-scripts covenant in docs/entity-field.md
   (updated in the same PR).
4. **A rebuild never runs effects.** Recompute after migration, lost cache or a new getter is a
   silent restore. Accumulators come from the saved cache or from #971 journal replay.
5. **Two-phase setters**, as rule 7.

Cold start: new world = effects run once; warm restart = none; rebuild = silent (decision 4).

## 4. Today's constructs mapped

| Today | Field form |
| - | - |
| trigger `changed ?x.p` | an `on` setter (or dependent getter) on field `p` |
| `isa ?x C` | the field is declared on class C |
| `filter expr` | `WHEN expr:` in the guard/getter |
| `below p N` | `WHEN this.p < N:` |
| `every S` | a rule on the clock field (`WHEN clock - last >= S:`), rule 9 |
| write `?x.p = e` | a setter writing `p`, or a computed field `p` with getter `e` |
| method dispatch | a write to a method field; the adapter is its `on` setter |
| calls as writes (adapters.py) | `llm.call` etc. are fields; writing one is the call |
| VM WHEN/WHERE/WRITE | translation layer, removed at the end |

## 5. All 12 goalgraph.TRIGGERS rewritten

Counting becomes a computed field instead of decrement-on-event: idempotent and replay-safe. The
five counting triggers (1, 2, 6, 7, 8) are the getter; the other seven become the nine setters below (ready and wait-armed also run on
status), as shipped in `native/packs/goalgraph.c1f`:

```
-- The goal graph (#773, #994) in cliffhanger's field syntax (native/src/syntax.rs).
-- Migrated from packs/goalgraph.json by the translator; tests/syntax_994.rs holds the two equal.
--
-- The getter (built into the engine, fields.rs `recount`): a task's or wait's open blockers are counted, never
-- decremented, so a dropped or doubled write can't make them drift:
--   Task.open_blockers getter: the blockers of this (Task, Decision, Wait) that are not finished
--   finished: Task and Wait -> status == 'done'; Decision -> status == 'closed'

-- a task with nothing open in its way is owed (on the count, and on its own status)
Task.open_blockers on-setter ready:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'owed'
Task.status on-setter ready-on-status:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'owed'

-- a task or a card that ends asks its goal to replan
Task.status on-setter replan-task:
  WHEN (this.status == 'done' or this.status == 'failed' or this.status == 'stuck'):
    this.goal.replan = 'task ' + this.eid + ' ' + this.status
Decision.status on-setter replan-card:
  WHEN (this.status == 'closed' or this.status == 'dropped'):
    this.goal.replan = 'card ' + this.eid + ' ' + this.status

-- a wait with nothing open in its way is armed; when it is due, its adapter checks
Wait.open_blockers on-setter wait-armed:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'armed'
    this.armed_since = clock
Wait.status on-setter wait-armed-on-status:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'armed'
    this.armed_since = clock
Wait.due on-setter wait-check:
  WHEN this.due:
    this.check = 'due'

-- a goal asked to replan calls the planner (and again after a reopen with a new reason)
Goal.replan on-setter plan:
  WHEN (this.status or 'open') == 'open' and this.replan:
    membrane::llm.call = {'about': this.eid, 'prompt': 'plan the goal: ' + this.replan, 'lane': 'planner'}
Goal.status on-setter plan-after-reopen:
  WHEN this.status == 'open' and this.replan and this.planned_ts and this.replan != this.planned_replan:
    membrane::llm.call = {'about': this.eid, 'prompt': 'plan the goal: ' + this.replan, 'lane': 'planner'}
```
Wait example fields: `open_blockers`, `armed`, `armed_since`, `due`. Note: today's `wait check`
fires every 10 s; in fields `due` recalculates on each clock tick, so the check cadence is the
tick, and the adapter must be idempotent per `every_s`. The parity checksum will show any drift.

Getter rows 13 and 15: ASSUMPTION, confirm with the operator. I read them as rows 13 and 15 of
the entity-script group (migration rows 12-15): row 13 `autonode.py` tick scripts become
clock-driven input fields (decision 1); row 15 `chat_io.py` chat/say scripts become `on`
setters on a `say` field paced by freeze (decision 2).

## 6. Migration table, in the order we would do it

| # | Item | Source | Becomes |
| - | - | - | - |
| 1 | Parity gate | #971 journal | the checksum test (section 7) |
| 2 | Goal-graph triggers (12) | goalgraph.py | section 5 |
| 3 | Banner rules | graphbanner.py | computed fields |
| 4 | Recall policy | recall_policy.py #983 | guard setters |
| 5 | Calls as writes | adapters.py #959/#956 | `on` setters |
| 6 | Self learned triggers (179) | ch_learn.py | field rules |
| 7 | Seed packs | seedpack.py #982, #984-991 | field declarations (finish data moves only; no new WHEN/WHERE/WRITE) |
| 8 | Entity scripts (rows 12-15) | night.py, autonode.py, chat_io.py | getters/setters; sensors pushed |
| 9 | VM | native/ (Rust, the only VM target), ch_native.py | WHEN/WHERE/WRITE as translation layer, then removed. Fields are built on the native VM only; no Python VM (ch.py) work. Python VM removal is the separate goal membrane::goal:aeac0f121bf2 |

## 7. How

One conversion per item, behind the issue's switch; no item switches on a live seat without the
operator's go. Parity gate: replay the same #971 journal under today's triggers and under fields,
both sides run on the native VM; checksum the whole field (every entity's stored and cached fields, sorted) and compare. Per-rule
"Done means" tests stay as VM unit tests. The checksum gates every migration row.

## 8. Same-PR doc edits (not in this draft)

doctrine.md (sensor line, SCHEDULED READS) and docs/entity-field.md (entity-scripts covenant,
decision 3). Until the design lands, #980 PRs #984-991 add no more WHEN/WHERE/WRITE triggers.

## 9. The field engine in the browser

The same Rust engine (getters, setters, `.c1f` packs, imports) is compiled to WebAssembly (`native/build-web.sh`,
binding `native/src/web.rs`) and runs in the babyseat client (`membrane/ui/fieldvm.js`, loaded by `index.html`).

- **Following the seat:** the page starts from `GET /ui/fieldvm` (the seat's packs and every entity of a class they
  declare on), then applies each `field.write` and `field.pack` event from `/ui/serve/event`. When the stream drops it
  reconnects and takes a fresh snapshot. Toward the seat the page is read-only: what its packs write stays in the page.
- **View packs:** packs importing `dom` wait on the seat and run in the page. `DomNode:<key>` entities are elements:
  `tag` (allow-listed), `text` (textContent only, never HTML), `parent`, and allow-listed attributes (`href` http(s)
  only). A click writes `clicked`. A round's DOM writes are applied together, once per frame.
- **The page's own DOM:** `link(document.body)` puts every page element (open shadow roots included) in the field as
  `DomNode:page:#<id>` (`DomNode:page:<n>` without an id) with `page=true`, its tag, parent, id, own text and the
  allow-listed attributes. A new element is born whole, a change is written (only what differs from the field, so a
  pack's own write never echoes), a removed element leaves the field (`Fields::remove`: what read or referenced it
  recalculates). Packs read page elements, put their nodes in them (`parent = 'DomNode:page:#chat'`) and write to them;
  writing `text` replaces the element's content.
- **Caching:** the loader asks for the wasm by its content hash (`fieldvm_version.js`), since the seat caches `.wasm`.
- **Tests:** `tests/js/fieldvm_core.test.mjs` (the wasm engine in Node) and `tests/js/fieldvm_link.test.mjs` (the page
  link in headless Chrome over the DevTools protocol).
