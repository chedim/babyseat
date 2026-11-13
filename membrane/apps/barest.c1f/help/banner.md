# Help:banner - your banner, line by line

Your banner reaches you at the start of every turn, between the standing text and the incoming message. It is the entity
`Banner:owner:main`, and its `text` is made of lines: the `text` of every `BannerLine` entity, from the lowest `order` up (the default
order is 0; lines of the same order show by name), joined with newlines. A line whose text is empty adds nothing.

Each line is an entity `BannerLine:<name>`. You write its two properties with entity_write; the banner shows the change on the next turn:

```
entity_write {spec: "BannerLine:plan", prop: "text", value: "importing the ledger; next: check the totals"}
entity_write {spec: "BannerLine:plan", prop: "order", value: 10}
```

```c1f
-- given BannerLine:a {"order": 2, "text": "second"}
-- given BannerLine:b {"order": 1, "text": "first"}
-- given BannerLine:c {"text": ""}
-- given Banner:owner:main {}
-- reads Banner:owner:main.text = "first\nsecond"
IMPORT banner_lines 1
```

Lines of the same order show by name, and a line with order below 0 comes before the default:

```c1f
-- given BannerLine:b {"text": "b line"}
-- given BannerLine:a {"text": "a line"}
-- given BannerLine:top {"order": -5, "text": "top line"}
-- given Banner:owner:main {}
-- reads Banner:owner:main.text = "top line\na line\nb line"
IMPORT banner_lines 1
```

To replace a line, write its `text` again. To remove it, write `retire = true`:

```
entity_write {spec: "BannerLine:plan", prop: "retire", value: true}
```

## A live line

A line becomes live when a getter gives it a `live` text: the line then shows the current state of what the getter reads, every turn,
with nothing for you to rewrite. (A line shows its `live` when it has one, else its stored `text`.) Load the pack with
`entity_write {spec: "Pack:trip", prop: "text", value: <the pack>}` (see `Help:packs`) and read `Pack:trip.state`. The pack imports
`banner_lines`, the pack that owns `BannerLine`; the getter names its line with `WHEN this.eid == '<the line>'` and ends with `ELSE: None`
so the other lines keep their stored text:

```c1f
-- given Stop:a {"name": "Lisbon", "done": true}
-- given Stop:b {"name": "Porto", "done": false}
-- given Stop:c {"name": "Faro", "done": false}
-- given BannerLine:trip {"order": 5}
-- given BannerLine:note {"order": 6, "text": "kept as written"}
-- given Banner:owner:main {}
-- reads BannerLine:trip.live = "trip: 1 of 3 stops done; next Porto"
-- reads Banner:owner:main.text = "trip: 1 of 3 stops done; next Porto\nkept as written"
-- write Stop:b.done = true
-- reads Banner:owner:main.text = "trip: 2 of 3 stops done; next Faro\nkept as written"
PACK trip 1
IMPORT banner_lines 1
BannerLine.undone getter: sort_by([{'name': s.name, 'k': s.eid} for s in instances('Stop') if s.done != True], 'k')
BannerLine.live getter:
  WHEN this.eid == 'BannerLine:trip':
    'trip: ' + str(len([s for s in instances('Stop') if s.done == True])) + ' of ' + str(len(instances('Stop'))) + ' stops done; next ' + str((pluck(this.undone, 'name') or ['none'])[0])
  ELSE: None
```

A line's text can be any reading, a short list too: `join([...], '; ')`.

## Habits

- One idea per line, so a line can be replaced or removed alone. Give lines orders with gaps (10, 20, 30) to insert between later.
- Put what the next turn needs first: the plan, what is open, what you decided. Keep a line short and current.
- Write a live line for anything you would otherwise re-read each turn (a file's size, a command's status, a count).
- Read `Banner:owner:main` (entity_read, prop `text`) to see the banner as it will reach you.
