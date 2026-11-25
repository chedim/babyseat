# Help:cliff - a block of the language in one call (the run_cliff tool)

`run_cliff {code}` runs a clifflet: a small block of c1f, the lines of a getter body. A line `name = expr` is a local (visible to the lines below it),
and the LAST line is the value, which comes back as text (JSON for a list or a dict). One call can read several entities, filter and join them, so a
whole look around the field is one step. Reads see the field as it is now; `instances('<Class>')` lists the entities of a class and `Class:name.prop` reads one.
Every example below is a clifflet that a test runs (`-- given <eid> <json>` makes an entity first, `-- value <json>` is what the call returns).

## Several reads in one call

```cliff
-- given Lamp:desk {"on": true, "watts": 40}
-- given Lamp:hall {"on": false, "watts": 60}
-- given Room:study {"lamp": "Lamp:desk"}
-- value {"count": 2, "watts": [40, 60], "study_lamp_on": true}
ws = [l.watts for l in instances('Lamp')]
{'count': len(ws), 'watts': ws, 'study_lamp_on': Lamp:desk.on}
```

## A filter and a join

`instances` lists in the order of the entity ids:

```cliff
-- given Lamp:desk {"on": true, "room": "study"}
-- given Lamp:hall {"on": false, "room": "hall"}
-- given Lamp:attic {"on": true, "room": "attic"}
-- value ["attic", "study"]
[l.room for l in instances('Lamp') if l.on]
```

## What an entity is called: `eid`, `cls`

Every entity reads `eid` (its id: `Lamp:desk`) and `cls` (its class) next to its own properties; there is no `name` or `id`
(those read null; `cls` is the first class of the entity's list, and `kind` is only a property like any other: the word `kind:` belongs to `entity_query`, where `kind:<Class>` matches any class on an entity's list, not only that first one). Test a class with `x is Class`. One call can list every pack with its state: the clifflet's own scratch packs are not listed, so the list is the field's (a Pack whose text is empty or not written yet reads `removed`, a loaded one `loaded`):

```cliff
-- given Lamp:desk {}
-- given Pack:demo {}
-- value [["Pack:demo", "removed"], "Lamp:desk Lamp"]
concat([[p.eid, p.state] for p in instances('Pack') if startswith(p.eid, 'Pack:d')], [join([l.eid, l.cls], ' ') for l in instances('Lamp')])
```

## A value that is null says why

A FAILURE (an Error: a failed cast, a getter that raised, a command that timed out, a block over its budget) comes back as `Error: <the reason>`; a null with a reason comes back as `null: <the reason>` (not an error: nothing failed); a plain null is the text `null`. A missing property is a null that says `absent: <entity>.<property>`. A null INSIDE a list or a dict keeps its reason too, written `{"$null": "<reason>"}` in the text you read (a null with no reason stays `null`):

```cliff
-- null: nothing measured yet
None('nothing measured yet')
```

```cliff
-- error: not-a-type: int expected, got text
Int('a')
```

```cliff
-- given Lamp:desk {"watts": 40}
-- text [40,{"$null":"absent: Lamp:desk.room"}]
[Lamp:desk.watts, Lamp:desk.room]
```

## A block that does not load

The loader's words come back, once for the block read as a value and once read as writes:

```cliff
-- error: not an expression
[x for x in
```

A block that runs past its step budget answers with that reason (`Error: budget exceeded (N steps)`), and the seat goes on ("The step budget" below).

## Inside a block: builtins, and the tools stay outside

`entity_query`, `entity_read` and `entity_write` are tools of the door, called by you; a block calls the builtins (`Help:c1f`). A tool's name in a block is answered with an Error:

```cliff
-- error: only the built-ins may be called
entity_read('Help:c1f')
```

Inside a block, read a class with `instances('<Class>')`, an entity's property with `x.prop` or `${x}['prop']` (also when the id is text you built), and one property of many entities with `${ids}['prop']`:

```cliff
-- given Lamp:desk {"watts": 40}
-- given Lamp:hall {"watts": 60}
-- value {"ids": ["Lamp:desk", "Lamp:hall"], "desk": 40, "all": [40, 60]}
ids = [l.eid for l in instances('Lamp')]
{'ids': ids, 'desk': ${'Lamp:' + 'desk'}['watts'], 'all': [${i}['watts'] for i in ids]}
```

`instances` lists the entities that are LOADED (a class declared `RESIDENT` is always loaded whole; `Help:packs`, "Storage"). For every stored entity of a class, loaded or not, use the tool `entity_query` (`Help:c1f`, "Finding entities").

## What a null or an Error usually means

| the block's last line | it answers | what it means |
|---|---|---|
| `None('nothing measured yet')` | `null: nothing measured yet` | a null that says why: nothing failed, the value is simply not there yet |
| `Lamp:desk.room` | `null: absent: Lamp:desk.room` | the entity is there and holds no `room` |
| `Lamp:nowhere.watts` | `null: absent: Lamp:nowhere does not exist` | no entity has that id |
| a getter that gave no value, `None` | `null` | a plain null, with no reason |
| `near(q, 'Vector', 3)` over no vectors | `null: empty: no vectors in class Vector` | nothing to search yet |
| `Int('a')`, `Error('...')` | `Error: <the reason>` | something FAILED; the reason says what to do |

Each row is run here:

```cliff
-- given Lamp:desk {"watts": 40}
-- null: absent: Lamp:desk.room
Lamp:desk.room
```

```cliff
-- null: absent: Lamp:nowhere does not exist
Lamp:nowhere.watts
```

```cliff
-- value null
None
```

```cliff
-- null: empty: no vectors in class Vector
near([1.0, 0.0], 'Vector', 3)
```

```cliff
-- error: the light needs a new bulb
Error('the light needs a new bulb')
```

## The step budget

Every evaluation (a getter, a setter, a block) has 100,000 steps. Each part of an expression it visits costs a step, and a comprehension pays for each item it walks; a native builtin (`len`, `sort_by`,
`group_by`, `props`, `near`, `vsum` ...) is ONE step however long its input. Past the budget the evaluation is an Error, `budget exceeded (100000 steps)`; the rest of the field keeps running, and a getter
over budget shows the Error until its inputs change. Three loops of 50 inside one another walk 125,000 items:

```cliff
-- error: budget exceeded
xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50]
len([len([len([c for c in xs if c > b]) for b in xs]) for a in xs])
```

A cheap getter hands the long walk to a builtin: `group_by(rows, key, ['col'])` in place of a total per key, `near` in place of a
cosine against every vector. Keep a loop off lists that grow without end (every message, every mention of a word: `mentioned_in`), or cut them first with `head` or `tail`.

## Writes

A block whose last line is a write (`<entity>.<prop> = value`) runs its lines as writes, like a setter body (locals feed the lines below). It answers
with the `reactions:` of the round (the values the engine wrote on the entities the block wrote, in full, with the setter or getter that wrote it and, for a null, its `why`; every other entity's changes only as counts on an `other changes:` line, `Class.prop xN`, plus any change that carries a reason). A write that starts an
effect (`Command:x.run = ...`) has its receipt in a LATER round: read `<entity>.<verb>_result` afterwards (`Help:c1f`, "Effects are writes"). A Command starts with ONE write, its `cmd` in the run value:

A command that outlives its `timeout_s` (default 120 s; set your own estimate in the run value, `timeout_s: 600`) is killed, and its `output`, `errors`, `ok` and `rc` read as an Error whose reason tells you how to retry:
`${'Command:cl'}['output']` answers `Error: Command:cl timed out after 120 s (killed); start it again with a bigger timeout_s ...`. What was collected is in `partial_output`.

```c1f-run
-- write Command:cl.run = {"id": "c1", "cmd": ["echo", "from a block"]}
-- receipt Command:cl.run_result.stdout = "from a block\n"
```

```cliff-write
-- given Lamp:desk {"watts": 40}
-- reads Lamp:desk.watts = 45
-- reads Lamp:desk.label = "desk 45"
base = 45
Lamp:desk.watts = base
Lamp:desk.label = 'desk ' + str(base)
```

The scratch the block runs in is removed after the call: nothing of it stays in the field except what your writes made. That scratch is a pack of its own that lives for the
one call, so a block holds lines of code and no header line: a class with a storage mode (`VIRTUAL`, `RESIDENT`), a getter or a setter that stays is a PACK. Keep it by writing its
text to the entity `Pack:<name>`: `entity_write {spec: "Pack:mine", prop: "text", value: "PACK mine 1\nRESIDENT Keep\n..."}` (`Help:packs`).
