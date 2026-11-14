# Help:upgrade - what changed in c1f since the last public release (0.7.53)

Everything below is live in this seat; each block is loaded and run by a test.

## Blocks and locals (getters and setters)

One line `name = expr` is a local, visible to the lines below it in its own block. A getter ends in its value. Locals are never
stored; a local may not take the name of a property, getter, builtin or loop variable (the loader says which).

```c1f
-- given Box:a {"w": 2, "h": 5}
-- reads Box:a.total = 11
Box.total getter:
  base = this.w * this.h
  tax = base / 10
  base + tax
```

## No node cap, a step budget, a 10 ms allowance

An expression may be as long as it needs. Each evaluation has a step budget (100,000 steps) and a time allowance of 10 ms of its own
time (waiting on a read does not count). An evaluation over either becomes an Error with the reason (`budget exceeded`, `timeout N ms`);
the rest of the field keeps running. A time kill is recorded as an entity `Trip:<eid>#<prop>` ({node, self_ms, allowance_ms, at, active}):
list them with entity_query `kind:Trip`.

## Nulls carry a reason

A missing property is a null with the reason `absent: <entity>.<property>`; an operation on a null is that null; `None('reason')` makes one; `or` replaces it. `why(x)` reads the reason. A null inside a list or a dict keeps its reason (`why(rows[0])`).
Casts `Int Float Num Text Bool List Dict` check a type (`Int('a')` is an Error saying `not-a-type: int expected, got text`: a failure, not a null, see `## Errors` below; so `Int(v) or 0` does not default, write `v if v is Int else 0`).

```c1f
-- given Box:a {"raw": "a"}
-- reads Box:a.n = 0
-- reads Box:a.why = null
-- reads Box:a.gone = "absent: Box:a.price"
-- reads Box:a.row_gone = "absent: Box:a.price"
Box.n getter: this.raw if this.raw is Int else 0
Box.gone getter: why(this.price)
Box.row getter: [this.raw, this.price]
Box.row_gone getter: why(this.row[1])
Box.why getter: None('waiting for a price') if this.price == None else this.price
```

## Errors: a failure is not a null

A null is "not set, absent, not applicable" (it keeps its reason). An ERROR is a failure: it carries its reason, and the reason says what to do. A failed cast, a getter
that raised, a block over its step budget or time allowance, a command that timed out, an entity key that is null or empty text, and `Error('reason')` in a pack are Errors.
`x is Error` tests one, `why(x)` reads its text, and `x == None` is FALSE for it. An Error spreads: arithmetic, `x[i]`, `x.a`, comparisons, `not`, `and`, `or`, an `if` or
`WHEN` condition and every call over an Error give that same Error (the first one wins), a getter that reads an Error returns it unchanged, and a `WHEN` over an Error does
NOT fall through to the next arm. So `or` never hides one: `x or 0` over an Error is the Error; write `0 if x is Error else x`. `x is not Error`, `x is Int` (kinds `Int Float
Text Bool List Dict Error`), `x is Trip` (an entity of that class, by its `parents`, nearest first, ending at `Entity`) and `x is (Trip, Place)` (all of them) read a value without
spreading it. Stored, a derived Error is a plain null (the old routes see null; `entity_read` and `run_cliff` print `Error: <reason>`); a setter that writes an Error is
refused, so a failure is never stored as if it were a value. In a list or dict an Error is kept while one expression runs and is a plain null once stored.

```c1f
-- given Box:a {"raw": "a"}
-- reads Box:a.n = 0
-- reads Box:a.failed = true
-- reads Box:a.why = "not-a-type: int expected, got text"
-- reads Box:a.is_null = false
-- reads Box:a.after = true
-- reads Box:a.kind = "text"
Box.bad getter: Int(this.raw)
Box.n getter: 0 if this.bad is Error else this.bad
Box.failed getter: this.bad is Error
Box.why getter: why(this.bad)
Box.is_null getter: this.bad == None
Box.after getter: (this.bad + 1) is Error
Box.kind getter: 'text' if this.raw is Text else 'other'
```

`Error` is reserved: no class and no local may be called `Error`.

```c1f-error
-- error: reserved
Error.size getter: 1
```

## Entity literals and constructors

An entity name without quotes is an entity (`Goal:board`); `[key]` appends a segment; `Class(name: value)` constructs.

```c1f
-- given Shelf:s {}
-- write Shelf:s.add = "Dune"
-- reads Book:copy:dune.title = "Dune"
Book.label getter: this.title
Shelf.add on-setter add-book:
  ALWAYS:
    slug = lower(this.add)
    Book:copy[slug] = Book(title: this.add)
```

## Retire: remove an entity

A write of `retire = True` removes the entity from the field and the store (any writer):

```c1f
-- given Box:a {}
-- given Box:b {}
-- write Box:a.done = true
-- reads Box:a.done = null
Box.size getter: 1
Box.done on-setter bye:
  WHEN this.done == True:
    this.retire = True
```

## Apps, layers, privilege, Pack entities

- An app is a folder `<name>.c1f/` of packs (loaded in dependency order) and data files; several apps are layers, the later one above.
  Trust is by position: the first app is the root of trust.
- Layers: getter arms stack (top first), setters fire top first, a same-named setter replaces, a layer overrides only what it imports.
- Privilege: `Pack:<name>.privileged = true` exempts a pack from rule B (writes to any class); only a privileged writer grants it.
- Pack entities: `entity_write Pack:<name>.text` loads a pack at runtime; `.state`, `.error`, `.good_text` report back. See `Help:packs`.
- Writes with a computed property name: `target.[expr] = value`. Boot facts from the command line: `--fact name=value`.

## If you know SQL, Vue or JavaScript

| you know | in c1f |
|---|---|
| SQL `COALESCE(a, b)`, JS `a ?? b` | `a or b` (a null only: `b if a == None else a`) |
| SQL `CASE WHEN c THEN x ELSE y END`, JS `c ? x : y` | `x if c else y`, or `WHEN c: x` / `ELSE: y` arms |
| SQL `SELECT ... FROM t WHERE c`, JS `arr.filter(...).map(...)` | `[f(r) for r in instances('T') if c]` |
| SQL view | a getter |
| SQL trigger | `Class.prop on-setter name:` |
| SQL `INSERT`, JS `new Thing(...)` | `Thing:id = Thing(a: 1)` |
| SQL `DELETE` | `this.retire = True` |
| SQL `COUNT(*)`, JS `arr.length` | `len(...)` |
| Vue `computed` | a getter |
| Vue `watch` | an on-setter |
| Vue `ref` / `reactive` | a property of an entity |
| Vue `v-if`, `v-else` | `WHEN` / `ELSE` |
| Vue `v-for` | a comprehension (getter) or `FOR x IN xs:` (setter) |
| JS template literal | `'a ' + str(x)` |
| JS `const x = ...` | a local line `x = ...` |
| JS `try / catch` | a null with a reason; test it with `== None`, default it with `or` |
| JS `fetch(url)`, SQL `SELECT` over the network | write `Fetch:<name>.request = {...}`, read `request_result` |
| JS `x % y`, `x ** y`, `x // y` | not in c1f: refused when the pack loads |

```c1f
-- given Box:a {"w": 2}
-- given Box:b {"w": 9}
-- given Query:q {}
-- reads Query:q.heavy = 1
Query.heavy getter: len([b for b in instances('Box') if b.w > 5])
```
