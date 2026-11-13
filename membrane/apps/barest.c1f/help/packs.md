# Help:packs - writing a pack through entity_write

A pack is the text of getters and setters (see `Help:c1f`). You load one by writing its text to an entity of class `Pack`; its id is the
pack's name:

```
entity_write {spec: "Pack:lamps", prop: "text", value: "<the c1f text>"}
```

The host loads the text at once: its classes, getters and setters are live for the next write. Read the outcome back:

- `Pack:lamps.state` is `loaded`, `error` or `waiting` (a pack whose import is not loaded yet waits and activates when it arrives);
- `Pack:lamps.error` is the loader's message (the line and what it found);
- `Pack:lamps.good_text` is the last text that loaded. A refused text leaves the running pack as it was.

Write the whole text again to change the pack (it reloads in place). Write an empty text to remove it. An optional first line
`PACK lamps 1` names the pack (the name must be the id) and `IMPORT <pack> <major>` lines name the packs whose classes you use.
Comments start with `--`.

```c1f
PACK lamps 1
IMPORT proc 1
-- a class this pack owns, with a getter and a setter
Lamp.lit getter: this.power == 'on'
Lamp.power on-setter log:
  ALWAYS:
    this.last = this.power
```

After loading it, `entity_write {spec: "Lamp:desk", prop: "power", value: "on"}` creates `Lamp:desk` and its getters and setters run;
the reply lists the `reactions:` (each value your write made the engine write, with the setter or getter that wrote it); Help:c1f,
"Effects are writes", says what a write that starts an effect returns, and `wait`.

## Who owns a class

- Rule A: the pack that declares a getter or setter on a class OWNS the class; only the owner defines its getters and setters. A pack
  that wants to add to a class another pack owns writes `IMPORT <owner> <major>`. A getter on `Command` without that import is refused
  with `add IMPORT proc`:

```c1f-error
-- error: IMPORT
Command.shout getter: lower(this.cmd)
```

- Rule B: a pack WRITES only entities of the classes it owns or imports (or of a class no pack owns yet). `entity_write` itself is
  privileged: your writes through it reach and create any class, including one your own pack owns. A pack you load is not privileged;
  grant it with `entity_write {spec: "Pack:lamps", prop: "privileged", value: true}` (only a privileged writer grants).
- A pack's setter starts a command with one write, the `cmd` in the run value: `Command:x.run = {id: 'b1', cmd: ['make', 'all']}` (the write makes the Command and keeps the `cmd`); the receipt is `Command:x.run_result` (`Help:c1f`):

```c1f-run
-- write Command:pk.run = {"id": "k1", "cmd": ["echo", "from a pack"]}
-- receipt Command:pk.run_result.rc = 0
```
- The shipped packs of this seat, which you may import: `boot` (Boot facts), `door` (Request), `fs` (File, Dir), `proc` (Command),
  `fetch` (Fetch), `store` (Store), `default_banner` (Banner), `door_tools`, `chat`, `chat_turn`.

## Layers

Packs stack in the order they were loaded; a pack loaded later is above the ones before it (a pack you write is on top):

- getters: the arms of all layers form one list, the top layer's first; the first `WHEN` that holds wins, and an `ELSE:` arm in an
  upper layer ends the search there;
- setters: every setter on a property fires, the top layer first; a setter with the SAME NAME as a lower one replaces it;
- a layer overrides only what it imports (`IMPORT` the pack whose getter or setter you replace);
- the banner: a pack that imports `default_banner` and defines `Banner.text` replaces the banner you start with (`Help:banner`).

```c1f
-- given Note:a {"title": "x"}
-- reads Note:a.line = "note: x"
Note.line getter: 'note: ' + this.title
```

## Classes of one entity: the nearest wins

An entity has ONE ordered list of classes, its `parents` (`cls:<name>` entries); the nearest class is the first entry, and the root class
`Entity` is implicitly last (the pack `entity` declares `Entity.summary`: the id and the class list, in one line, on every entity). The class in
the id only starts the list: an entity born with no `parents` is the list [its id's class], one born with `parents` gets its id's class put
first; after that the id has no standing, and writing `parents` (the whole list) reorders, adds or replaces classes. For one property the
getters of all those classes form ONE list of arms, in list order, then `Entity`; within a class the top layer first. The first arm whose
`WHEN` holds wins, and an `ELSE:` arm ends the search. A `WHEN` that fails falls through to the next-nearer class, so an arm with no `ELSE:`
adds to what the farther class says instead of hiding it. A class you drop from the list takes its getters' values with it, and its setters stop firing.

```c1f
-- given Lamp:desk {"watts": 60, "parents": ["cls:fixture"]}
-- given Fixture:x {}
-- reads Lamp:desk.label = "lamp"
-- reads Lamp:desk.brightness = "bright"
-- write Lamp:desk.watts = 10
-- reads Lamp:desk.brightness = "dim"
-- reads Fixture:x.brightness = "dim"
-- reads Fixture:x.summary = "Fixture:x (Fixture)"
-- write Lamp:desk.parents = ["cls:fixture", "cls:lamp"]
-- reads Lamp:desk.label = "fixture"
-- write Lamp:desk.parents = ["cls:fixture"]
-- reads Lamp:desk.summary = "Lamp:desk (cls:fixture)"
Lamp.label getter: 'lamp'
Lamp.brightness getter WHEN this.watts > 40: 'bright'
Fixture.label getter: 'fixture'
Fixture.brightness getter: 'dim'
```

Writing `parents` is also the second way to replace your banner: give a class of your own a `text` getter and write `parents: ["cls:<yourclass>"]`
on `Banner:owner:main` (`Help:banner`).

## Storage: stored and loaded on demand, resident, or only in memory

A class has one of three storage modes, and ONLY THE PACK THAT OWNS THE CLASS declares it, in the header, one class per line (like `IMPORT`):

- (nothing) the default: its entities are STORED and loaded ON DEMAND. A restart does not load them; one loads when something refers to it (a read by id, a write, a getter that names it)
  and the seat also loads the newest ones in the background after it starts. `instances('<Class>')` lists only the entities that are LOADED. Use this for what can grow: messages, notes.
- `RESIDENT <Class>`: stored and ALWAYS loaded (at start, and the moment the pack declares it). `instances` lists them all. Use it for a class you list or scan as a whole, or that has a sensor, a command or a watcher.
- `VIRTUAL <Class>`: kept in memory only: never stored, never restored, not in the journal, not in `entity_query` (a block still reads the live ones with `instances` and by id). Use it for
  what describes the running process. What your setters write to stored entities while a virtual one triggers them is journalled, so a replay still ends in the same state.

A class belongs to one pack: a `VIRTUAL` or `RESIDENT` line in any other pack is refused, and naming both modes for one class is refused.

```c1f
-- given Stop:a {"due": 5}
-- given Stop:b {"due": 500}
-- given Report:r {}
-- reads Report:r.count = 2
-- reads Stop:a.late = true
PACK stops 1
RESIDENT Stop
Stop.late getter: this.due != None and this.due < 100
Report.count getter: len(instances('Stop'))
```

```c1f-error
-- error: only the owner
PACK greedy 1
IMPORT proc 1
VIRTUAL Command
```

Every entity also has `updated`, read-only and provided by the VM: the time of its last write (seconds since 1970), stamped with the write; reading it causes no recompute, writing it is refused:
`Note:a.updated`.

## Habits that keep packs working

- Load, then read `Pack:<name>.state`; fix `error` before anything else.
- Give each class getter a reading of its real source, so reading the entity returns the world as it is now.
- Keep one idea per getter; name a part of a computation as a getter or a local.
