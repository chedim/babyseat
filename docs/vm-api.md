# The cliffhanger VM's API, as it is today (#971 step 1)

Since 0.7.5 membrane's engine is the native (Rust) VM (#971), with Python left only as the
launcher and the pip wheel. This page writes down the surface it provides.
It is taken from the Python code as it stands (`writes.py`, `entities.py`, `ch.py`,
`adapters.py`, `journal.py`), so a second VM can be built against it and checked by
replay. Where the code and this page disagree, the code wins and this page gets fixed.

## 1. A write is the only event

Every change to the field is a **write** (`writes.emit`, #576 A):

| field | meaning |
|---|---|
| `path` | `<eid>` (birth or removal), `<eid>.<prop>`, `<eid>.position`, or `rel:<subj>\|<rel>\|<obj>` |
| `eid`, `prop` | the path split by the writer (an eid may contain dots: `f:router.py`) |
| `old`, `new` | the overwrite: nothing → something is a birth, something → nothing a removal |
| `writer` | who wrote it: `person:<name>`, `llm:<model>`, `script:<eid>`, `trigger:<eid>` (the VM), `adapter:<branch>` (an effect's receipt), `self`, `field` |
| `chain` | `{id, root, via}`: the causal chain; `via` lists the triggers it passed through (the loop rule) |
| `ts` | when |

Writer and chain are provenance, never authority.

**Listeners** (`writes.on_write`) hear every write synchronously. They must be cheap and
must not write; the VM, the adapters and the journal each only queue.

## 2. The store

`EntityStore` holds entities `{eid, cls, properties, position, parents, ...}`:

- `upsert(ent)`: diffs the entity against its last snapshot and emits one write per
  changed property or move (`writes.diff`); a new eid emits a birth.
- `remove_many(eids)` / `remove(spec)`: emit removals.
- `docs`: the live map, eid → entity (reads).

## 3. Triggers (`ch.py`)

A trigger is an entity of class `Trigger`: `WHEN <watchers> WHERE <atoms>: <target> = <expr>`.

- **WHEN.** Event watchers: `changed(path)` (a write on a path; `new` / `old` are bound
  to the written values, #959) and `every(period)`. Level watchers: `near`, `away`,
  `above`, `below`, `vel`, `align`, `quiet`. Pulses: `pos`, `around`. A level watcher
  fires on a crossing it saw, never on what was already true (the first evaluation only
  seeds).
- **WHERE.** `isa(?x, Class)`, `is(?x, eid)`, `filter(expr)`, and
  `dispatches(?x, method, ?m)` (#959: the method `resolve_method` picks for `?x`).
- **WRITE.** `[target, expr]`, where the target is `?x.prop`, `<eid>.prop` or `.position`.
  The value is a scalar, a string, None, or a list of finite numbers; dicts and lists of
  those only for a call's arguments (`_writable`, `_call_envelope`).
- **Expressions.** A small safe language: arithmetic, comparisons, and/or/not, `x.prop`,
  `dot`, `norm`, `distance`, `unit`, `abs`, `min`, `max`, `prop`. The caps are
  `VEC_MAX` 8192. The old `EXPR_MAX` 400 / `NODES_MAX` 64 caps are gone from the native VM (ch.py parity for them is intentionally dropped): an
  evaluation has a step budget (`DEFAULT_EVAL_BUDGET` 10000, env `MEMBRANE_EVAL_BUDGET`; over it: the Error `budget exceeded (N steps)`).
  A getter body is a block of locals (`name = expr` lines) ending in its value (docs/c1f-stdlib.md).
- **Denied properties.** No trigger may write `command`, `owner`, `owner_sig`, `when`,
  `where`, `write`, `shadow` or `predicted`.
- **Index.** `changed` triggers are indexed by the property they watch (#957); level
  triggers and pulses are visited on every write, in definition order.
- **The loop rule.** A firing whose trigger is already in the chain's `via` is refused,
  unless `self` came after it.
- **Self's triggers** are signed with an HMAC under `.ch-key` beside the store. They run
  only with a learner attached.
- **Running.** `VM(store)`: `attach` / `detach` (listen), `define(name, when, where,
  write, owner)`, `drain()` (run what is queued), `sweep()` (the level watchers, asked
  for by the thinker), `start` / `stop` (the worker thread), `report()`.
  `QUEUE_MAX` is 4096 waiting writes, oldest dropped and counted.

## 4. Effects are writes (`adapters.py`)

An effect happens because something wrote an **adapter branch**. The branches are
`chat:<person>.say`, `<toy>.poke`, `membrane::llm.call` / `llm.wake` / `llm.unwake`,
`membrane::presence.restart`, `<method>.invoke` (a method run; a written call
`{object, call, request: {id, args}}` since #959), `<wait>.check`, and
`rel:<root>|execute|<ex>` (code runs).

The adapter consumes the branch (sets it back to `''`) and writes the receipt
(`<prop>_result`, `_rc`; for written calls, on the object's call path). Those writes are
`adapter:<branch>` and fire nothing. In the native VM, an adapter is a callback out to
the host (Python today), called with the write.

## 5. The journal and the replay contract (`journal.py`, #971 step 2)

Until #971 nothing kept the writes: the store keeps only the resulting state. `Journal(path,
store)` appends every write to JSONL:

    {"ts", "path", "eid", "prop", "new", "writer", "chain"[, "born": {"cls", "props", "position"}]}

A birth carries the entity's content, which a bare `(eid, None, eid)` can't rebuild.

- `replay(path, store)` applies every line and rebuilds the store.
- `Journal.watch(vm)` also journals the VM's sweeps with their time (`{"sweep": now}`), so what a sweep
  fires (`every`, a level crossing the thinker's beat sees) replays: `replay_vm` runs each sweep at its
  recorded time on a VM whose clock it feeds.
- `replay_vm(path, store, make_vm)` is **the VM contract**. Only the outside writes go in
  (everything not `trigger:*`; adapters' recorded receipts stand in for running the
  effects). A fresh VM is drained after each one, and its writes must equal the recorded
  `trigger:*` writes, in order. The final state must match too. A native VM passes the
  same test on the same journals (`tests/test_journal_971.py`).

Limits an evaluation runs under (native): a step budget (default 100,000, env `MEMBRANE_EVAL_BUDGET`) and a self-time allowance
(default 10 ms, env `MEMBRANE_EVAL_TIME_MS`; the time spent in host calls that wait is not counted). Over either, the node is a
Error (`budget exceeded (N steps)`, `timeout N ms (allowance M ms)`). The budget is deterministic. The time trip is not, so
replay needs it recorded: the kills only, never timings. A kill is a `Trip:<eid>#<prop>` entity written as a host write after the
evaluation (like an effect receipt: writer `field:driver:trip`), which the journal keeps as an outside write; on replay the node
evaluates, the journalled Trip arrives, and the node recomputes to the same null. It applies until an input of the node changes
(see docs/c1f-stdlib.md, Limits). The host must journal those writes as outside writes (open: `fields_live.py` stamps them `trigger:*`).

## 6. The native VM (`native/`, `membrane/ch_native.py`, #971 step 3)

A Rust core (PyO3) behind `ch.VM`'s API. `ch.make_vm()` always returns it (no switch, no Python fallback; a missing extension raises at startup) (`native/build.sh` puts `membrane/membrane_vm.so` in place).
The seat builds its VM
through `make_vm`.

- **In Rust:** the expression language (its own parser, with Python's semantics and `ast.walk`'s
  node count), a mirror of the field kept from its writes, the triggers' index, WHERE bindings,
  WHEN watchers with per-binding state, the loop rule, and self's chain rule.
- **In Python:**
  - `define` and `_validate`;
  - writing a firing (`_writable`, `_call_envelope`, the position check, the upsert);
  - Blurp's learner hooks (`observe`, `on_fire`, `landed`);
  - the lookups that follow the store's spelling rules, as callbacks: resolving a spec,
    `dispatches`, `vel` and `align`.
- **Firing order:** the drain is resumable. `step` returns one firing, the wrapper applies it,
  and the write comes back through the listener. So a later trigger on the same write reads what
  an earlier one wrote, as in ch.py.
- **Things changed in place, with no write:** a sweep re-reads the live store into the mirror
  first, because ch.py reads the live store. Only what its triggers can read: the entities they can
  bind or name, their positions only when a sweep trigger is geometric (near, away, pos, around),
  and every position only when an `around` trigger exists. A `changed` trigger counts as a sweep trigger here when it also has a level
  condition or a filter, since those read the store when its write arrives; a bare `changed` trigger
  costs a sweep nothing; when any `changed` trigger fires, the entities its write expression names are
  read from the live store first, so that cost is per firing. With no sweep trigger, a sweep reads
  nothing. A drain sees only what was written.
- **Values crossing to the core** are cleaned in one place (`ch_native._clean`): NaN and Infinity
  become null (as `cblstore.dumps` stores them), a lone surrogate becomes U+FFFD (keys too), an integer past a float's range becomes the largest
  float of its sign (the store keeps the real value), and nesting past 64
  becomes its text. A row that still can't be carried is left out and said in `errors`. If the native
  VM fails to start, `make_vm` raises at startup: there is no Python VM to fall back to.
- **Locking:** every call into the core is made under the VM's one evaluation lock, so the core's
  own lock is never contended while it calls back into Python (Memo's review of #972: a deadlock).
- **Known difference:** integers are 64-bit in the core. `9223372036854775807 + 1` is a number in
  ch.py and an error (no write) in the native VM.
- **CI:** the `native-vm` job builds `native/` and runs the VM suites against it.
- **The contract:**
  - `tests/test_ch_native_971.py`: a journal recorded with ch.VM replays identically through the
    native VM, and the expression language matches ch.py's.
  - Every VM suite (`test_ch_576`, `test_ch_index_956`, `test_ch_calls_956`, the Blurp suites, …)
    runs against the native VM.
  - The index tests read the native VM's trace (`vm.trace`) where they spied on
    `ch.VM._on_write`.

## 7. Gaps found while writing this down

1. **No write journal on the seats yet.** `journal.py` exists but no seat opens one, so
   there are no real journals to replay. Opening one on a live seat needs the
   operator's go, plus decisions on where it lives, how it is rotated, and its size. A
   seat makes many writes, and `.position` writes carry whole vectors.
2. **Relations** (`rel:*`) are journalled but not yet replayed.
3. **Signatures.** Self's triggers are signed with a key beside the store. A replay into
   another store has another key, so self's triggers don't run there unless the replay
   carries the key.
4. **Drain order.** The replay drains the VM after every outside write. The live VM drains
   on its own thread, so on a busy seat the order of trigger writes interleaved with
   outside writes can differ. A strict replay needs the drain points journalled, or a
   comparison that is per chain instead of global.
5. **Writes outside the store's diff.** Changes to `cls` and `parents`, and some
   in-place edits that bypass `upsert`, may not emit. Finding them is part of step 5:
   whatever doesn't emit can't be replayed.
6. **The store's one lock.** `cblite.py` takes one lock per database for every call, so a
   multi-core VM needs several connections, or a reader/writer split.
