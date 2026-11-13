# The c1f standard library (design, 2026-11-12)

c1f has no functions and no macros. A pack is getters and setters over entities, so a "library" can only be **data contracts and
shared getters on a class** that every pack imports. The standard library is therefore the contract of the classes the OS is
built on, plus a small set of builtins. Principles:

1. **Declare, don't restate.** A pack states what it is (allowed methods, whether it is public) as a property; the shared getter does
   the work once. A route family never writes its own 405 or 401.
2. **Data, not code, crosses packs.** Facts the launcher knows are one dict; a family reads a key.
3. **No new builtin unless the language cannot say it.** Each proposed builtin below has a reason a pack cannot work around.
4. **Every shared getter has a spec** (`tests/specs/*.json`) and every contract is documented here.

## The Request contract (`membrane/packs/door.c1f` is the library for the class `Request`)
A request is an entity `Request:<n>`. A family pack `IMPORT door 1` and defines, for its family `<f>` (`models decisions state voice
session self tools`):

| getter | meaning | required |
|---|---|---|
| `Request.o_<f>` | True when the path is the family's | yes |
| `Request.r_<f>` | the reply `{status, headers?, body?\|file?}`; None while an effect has not landed | yes |
| `Request.m_<f>` | the allowed methods, a list, e.g. `['GET', 'POST']`; the door answers 405 + `allow` for any other | no (without it the family checks its own) |

Shared getters a family may read (all on `Request`): `method path query headers body peer`, `is_read`, `authed`, `sub`, `person`
(the verified name), `entrant`, `https`, `facts` (the launcher's facts dict), `jbody` (the body parsed as JSON, `{}` when it is
not), `qpairs` (the query as `[[k, v], ...]`), `ms` (now in epoch milliseconds, whole). Per-request outbound work is a per-request
entity: `('Fetch:' + this.conn).request = {...}` and read its `request_result`.

## Boot facts
The launcher writes each fact as a property of `Boot:seat` and also the whole set as the dict `facts`; a pack reads
`prop(this.facts, 'seat_name')` instead of a comprehension over `instances('Boot')`.
They are assembled by ONE pure function, `fields_live.seat_facts(env, settings, srv, known)` (tests/test_seat_facts.py): a missing
source leaves its fact out (never None); `door_port` exists only when `MEMBRANE_DOOR_PORT` is set (no door by default);
`door_secret_file` is a path, never the secret. The router's facts (`router_on`, `cloud_models`, `minds`) are only known after the
router step: they are added then (`boot_started(..., announce=False)`), after `started` was said, so a setter on `started` sees
them empty (see the router pack's `router-rungs` / `router-minds`).

`tail(text, n)` (added with the reply cap, #1143) is the mirror of `head`: a text's last n characters, a list's last n.

## Text builtins: `spans` (the tokenizer, #1143; needs the operator's approval as a new builtin)
`spans(text, layer)` is the native tokenizer's door (`native/src/text.rs`, docs/semtext-and-legs.md "Tokenizer (built)"): it returns
`[{start, end, text}]`, the layer's spans sorted and non-overlapping, `start`/`end` in chars (the unit of `len`, `head`, `tail`), `text`
the surface. `layer` is one of `sentence word identifier path url number`; another name is an error that lists them. Typed
`List(Dict)`; a pack cannot do this itself (UAX #29 tables are compiled in). `Msg.sents getter: spans(this.text, 'sentence')`.
It is INTERNAL: only the SemText pack (`membrane/packs/semtext.c1f`) calls it, and a model reads a text's `sentences`, `words`, ... properties instead (`Help:semtext`, which never teaches `spans`).
There is no `slice` builtin: a span's `text` is already cut, and `head(tail(t, len(t) - start), end - start)` cuts by offsets when a
pack holds only the offsets.

## Builtins proposed (each needs the operator's approval; none is added yet)
| builtin | why a pack cannot work around it |
|---|---|
| `int(x)` | truncating a float is `words(replace(str(x), '.', ' '))[0]` today: wrong for negatives and exponents |
| `dict(rows)` | build `{k: v}` from `[[k, v], ...]` (query params, grouped stats): c1f can only write a dict with fixed keys |
| `merge(a, b)` | one dict from two: a reply's headers, a family's extra fields |
| `keys(d)` | enumerate a dict (listing a settings object) |
| `urldecode(s)` | `%20` and `+` in a query value: no string primitive can say it |

## Conventions (the traps that bit)
- There is no size cap on an expression (the old 64-node / 400-character caps are gone). Name a part with a LOCAL (below) when it
  reads better; an evaluation has a step budget instead (see Blocks and locals).
- How getters are ordered (computed once per pack load, never per write): a getter sits above the getters it reads. The read is
  class-aware: `this.n` is `n` of the getter's own class; `x.n`, `g.n` and `prop(x, 'n')` are `n` of x's class when it is known
  (`this`, a loop variable over `instances('X')` or over a getter of known element type, a constructor, an entity literal, a
  literal-key subscript `x['n']` of such an entity). Where x's class is unknown (a plain property, a dict) the read still orders
  after every getter of that name, EXCEPT where that would close a cycle the known reads do not: a name shared with an unrelated
  class can no longer make a getter compute from a stale input. Strings in dict literals and list elements are data, never reads
  (a string is a name only as the last argument of a call). A cycle keeps the level it reached (its closing edge reads the
  previous value). Never name a property `this`.
- `concat` joins lists (`+` is vector addition); there is no `int()` yet; `clock` is epoch seconds.
- A class a shipped pack declares is owned: other packs `IMPORT` it.

## Blocks and locals (getters and setters)
A bare line `name = expr` (no dotted target; a write always has one, like `this.x = ..`) defines a LOCAL:
- visible to the LATER lines of its own block only; never a property, never stored, never readable as `this.name`, gone when the block ends;
- a getter's body is a block: locals first (one line each), the LAST line is the value (cached on the entity as before). Each arm is
  its own block (`X.f getter WHEN c:` with its lines indented below, or `WHEN c:` / `ELSE:` under `X.f getter:` with the lines below);
  one arm cannot see another arm's locals. Example:
  ```
  Box.total getter:
    base = this.w * this.h
    tax = base / 10
    base + tax
  ```
- a setter's body: locals feed the writes (and FOR loops) below them, per branch; a local made inside a FOR ends with it;
- a local that holds a reasoned null passes it on like any value; `Why(...)` (`why_chain`) lists the locals of the getter evaluation it explains
  (`locals: {name: value}`, a null shows its reason);
- the loader refuses a local that shadows a property of `this` (a name the class reads or writes as `this.<name>`, or a setter trigger),
  a getter of the class, a builtin or reserved name (`len`, `recall`, `this`, `clock`, a pronoun ...), a loop name or an earlier local;
  a local twice in one block is refused too; a block that ends in a local (no value) is refused; a local goes before the value.
No other syntax was added (no `;`, no multi-line local).

## Entity literals and `[key]` (operator, 2026-11-12)
An entity name written WITHOUT quotes is an entity, in an expression and on the left of a write: `membrane::Config:voice:in`,
`Goal:board`. `::` follows a namespace, `:` joins the segments. A colon is part of a name only when a name character follows it
DIRECTLY (`Goal:board`, `Config:voice:in`); the colon that ends a line (`WHEN cond:`, `X.f getter:`) or is followed by a space is
what it always was, and so is everything inside quotes. A segment after a colon may be a word that is an operator elsewhere
(`voice:in` is a segment, not the `in` operator). Write named arguments with a space after the colon (`kind: 'in'`): `kind:x` would
read as an entity.
- `[expr]` directly after an entity literal appends `:<value>` as the last segment: `membrane::Chat:message[123]` is
  `membrane::Chat:message:123`, `membrane::Config:voice:in[slug]` evaluates `slug` (a local, a getter or a loop name; a bare name
  is NEVER a literal segment). The value must be an integer or non-empty text. A float, bool, list, dict, entity or null gives a
  reasoned null (`error: the key of <entity> is ...`) and, on the left of a write, creates and writes nothing; a null key passes on
  its own reason.
- The path may go on after a `]`: `:segment` (directly after the `]`) is a CHILD entity, `.name` is a PROPERTY.
  `membrane::Chat:message[id]:response` is `membrane::Chat:message:<id>:response` (an entity); `message[id].response` reads the
  property `response` of the message. A `]` then a colon then a space or the end of the line still ends a `WHEN` / header line.
  Further `[key]`s may follow (`Chat:a[1]:b[2]`); `.prop` after the path reads a property: `membrane::Config:voice:in[slug].on`.
- On a list or dict, `[x]` stays an index; it is a key only when its left operand is an entity literal.
- The quoted spellings keep working unchanged: `('Goal:' + slug).status = ..` creates `Goal:<slug>` exactly as before (creation:
  `<Class>:<key>` of a class the pack owns or imports). Inference: a literal is `Entity(Class)` (the first segment after a
  namespace), nullable when a key may be null or is not an int or text.

## Constructors (entity classes only)
A call to a class name with NAMED arguments, on the right of a write whose left is an entity, makes that entity:
`membrane::Config:voice:in[slug] = Voice(kind: 'in', person: p)`, `membrane::Chat:message[id]:response = Response(text: t)`.
- **The identity comes from the target** (the left side). There is no `key` argument and no declared identity: the same class is
  made at any eid the target names, and the same target is the same entity.
- Creation is a write's creation path with a write's rules: the class must be one the pack OWNS (declares a getter or setter on) or
  IMPORTS; otherwise nothing is created and a receipt says `error: constructor ...` (a reasoned refusal, never a crash). A null or
  bad `[key]` creates nothing and the receipt carries its reason. The named arguments are set as the first properties (a null
  argument sets nothing); arguments are all named (`Voice(1)` is not a constructor) and none repeats.
- **Idempotent**: if the entity exists nothing is created again, and an argument fills only a property that has no value (an existing
  value is never overwritten). So a setter may construct on every request.
- **Foreign eid prefix**: when the target's first segment is not the class (`membrane::Config:...` built as `Voice(...)`; the class
  cannot be read from the eid) the entity is created as the class its eid names (`Config`, as the Python seat made it) and stamped
  `parents: ['cls:voice']`, the ISA kind-mirror (`voice_io.entity()` stamps the same), so the engine mirrors it by kind. A `Voice`
  getter then runs on it.
- Outside the right of such a write a constructor is a reasoned error. Inference: it is `Entity(Class)`, and its arguments are typed.
- Out of scope: value classes (`Std.Url(raw: x)` natives), `Import`, identity declarations.

## A write with a computed property name (#1143)
`<target>.[<expr>] = value` writes the property whose NAME the expression gives (non-empty text; anything else writes nothing, no
error). Everything else is a write: the creation path, the class ownership check (`IMPORT`), the receipt `refused`. The surface
snapshot lists it as `<target>.[]`. It exists for the MCP door's `entity_write {spec, prop, value}` (`door_tools.c1f`); a pack that
knows its property writes it by name.
A constructor of a foreign-prefix entity (`Config:x = Voice(...)`) computes the kind's getters at once: the kind stamp (`parents`)
is written after the birth, and the entity is born again for the getters. A SETTER of a kind still needs an ISA parent entity
(`cls:voice`): a setter on a kind reaches only entities whose parent chain holds that entity; write the setter on the class instead.

## Limits: the step budget and the time allowance (dials)
The old caps (an expression at most 64 nodes / 400 characters) are removed from the native VM; `membrane/ch.py` keeps them for its own
triggers (parity for the caps is intentionally dropped). Instead each evaluation has a STEP BUDGET: every node visited costs a step (a comprehension pays for each element it evaluates); past the budget the result is a reasoned null `error: budget exceeded (N steps)` (a getter's
`Why` shows it, a receipt says where), never a hang or a panic. The default is 100,000 steps (`DEFAULT_EVAL_BUDGET`), overridden by the env
`MEMBRANE_EVAL_BUDGET`. `VEC_MAX` (8192) stays. Structural limits that are not size caps: expressions nest at most 64 deep and
evaluate at most 256 deep (a long `a + b + c ...` is flat and unaffected). All accounting lives in `expr::Budget`.

**The time allowance.** Each getter or setter EVALUATION (not an AST node) also has a SELF-TIME allowance, default 10 ms
(`DEFAULT_EVAL_TIME_MS`, env `MEMBRANE_EVAL_TIME_MS`; both approved by the operator 2026-11-12, to be tuned). Self-time is the
elapsed time minus the time spent in host calls that wait on I/O (`expr::waiting`: the synchronous `Host::call`; `sql`, `jsonl`,
`read`, `listdir`, `stat` and the `call_later` functions only ask and return a pending null at once, so there is nothing to
subtract there). Over it, that node (only) becomes a reasoned null `error: timeout N ms (allowance M ms)` (N rounded up); `Why`
prints it like the budget's reason; the rest of the engine keeps running. The clock is read twice per evaluation (start, end),
and the step counter doubles as the in-evaluation backstop: every `TIME_CHECK_EVERY` (256) steps it reads the clock again, so a
runaway loop inside one evaluation trips before it ends. The clock is injectable (`expr::set_clock`) for tests.
**A kill is recorded, a timing is not** (operator, 2026-11-12). A time trip is the one outcome that is not deterministic, so
exactly the kills are written down, as TRIP ENTITIES (`membrane/packs/trip.c1f` declares the class, so the host mirrors it): when
a getter evaluation is killed the engine writes `Trip:<eid>#<prop>` {`node`, `self_ms`, `allowance_ms`, `at`, `active`} the way an effect
receipt arrives (writer `field:driver:trip`, a `born` then one write per property), so a journal that keeps the outside writes keeps
the kill and needs no new line type. An evaluation that finishes in time writes nothing: there is no timing log. The budget trip
(`budget exceeded`) is deterministic and is not recorded.
- **While it applies.** A node whose own Trip is `active` IS the reasoned null `error: timeout N ms (allowance M ms)` (read from the
  Trip's numbers; nothing is evaluated), and `Why` points at the Trip (`trip: <eid>`). It applies until one of the node's inputs changes:
  then the node is evaluated fresh, and a fresh evaluation that does not trip writes `active: false` (the kill stays as history; this
  write is derived, not journalled); one that trips again rewrites the Trip (a new `self_ms`/`at`, `active` written true again).
- **No loop.** The engine's own Trip write is applied to its mirror at once and is not an input of the node: the node is not
  re-evaluated for it, and the write is never fed back.
- **Replay.** A replay re-evaluates the node (fast: it does not trip), then the journalled Trip arrives and the node recomputes to the
  same null: the final state equals the live run. If an input changes in the same round as a Trip arrives, the input wins.
- **Not covered.** A SETTER's trip is a `refused` receipt on every run, not a node with a lifetime: it is not recorded yet. A Trip
  that is already in a restored state is not applied after a restart (the first evaluation is a fresh chance).
- **Host duty.** The host must journal the `field:driver:trip` writes as outside writes (today `fields_live.py` stamps every engine
  write `trigger:field:*`, which replay treats as derived), and feed them back on replay like any outside write.

## The contracts are executable
`native/tests/contracts.rs` fails when a contract is broken: every shipped pack loads together (view packs that import `dom` are
the browser's); a route family pack imports door, defines both `o_<f>` and `r_<f>`, is a reserved family, and does not redefine the
door's getters; every Boot fact a pack reads is in `membrane/packs/facts.json` (with a `why`), and a fact marked `wired` is written
by the launcher. `wired: false` entries are the launcher's to-do list for the cutover. A new fact = a registry entry; a new family =
a reserved name in `FAMILIES` and in the door's chain.

## The surface snapshot: a pack as a black box
`membrane/packs/surface.json` records, per pack, everything it touches from outside: its imports, its declared classes (and a count
of members), the Boot facts it reads, the paths it claims, and the properties it WRITES on other entities (`Goal:*.status`, `*.answer` for
a dynamic target, `ref(goal).replan` through a reference). `native/tests/contracts.rs` recomputes it from the pack text and fails on any
difference (unquoted entity targets count like quoted ones: a keyed `membrane::Config:voice:in[slug].on = ..` is `membrane:*.on`, and a constructor is listed as `membrane:*=Voice()`). Reviewing a pack, an agent's branch or Memo's edit means reading the surface diff, not the code: a pack that starts
writing a new property, claiming a new path or importing a new pack fails the test until the change is reviewed and recorded
(`SURFACE_UPDATE=1 cargo test --test contracts surface`). There is no `private` keyword: the contract is the surface.

## Natives: Rust inside core packs only (operator, 2026-11-12)
A shipped pack may carry `native <Class>.<getter> (<args>) -> <Type> rust:` blocks. A build step extracts them into a generated
Rust module compiled into the library and registered in the native-class table; the pack then uses the class like any other
(`Std.Url(raw: x).decoded`). The declared signature is the contract: arguments are checked like casts before the block runs (a value
of the wrong type or a null never reaches it: the call returns the reasoned null), and the return converts by type (`T?` is an Option,
`Result<T, String>`'s `Err` becomes `None('error: ...')`, a panic is caught and becomes a reasoned null, an unlisted Rust type is a
build error). A native is pure: it receives values and returns a value; I/O stays in the drivers. **Only core packs can contain Rust:**
a `native` block in a pack loaded at runtime (a `Pack` entity, which is what the model writes) is refused by the loader, and no toolchain
is needed on a seat. A sandboxed `native wasm:` variant for runtime packs is a later option.

## Inferred types (a report, enforces nothing)
c1f is dynamic at run time, but a getter's type follows from its definition. `native/src/infer.rs` (`infer_expr`, `infer_pack`,
`infer_packs`) deduces it from literals, the builtins' signatures, the types of other getters it reads (any order; a cycle is `Any`
and a finding), and the right-hand sides of setters (collected as the union of types WRITTEN to each `Class.prop`, shown next to
the getter's type when the property has both). Plain properties written from outside (Boot facts, Request fields, driver receipts,
JSON) are untyped edges: `Any`. A getter that reads its own name (`X.f getter: this.f`, the fallback arm) reads that written value:
also an edge, not a cycle.

Types: `Bool Int Float Num(=Int|Float) Text List(T) Dict Entity(Class) Any`, `?` for nullable, `|` for a union, `None` for the
null alone, `Never` for the element of `[]`. Rules (they MODEL the null semantics, they never run them):
- an operation on a nullable value is nullable (`Any + 1` is `Num?`); `x or d` strips the null of `x`; a getter whose last arm is
  missing is nullable (`Text?`); a list or dict miss is a null (`l[0]` is `T?`);
- `Int(x)`, `Text(x)` ... are checked casts: the target type, nullable unless `x` is already that type; `Int(x) or Text(x)` is not
  null when the two casts together cover `x`'s kinds; `x == None` is a null test (`Bool`);
- builtins have signatures (`len` is `Int` whatever it gets; `words` is `List(Text)`; `instances('Boot')` is `List(Entity(Boot))`;
  host functions `stat sql read ...` are `Any`); `Entity(C).p` is `C.p`'s getter type, or `Any`;
- a block's locals are typed in order: a local has the type of its value inside the lines below it (getters and setters alike).
- not modelled: dict value types (`Dict` is a bare `Dict`), flow-sensitivity, the host functions' results.

Findings are what is certainly wrong: text added to a number, an index or attribute on something that cannot take it, a builtin
given an argument of the wrong kind (`len(3)`), a wrong argument count, `<` between text and a number, a loop over a non-list, a
getter cycle. Nothing fails on them yet. `membrane/packs/types.json` is the snapshot of every shipped pack (`pack -> getters,
writes, findings`); `native/tests/contracts.rs` compares it and `TYPES_UPDATE=1 cargo test --test contracts types` regenerates it.

## Order of work
1. This document and the Request contract's shared getters with specs (`tests/specs/door_std.json`): `facts`, `jbody`, `qpairs`,
   `person`, and the declarative method gate `m_<f>`.
2. The builtins above, one at a time, each with a Rust test, after approval.
3. Migrate the families the agents wrote to the contract (drop their own 405 checks, fact comprehensions and JSON parses).
