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

## What an entity is called: `eid`, `cls`, `parents`

Every entity reads `eid` (its id: `Lamp:desk`), `cls` (its class) and `parents` (its class list, when it has one) next to its own properties; there is no `name` or `id`
(those read null). One call can list every pack with its state: the clifflet's own scratch packs are not listed, so the list is the field's (a Pack with no text yet reads `waiting`, a loaded one `loaded`):

```cliff
-- given Lamp:desk {"parents": ["cls:lamp"]}
-- given Pack:demo {}
-- value [["Pack:demo", "waiting"], "Lamp:desk Lamp [\"cls:lamp\"]"]
concat([[p.eid, p.state] for p in instances('Pack') if startswith(p.eid, 'Pack:d')], [join([l.eid, l.cls, str(l.parents)], ' ') for l in instances('Lamp')])
```

## A value that is null says why

A null with a reason comes back as `error: <the reason>`; a plain null is the text `null`. A missing property is a null that says `absent: <entity>.<property>`. A null INSIDE a list or a dict keeps its reason too, written `{"$null": "<reason>"}` in the text you read (a null with no reason stays `null`):

```cliff
-- error: nothing measured yet
None('nothing measured yet')
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

A block that runs past its step or time budget answers with that reason (`error: budget exceeded (N steps)`), and the seat goes on.

## Writes

A block whose last line is a write (`<entity>.<prop> = value`) runs its lines as writes, like a setter body (locals feed the lines below). It answers
with the `reactions:` of the round (each value the engine wrote because of the block, with the setter or getter that wrote it). A write that starts an
effect (`Command:x.run = ...`) has its receipt in a LATER round: read `<entity>.<verb>_result` afterwards (`Help:c1f`, "Effects are writes"). A Command starts with ONE write, its `cmd` in the run value:

A command that outlives its `timeout_s` (default 120 s; set your own estimate in the run value, `timeout_s: 600`) is killed, and its `output`, `errors`, `ok` and `rc` read as a null whose reason tells you how to retry:
`prop('Command:cl', 'output')` answers `error: Command:cl timed out after 120 s (killed); start it again with a bigger timeout_s ...`. What was collected is in `partial_output`.

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
