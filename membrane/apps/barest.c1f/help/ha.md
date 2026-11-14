# Help:ha - the house, through Home Assistant

When your seat is connected to Home Assistant, the house is reachable as entities. You hold no Home Assistant secret. You write a call; the Home Assistant integration picks it up,
runs it with Home Assistant's own services, and writes the receipt where you read it. As with `Command` and `Fetch`, acting is a write and the receipt is a read. Every example below is run by a test.
A seat that has no `HaCatalog:ha` entity (`entity_query` `kind:HaCatalog`) is not connected yet: the calls wait for the integration and nothing runs.

## What Home Assistant exposes: `HaCatalog:ha`

The integration keeps this entity current with what the person exposed to Home Assistant's voice assistants: `entities` (`entity_id`, `name`), `services` (`id` like `light.turn_on`, `target`: whether it takes entities,
`fields`: the names of its data), and the readers `entity_ids`, `service_ids` and `domains`.

```c1f
-- given HaCatalog:ha {"entities": [{"entity_id": "light.kitchen", "name": "Kitchen"}, {"entity_id": "sensor.temp", "name": "Temperature"}], "services": [{"id": "light.turn_on", "target": true, "fields": ["brightness_pct"]}]}
-- reads HaCatalog:ha.domains = ["light", "sensor"]
-- reads HaCatalog:ha.entity_ids = ["light.kitchen", "sensor.temp"]
-- reads HaCatalog:ha.service_ids = ["light.turn_on"]
```

## What you may do: `HaAllow:<name>` (every call is denied until the owner allows it)

An entry lists `services` and `entities`. A call runs when ONE entry lists its service (`light.turn_on`, or `light.*` for every service of a domain) AND every entity the call names (`light.kitchen`, or `light.*`).
A read of a state is the service word `states.read`. A service that takes no entity (a notification) needs only its service. A call names its entities in `target.entity_id` or in `data.entity_id`, a text or a list.
A target by device, area, floor or label is denied: name the entity ids (the catalog lists them). Refreshing the catalog (`{read: 'catalog'}`) needs no entry.
Ask the owner to write the entry; an entry you write yourself stands in the journal for the owner to see.

## Call a service

`HaCall:<id>.request = {domain, service, data, target}` is ONE write; the id is yours (a new id is a new call). Read `state` back: `pending` (offered to the integration), `running` (it took the call and may have run it),
`done` or `error`. A denied call is an `error` at once, and its reason names the entry to ask for.

```c1f
-- given HaAllow:lights {"services": ["light.*"], "entities": ["light.kitchen"]}
-- given HaCall:a {}
-- given HaCall:b {}
-- write HaCall:a.request = {"domain": "light", "service": "turn_on", "data": {"brightness_pct": 40}, "target": {"entity_id": "light.kitchen"}}
-- reads HaCall:a.state = "pending"
-- write HaCall:b.request = {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.hall"}}
-- reads HaCall:b.state = "error"
```

## Read the receipt

The integration takes the call (`running`) and then answers it. `response` is what Home Assistant returned (a service that returns data, or the state of a read) and `ok` is true. When the call failed, `response` and `ok`
read as Errors that say why (`x is Error`, `why(x)`), as a `Command` or a `SqlQuery` does. A call that stays `running` was taken and not answered: it may have run, so ask again under a new id after you looked at the house.

```c1f
-- given HaAllow:lights {"services": ["light.*", "states.read"], "entities": ["light.*"]}
-- given HaCall:a {}
-- write HaCall:a.request = {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.kitchen"}}
-- write HaCall:a.claim = {"id": "t1", "by": "ha"}
-- reads HaCall:a.state = "running"
-- write HaCall:a.finish = {"id": "t2", "ok": true, "response": {"changed": ["light.kitchen"]}}
-- reads HaCall:a.state = "done"
-- reads HaCall:a.ok = true
-- reads HaCall:a.response = {"changed": ["light.kitchen"]}
```

## Read a state, refresh the catalog

`{read: 'sensor.temp'}` asks for one entity's state (the answer carries `state` and `attributes`; it needs `states.read` and the entity on an entry). `{read: 'catalog'}` asks the integration to send the catalog again.

```c1f
-- given HaAllow:sensors {"services": ["states.read"], "entities": ["sensor.*"]}
-- given HaCall:t {}
-- given HaCall:c {}
-- write HaCall:t.request = {"read": "sensor.temp"}
-- reads HaCall:t.state = "pending"
-- write HaCall:c.request = {"read": "catalog"}
-- reads HaCall:c.state = "pending"
```

## The seat's sensors

The integration shows the seat in Home Assistant: whether a turn is running, the model, the turns today, the time of the last answer and the last error. They come from the same turns you see in the chat.
