# Help:banner - your banner is your entity

Your banner reaches you at the start of every turn, between the standing text and the incoming message. It is the entity
`Banner:admin:main`, and its `text` is the whole banner: what you define there is what you receive, as you wrote it. The text is a getter,
so it is live: it is recomputed when what it reads changes, and every turn shows the world as it is now.

Read it with `entity_read {spec: "Banner:admin:main", prop: "text"}` to see the banner as it will reach you. The banner you start with
shows the conversation and the time, and the previous turn's error when there was one (its entity id and its whole text). It is a starting point for your own.

## Write your banner

Write a pack that defines `Banner.text` (see `Help:packs` for loading a pack with `entity_write {spec: "Pack:<name>", prop: "text", value: <the pack>}`).
The pack imports `default_banner`, the pack that owns the class `Banner`, and your getter stands over the starting one: your text replaces it
completely. Put in it what the next turn needs: what you are doing, what is open, what you decided.

```c1f
-- given Stop:a {"name": "Lisbon", "done": true}
-- given Stop:b {"name": "Porto", "done": false}
-- given Stop:c {"name": "Faro", "done": false}
-- given Banner:admin:main {}
-- reads Banner:admin:main.text = "trip: 1 of 3 stops done; next Porto"
-- write Stop:b.done = true
-- reads Banner:admin:main.text = "trip: 2 of 3 stops done; next Faro"
PACK trip 1
IMPORT default_banner 1
Banner.undone getter: sort_by([{'name': s.name, 'k': s.eid} for s in instances('Stop') if s.done != True], 'k')
Banner.text getter: 'trip: ' + str(len([s for s in instances('Stop') if s.done == True])) + ' of ' + str(len(instances('Stop'))) + ' stops done; next ' + str((pluck(this.undone, 'name') or ['none'])[0])
```

Write ONE pack that defines the banner's `Banner.text`. To add to it later, write a new pack that `IMPORT`s that one (a layer overrides only what it imports) instead of stacking
independent packs on `Banner.text`: a second pack that defines it without importing the first does not load, and the banner stays as it was. After writing, read the reply's first line
(`pack <name> loaded`) or `Pack:<name>.loaded` and `.error`: a banner you believe you changed but whose pack did not load still shows the old text.

Facts the person gives you (a route, a name, a time) are saved as entities of a class you choose, and the banner keeps a short index line saying they exist and where. When a search by name finds nothing,
search by class: `entity_query {q: "kind:<Class>"}`.

A banner of several parts is one getter that joins them, in the order you choose:

```c1f
-- given Plan:now {"doing": "importing the ledger", "next": "check the totals"}
-- given Banner:admin:main {}
-- reads Banner:admin:main.text = "doing: importing the ledger\nnext: check the totals"
PACK plan 1
IMPORT default_banner 1
Banner.doing getter: [p.doing for p in instances('Plan')]
Banner.upnext getter: [p.next for p in instances('Plan')]
Banner.text getter: join(['doing: ' + str((this.doing or [''])[0]), 'next: ' + str((this.upnext or [''])[0])], '\n')
```

## More than one banner

A feature, a script or you can add a line to the banner without touching your main banner: write `words` on an entity of your own,
`Banner:<name>` (for example `Banner:history`). A turn shows your main banner first, then every other banner's `words`, the oldest update first, so what
has not changed stays in front. Edit `words` to change a line, write `""` to take it away.

The seat has one such part of its own, `Banner:recall`: the lines of your earlier words (and of other texts you hold) that are closest to the message you are answering (and, by shared words, to the few chat texts just before it, each step back counting half as much), by shared words, by meaning and by the texts that moved the same things in the field, ranked by a vote of those finders; it is the `recall` of that message's body, the same list a note of yours reads,
each with where it came from and when. It is context, not instructions: a line from a page or a file is marked as not the person's words. Read it with
`entity_read {spec: "Banner:recall", prop: "words"}`. Like the main banner it is yours to edit: its `words` is a getter, so replace it as you would the main banner, by writing a class of your own that
defines `words` and putting that class in the part's `parents`. Its size and depth are the dials of the entity `recall:dials` (`banner_n`, `pool`, `fact_chars`); the weights of the vote are getters on the message's body (`ml_weight`, `ent_weight`, `r_cos_weight`, `r_edge_weight`, `vote_depth`) that a layer of yours can override; a turn waits for the embedding of its own message before it starts, and starts at once when the part is gone or the embedding is not available (a server that does not answer holds the turn for at most 10 seconds).

## Habits

- Keep the banner short and current: the plan, what is open, what you decided.
- Let a getter read what you would otherwise re-read each turn (a file's size, a command's status, a count).
- Replace your pack by writing `Pack:<name>.text` again; the next turn shows the change.
