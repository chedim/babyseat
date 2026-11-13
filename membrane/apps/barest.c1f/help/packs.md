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

After loading it, `entity_write {spec: "Lamp:desk", prop: "power", value: "on"}` creates `Lamp:desk` and its getters and setters run.

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
- The shipped packs of this seat, which you may import: `boot` (Boot facts), `door` (Request), `fs` (File, Dir), `proc` (Command),
  `fetch` (Fetch), `store` (Store), `banner`, `door_tools`, `chat`, `chat_turn`.

## Layers

Packs stack in the order they were loaded; a pack loaded later is above the ones before it (a pack you write is on top):

- getters: the arms of all layers form one list, the top layer's first; the first `WHEN` that holds wins, and an `ELSE:` arm in an
  upper layer ends the search there;
- setters: every setter on a property fires, the top layer first; a setter with the SAME NAME as a lower one replaces it;
- a layer overrides only what it imports (`IMPORT` the pack whose getter or setter you replace);
- the banner: give `Banner:<owner>:<chat>` a class of your own with a `text` getter to replace it, or add arms to the banner's.

```c1f
-- given Note:a {"title": "x"}
-- reads Note:a.line = "note: x"
Note.line getter: 'note: ' + this.title
```

## Habits that keep packs working

- Load, then read `Pack:<name>.state`; fix `error` before anything else.
- Give each class getter a reading of its real source, so reading the entity returns the world as it is now.
- Keep one idea per getter; name a part of a computation as a getter or a local.
