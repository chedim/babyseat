# babyseat in Home Assistant

*Slices 1 and 2 of the move to cliffvm (#1143, plan: [hass-plan.md](hass-plan.md)), component version 0.7.60. This page describes the component as it ships now.*

The **babyseat (membrane)** integration (HACS; the domain is `membrane`) is a **thin client of a seat you already run**. It gives Home Assistant three things: the seat as an **Assist conversation agent**, a few **entities and services** for the seat, and the seat's **use of the house** (the model reads states and calls services through Home Assistant, with no Home Assistant secret on the seat). It connects to the seat's address with a token. It does **not** start a seat inside Home Assistant and it installs no package (its manifest has `requirements: []`).

**Important first: the seat's use of the house, the sensors and the model select need the OWNER's token** (the sub `owner`, or the one your seat's `operator` fact names). Assist works with any token of the seat. With a token of another person the calls the model writes stay `pending`, the sensors and the select's write are missing, and nothing else breaks (below, "Which token").

## What works, and what does not yet

| | now |
|---|---|
| Assist: the seat answers Home Assistant's conversations (one seat session per HA conversation, the speaker's name sent as a first line `[HA user: <name>]`) | works |
| Setup form: seat URL + token; a health check at setup; a token that ran out asks for a new one (reauth) | works |
| The seat uses the house: the model reads an entity's state and calls services through Home Assistant (`HaCall`), under an allow-list the owner writes (default deny) and what the person exposed to Assist | works (owner token) |
| Entities: the seat reachable, a turn running, the model, turns today, last answer, last error; a select of the seat's model | works (owner token) |
| Services: `membrane.send_message`, `membrane.ask` | works |
| MQTT in the field, a live mirror of Home Assistant's states into the field (watches that fire on a state change), the thinker/loops/field sensors of the old component | not built (the sensors have no source on cliffvm and are gone) |
| The seat as Home Assistant's child process ("Inside Home Assistant") | gone: run the seat yourself (below); existing "inside" entries ask for a seat address and token once |
| The sidebar panels, membrane home as landing page, the chat and field map inside HA, the per-user relay, speech input, several seats (the zoo) | later (slice 3, after the page rebuild) |

## Install

1. HACS -> the three dots -> *Custom repositories* -> `https://github.com/chedim/membrane`, category *Integration*. Install **babyseat (membrane)** and restart Home Assistant.
2. Run a seat (next section) and mint a token for Home Assistant.
3. *Settings -> Devices & services -> Add integration -> babyseat (membrane)*: the seat's URL and the token.
4. *Settings -> Voice assistants*: pick the **Membrane seat** conversation agent for an assistant.

## Run a seat

The seat is `cliffvm` (docs/cliffvm.md): `scripts/seat-full` starts the whole set of packs from a checkout (`scripts/seat-full --data-dir <dir> --port 4096`, once `cd native && cargo build --release --no-default-features --features bin` has built the binary). The turn is answered through the `claude` CLI with an Anthropic API key in the seat's environment (`ANTHROPIC_API_KEY`; docs/auth.md, "The claude turn's credential").

**The seat listens on 127.0.0.1 by default.** Home Assistant on the same machine reaches it as it is. A seat on another machine must be started with `cliffvm run ... --bind <address>` (for example `--bind 0.0.0.0` on a network you trust: every route needs a token, but the port is then open to the network); `scripts/seat-full` has no `--bind` option yet.

## Mint a token, paste it

Every route of a seat with a door secret needs a token, Home Assistant's calls too (docs/auth.md). On the seat's machine:

```
cliffvm token mint --sub owner --name "Home Assistant" --ttl 30d --secret-file <seat data dir>/door.secret
```

It prints the token (the life, `--ttl`, is required and yours to choose; `12h`, `30d` or seconds). Paste it in the form's *Token* field: the component sends it as the Basic password (`Authorization: Basic opencode:<token>`), which the seat reads as the token. The first request makes that person on the seat; Assist's messages carry the speaker line `[HA user: <name>]` of the Home Assistant user who spoke, whoever the token is.

## Which token

The routes the seat's use of the house runs on (`/membrane/ha/*`) and the model select's write (`POST /membrane/options`) are **the owner's** (docs/auth.md: the owner is the person whose sub is the seat's `operator` fact, else `owner`, the sub of the token the seat writes at boot). So the token you give Home Assistant for these is the owner's: mint it with `--sub owner` as above. That is a decision to make knowingly: it is the same power as the owner's own token (the model's tools, the packs). A token of another sub still gives Assist; then the pull answers `403`, the component keeps asking quietly (the calls stay `pending` on the seat), and the sensors and the select's write are absent. Mint a token with the owner's sub when you want the rest.

## When the token runs out (reauth)

Home Assistant polls the seat's `GET /global/health` every 60 seconds with the token. When the seat answers `401` (the token ran out, or the seat's secret changed), Home Assistant shows the integration as needing attention (*Settings -> Devices & services -> Membrane -> Reconfigure*): mint a new token as above and paste it. A turn that meets the `401` first tells the person in Assist, in words, to do the same. A seat that is down is a retry, not a reauth.

An entry made by the old component: a connect entry keeps its address, and its old seat token is asked for again if the seat is a cliffvm seat (the old tokens are not JWTs); an entry that ran the seat inside Home Assistant has no seat to connect to and asks for an address and a token.

## The seat uses the house

The model writes `HaCall:<id>.request = {domain, service, data, target}` (a service call) or `{read: <entity_id>}` or `{read: "catalog"}`, as it writes any entity. The component, which already talks to the seat, **pulls** the pending calls every 2 seconds (`GET /membrane/ha/calls`), takes one (`POST /membrane/ha/calls {id, claim: true}`: the seat marks it `running`, so two pullers never run one call), runs it with Home Assistant's own `hass.services.async_call` / `hass.states`, and answers it (`POST /membrane/ha/calls {id, result: {ok, response | error}}`): the call is `done` or `error`, and the model reads `HaCall:<id>.state`, `.response` and `.ok` like a driver's receipt (a failed call reads as an Error with the reason). A call the component took and never answered (Home Assistant restarted in between) stays `running` for ever: it may have run, so it is not offered again; the model asks again under a new id.

**No Home Assistant secret is on the seat, and none of the seat's is in Home Assistant but the token you pasted.** There is no `ha_base_url` and no token pushed to the seat; what runs is Home Assistant's own service call, inside Home Assistant. Home Assistant is NOT a person of the seat: the component keeps signing in with the owner's token.

**Every call runs as a Home Assistant user, "Membrane".** At setup the component finds or creates the hidden Home Assistant **system user** `Membrane` (the old component's own: `system_generated`, non-admin, `local_only`, group **Users** = `system-users`; it has no login and no token, and shows in no People list) and runs every service call with `Context(user_id=<its id>)`. Home Assistant's own permission model therefore decides what the model may touch, and the model cannot write that policy (it lives in Home Assistant, not on the seat). The user is found by the id kept in the config entry (`ha_user_id`, written at the first setup; an entry without it finds the user by its name), made again if someone deleted it (at the next call), and removed when the integration is removed. A call Home Assistant refuses (`Unauthorized`) is an `error` receipt on the seat with the reason (who was refused what: `Home Assistant refused light.turn_on for its Membrane user (unauthorized: entities control on light.kitchen)`), never silent. What the group allows: Home Assistant's built-in **Users** group gives control of **all entities** (`entities: all`) and none of the admin-only services (user, config and integration management, and the like). Read-only reads (`{read: ...}`) are plain state reads and are not run as the user. To narrow it, change nothing in the seat: this is Home Assistant's policy to grow or limit.

**Three lines hold what the model may do** (1 and 2 below, and Home Assistant's permissions for the Membrane user above):

1. **The seat's allow-list**, `HaAllow:<name>` entities (`services`: `light.turn_on`, `light.*`, `states.read`; `entities`: `light.kitchen`, `light.*`), written by the owner. **Default deny**: with no entry every call is an `error` at once and the component never sees it. One entry must list the call's service and every entity it names (in `target.entity_id` or `data.entity_id`); a target by device, area, floor or label is denied (it expands to entities the list cannot check). The catalog refresh needs no entry. The seat's own tool for writing is the owner's, so the model can write an entry too; the seat has no security inside it by decision (docs/c1f-layers.md, "Privilege"), the entry is journalled and visible. Home Assistant's own line below is the one the model cannot write.
2. **What the person exposed to Assist** (Settings -> Voice assistants -> Expose). The component refuses to read, or to name in a call, an entity that is not exposed, whatever the seat allowed, and the catalog lists only exposed entities.

`HaCatalog:ha` is the seat's copy of what Home Assistant exposes (`entities`, every `services` row `{id, target, fields}`), pushed at setup and again when it changed (checked every minute) or when the model asks (`{read: "catalog"}`). Every call, claim and receipt is a write of an entity, so the seat's journal holds them and a restart replays them as they were and runs nothing. `Help:ha` on the seat is the model's page for all of this (every example in it is run by a test).

## Entities

Made from what the seat really knows (`GET /membrane/ha/metrics`, rows built by the seat's `ha` pack from its own turns; `GET /membrane/options`): `binary_sensor` *Reachable* (the seat answers its health), *Busy* (a turn is running); `sensor` *Model*, *Turns today*, *Last answer* (a time), *Last error* (the cause of the last failed turn, with the time of it as an attribute; a state is cut at Home Assistant's own 255 characters); `select` *Model*: the models the seat lists, the one it runs on, and choosing one writes `POST /membrane/options {loops: <model>}` (the seat's pick for the chat, the page's own post). The old component's 17 metric entities and its seat select are removed from the entity registry when an entry is upgraded (`async_migrate_entry`, 1.4): 13 of them read the efield, the thinker and the loops, which cliffvm does not have, and none continues. The list of rows is the seat's, so it changes with the seat, not with this component; a row added later appears after a reload of the integration.

## Services

Described in `services.yaml`:

- `membrane.send_message {text, session?}`: hand the seat a message and return at once (the seat answers in its own time, in its chat).
- `membrane.ask {text, session?}`: the answer comes back as the service's response data, `{answer, session}` (use it with *Perform action* and a response variable); a turn can take minutes.

`session` is a name (default `service`): the same name continues the same seat session (titled `hass-<name>`, like Assist's `hass-<conversation id>`). The caller's Home Assistant user, when there is one, is the speaker line. An error says why (`The seat could not answer: ...`); a refused token starts the reauth.

## Errors in Assist

A failed turn is spoken as an error with the seat's own cause, for example `The seat could not answer: claude failed (rc 3): ...`; an unreachable seat says so; a refused token starts the reauth.

## Where things are

The entry is `{serve_url, serve_user, serve_pass}`; `serve_pass` holds the token. The component has no files of its own on disk and starts no process (one background task per entry pulls the seat's calls). The old component's seat directory `<config>/membrane/` (data of a seat it started) is left alone; the old field in it is the legacy import's input when you start a new seat (docs/migration-design.md).

## Tests

`pytest tests/ha` runs the component against a fake Home Assistant (tests/ha/fakeha.py) and, where a `cliffvm` binary is built (`cd native && cargo build --features bin`), against a real seat with a fake `claude`. `native/tests/session_1143.rs` and `native/tests/ha_1143.rs` pin the routes the component calls (the calls, the owner's gate, the allow-list, the journal replay).
