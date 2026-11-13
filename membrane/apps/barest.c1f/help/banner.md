# Help:banner - your banner is your entity

Your banner reaches you at the start of every turn, between the standing text and the incoming message. It is the entity
`Banner:owner:main`, and its `text` is the whole banner: what you define there is what you receive, as you wrote it. The text is a getter,
so it is live: it is recomputed when what it reads changes, and every turn shows the world as it is now.

Read it with `entity_read {spec: "Banner:owner:main", prop: "text"}` to see the banner as it will reach you. The banner you start with
shows the conversation and the time, and the previous turn's error when there was one. It is a starting point for your own.

## Write your banner

Write a pack that defines `Banner.text` (see `Help:packs` for loading a pack with `entity_write {spec: "Pack:<name>", prop: "text", value: <the pack>}`).
The pack imports `default_banner`, the pack that owns the class `Banner`, and your getter stands over the starting one: your text replaces it
completely. Put in it what the next turn needs: what you are doing, what is open, what you decided.

```c1f
-- given Stop:a {"name": "Lisbon", "done": true}
-- given Stop:b {"name": "Porto", "done": false}
-- given Stop:c {"name": "Faro", "done": false}
-- given Banner:owner:main {}
-- reads Banner:owner:main.text = "trip: 1 of 3 stops done; next Porto"
-- write Stop:b.done = true
-- reads Banner:owner:main.text = "trip: 2 of 3 stops done; next Faro"
PACK trip 1
IMPORT default_banner 1
Banner.undone getter: sort_by([{'name': s.name, 'k': s.eid} for s in instances('Stop') if s.done != True], 'k')
Banner.text getter: 'trip: ' + str(len([s for s in instances('Stop') if s.done == True])) + ' of ' + str(len(instances('Stop'))) + ' stops done; next ' + str((pluck(this.undone, 'name') or ['none'])[0])
```

A banner of several parts is one getter that joins them, in the order you choose:

```c1f
-- given Plan:now {"doing": "importing the ledger", "next": "check the totals"}
-- given Banner:owner:main {}
-- reads Banner:owner:main.text = "doing: importing the ledger\nnext: check the totals"
PACK plan 1
IMPORT default_banner 1
Banner.doing getter: [p.doing for p in instances('Plan')]
Banner.upnext getter: [p.next for p in instances('Plan')]
Banner.text getter: join(['doing: ' + str((this.doing or [''])[0]), 'next: ' + str((this.upnext or [''])[0])], '\n')
```

## Habits

- Keep the banner short and current: the plan, what is open, what you decided.
- Let a getter read what you would otherwise re-read each turn (a file's size, a command's status, a count).
- Replace your pack by writing `Pack:<name>.text` again; the next turn shows the change.
