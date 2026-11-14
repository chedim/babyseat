# Help:settings - the settings of the seat

Every setting a person may look at or change is an entity, `Setting:<group>.<key>`. `entity_query` `kind:Setting` lists them all. The groups, in the order a window shows them: `model`, `chat`, `map`, `home`, `import`, `access`, `limits`.
A row is data you read: `label`, `help` (one sentence), `kind`, `live`, `order`, and for a number `min`, `max`, `whole` and `unit`; `default`; `how` (what a row that is only shown says about changing it).
`current` is the value in force right now. It reads the one owner of the value, so it never drifts: the value written to the row, else the launch fact the row names (`fact`), else `default`.

## kind and live

- `text`, `number`, `list` and `allowlist` rows are changed by writing. `readonly` rows are shown, and say in `how` what changes them.
- `live` is `live` when a write is in force at once (the next message, the next read of the map) and `restart` when the value is fixed when the seat starts. A `restart` row is always `readonly`.

## Change a setting: write `set`

`Setting:<group>.<key>.set = <value>` is the one door. A good value is written to its owner, `applied` says when, and `refused` is empty. A value that does not fit is refused with the reason, in `refused`, and nothing else is written: a number outside its range is never rounded into it.
Everyone with access is the owner, so the write is the same for everyone; when the seat itself turns a write down (for the owner only), the reply holds the reason as text.

```c1f
-- given Setting:chat.timeout {"label": "Answer time limit", "kind": "number", "live": "live", "default": 21600, "min": 1, "max": 604800}
-- reads Setting:chat.timeout.current = 21600
-- write Setting:chat.timeout.set = 0
-- reads Setting:chat.timeout.refused = "Answer time limit must be at least 1 (0 is below it)"
-- reads Setting:chat.timeout.current = 21600
-- write Setting:chat.timeout.set = 90
-- reads Setting:chat.timeout.current = 90
-- reads Setting:chat.timeout.refused = ""
```

A whole number is asked for where it says `whole`, and a list holds names that are not empty (an empty name would match everything):

```c1f
-- given Setting:chat.max_steps {"label": "Most steps per answer", "kind": "number", "live": "live", "default": 2048, "min": 1, "whole": true}
-- write Setting:chat.max_steps.set = 2.5
-- reads Setting:chat.max_steps.refused = "Most steps per answer must be a whole number, not 2.5"
-- reads Setting:chat.max_steps.current = 2048
-- given Setting:map.hidden {"label": "Hidden kinds", "kind": "list", "live": "live", "default": ["Fetch"]}
-- write Setting:map.hidden.set = ["Fetch", ""]
-- reads Setting:map.hidden.refused = "Hidden kinds must not hold an empty or non-text name (an empty name matches everything)"
-- write Setting:map.hidden.set = ["Fetch", "Span"]
-- reads Setting:map.hidden.current = ["Fetch", "Span"]
```

## What is live

- `chat.max_steps`, `chat.timeout`: the next message runs with them; a message that is running keeps what it started with.
- `map.hidden`, `map.hidden_prefix`: the next read of the map. `map.window` (how many of the most recently active things it shows) and `map.poll_ms`: the next time the page asks for its settings; a read of the map that names no size uses `map.window`.
- `model.chat`: the model that answers a message with no pick of its own, the next message. An empty name leaves the pick to the seat's fallback.
- `home.allow`: the list of allowed actions, written as a whole: `[{name, services, entities}]`. An entry the new list leaves out is removed. With no entry nothing is allowed.

## What is shown only

`model.cloud`, `model.signin`, `model.cli`, the `import.*` rows (the other databases show their mode: `ro` reads only, `rw` lets the model change the file, so a person sets it by writing that source's own entity), and the `access.*` and `limits.*` rows are `readonly`.
No setting holds or shows a secret. A key or a token appears only as the NAME of its environment variable, or as the path of a file; `model.signin` says `api_key` or `login`.
