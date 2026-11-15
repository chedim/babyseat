# babyseat in Home Assistant

Status: describes the component in `custom_components/membrane/` as it ships in this release (its version in `manifest.json` is the package's; the domain stays `membrane`). Tests: `pytest tests/ha` (a fake Home Assistant and a real private seat with a fake `claude`),
`native/tests/ha_1143.rs` and `native/tests/session_1143.rs` (the routes the component calls).

The **babyseat (membrane)** integration (HACS) is a **thin client of a seat that runs outside Home Assistant**. It gives Home Assistant three things: the seat as an **Assist conversation agent**, a few **entities and services** for the seat, and the seat's
**use of the house** (the model reads states and calls services through Home Assistant, with no Home Assistant secret on the seat). It connects to the seat's address with a token. It does **not** start a seat inside Home Assistant and it installs
no package (`requirements: []`). Where the seat runs:

- **Home Assistant OS or Supervised**: the **babyseat add-on** runs the seat under the Supervisor and announces it to Home Assistant (discovery, below): no address or token to type.
- **Home Assistant Container or Core**: there is no add-on; run `cliffvm serve` on a machine Home Assistant can reach (the same machine, or one on your network) and give the integration its address and token.

## What it does

| | |
|---|---|
| Assist: the seat answers Home Assistant's conversations (one seat session per HA conversation; the speaker's name goes as a first line `[HA user: <name>]`) | works |
| Setup: found by the add-on's discovery, or a form with the seat URL + token; a health check at setup; a token that ran out asks for a new one (reauth) | works |
| The seat uses the house: the model reads an entity's state and calls services through Home Assistant (`HaCall`), under an allow-list (default deny) and what the person exposed to Assist | works |
| Entities: the seat reachable, a turn running, the model, turns today, last answer, last error; a select of the seat's model | works |
| Services: `membrane.send_message`, `membrane.ask` | works |

One entry per Home Assistant: one seat.

## Install

1. HACS -> the three dots -> *Custom repositories* -> `https://github.com/chedim/membrane`, category *Integration*. Install **babyseat (membrane)** and restart Home Assistant.
2. Run a seat:
   - **OS / Supervised**: install the babyseat add-on (its own page says how) and start it. *Settings -> Devices & services* shows **babyseat (membrane)** as *Discovered*: *Add*, then *Submit*. Done: skip step 3.
   - **Container / Core**: run `cliffvm serve` where Home Assistant can reach it (next section) and read its admin token (*The token*, below).
3. *Settings -> Devices & services -> Add integration -> babyseat (membrane)*: the seat's URL (for example `http://192.168.1.20:4096`) and the token.
4. *Settings -> Voice assistants*: pick the **Membrane seat** conversation agent for an assistant.

## Run a seat (Container / Core)

The seat is `cliffvm serve` (docs/cliffvm.md; the README has the install and the quick start). The turn is answered through the `claude` CLI with an Anthropic API key in the seat's environment (`ANTHROPIC_API_KEY`; docs/auth.md).

**The seat listens on 127.0.0.1 by default.** Home Assistant on the same machine reaches it as it is. A seat on another machine must be started with `--bind <address>` (for example `cliffvm serve --bind 0.0.0.0` on a network you trust:
every route still needs a token, but the port is open to the network). `scripts/seat` (the dev wrapper) passes every `cliffvm serve` option through, `--bind` included.

## The add-on's discovery

The add-on announces its seat through the Supervisor as the service `membrane` with the config `{host, port, token}`: `host` is the add-on's name on the Supervisor's network, `port` the seat's port (4096 when it names none), `token` the seat's admin token.
Home Assistant hands it to the component's `async_step_hassio` (`config_flow.py`), which:

- builds `http://<host>:<port>` and keys the entry by `<host>:<port>` (the unique id);
- **when no entry exists**: checks the seat's health with the token (as the form does; a refused token aborts with *invalid_auth*, a seat that does not answer with *cannot_connect*) and asks you to confirm (Home Assistant's rule: a discovery never makes an entry by itself); the entry is `{serve_url, serve_user, serve_pass}` like the form's;
- **when the entry of this seat exists** (the same unique id, an entry typed by hand with the same address, or an entry that ran the seat inside Home Assistant and has no address yet): stores the announced address and token and sets the entry up again, with no step of yours. The add-on announces at every start, so a token the seat refused is repaired the next time the add-on starts; an announcement that changes nothing does nothing;
- **when the entry names another seat**: leaves it alone (one entry per Home Assistant).

**The Ollama hint.** When Home Assistant has its own Ollama integration, the discovery's confirmation and the manual form say which server and model it uses (`ha_ollama` in `config_flow.py`: the first `ollama` entry with a `url`; the model of its conversation subentry, else the entry's `model`). This tells you how to make the seat use the same server: set the add-on's Ollama URL and model options to them, or start `cliffvm serve` with `--fact seat_backend=<url>` and pick the model in the seat's model select. It is only a hint: the component reads Home Assistant's entries, makes no network call, configures nothing and writes nothing to the seat. Without an Ollama entry the forms are the plain ones (the hint has its own step ids, `hassio_confirm_ollama` and `user_ollama`).

An announcement without a host or a token aborts (*invalid_discovery*). How the add-on runs the seat is the add-on's own page; this is its whole contract with the component.

## The token

Every route of the seat needs a token, Home Assistant's calls too (docs/auth.md). The simplest token is the one the seat writes at every start, `<data dir>/admin.token` (the path is printed at start). Or mint one on the seat's machine:

```
cliffvm token mint --sub ha --name "Home Assistant" --ttl 30d --secret-file <data dir>/door.secret
```

It prints the token (`--ttl` is required and yours to choose; `12h`, `30d` or seconds). Paste it in the form's *Token* field: the component sends it as the Basic password (`Authorization: Basic opencode:<token>`), which the seat reads as the token.
There are no users: every valid token is the admin, so any token of the seat gives Assist, the seat's use of the house, the sensors and the model select alike. Treat the token as the seat's key.

## When the token runs out (reauth)

Home Assistant polls the seat's `GET /global/health` every 60 seconds with the token. When the seat answers `401` (the token ran out, or the seat's secret changed), Home Assistant shows the integration as needing attention
(*Settings -> Devices & services -> Membrane -> Reconfigure*): paste a new token. A turn that meets the `401` first tells the person in Assist, in words, to do the same. A seat that is down is a retry, not a reauth.

The reauth form says where the new token is: `<data dir>/admin.token` (new at every start of the seat), a minted one, or, for the add-on, the add-on's log (and the add-on's discovery stores it by itself at its next start). An entry that ran the seat inside Home Assistant gets its own reauth form, with the address too (*Upgrading from 0.7.53*, below).

## Upgrading from 0.7.53

**The seat no longer runs inside Home Assistant.** 0.7.53's "Inside Home Assistant" mode ran the Python seat as Home Assistant's child process; that seat is gone, and the component only connects. HACS updates the component; at the first start the entry is migrated (`async_migrate_entry`):

- **An entry that ran the seat inside Home Assistant** has no seat to connect to: it is kept with an empty address and token, and Home Assistant shows it as needing attention. The form says what happened and what to do: on **Home Assistant OS or Supervised**, install the **babyseat add-on** and start it (its discovery fills the entry in by itself, no form to fill); on **Container or Core**, run `cliffvm serve` on a machine Home Assistant can reach and enter its address and token in the form.
- **An entry that connected to a seat** keeps its address, but **the old seat's tokens are not valid on the new seat** (the new seat has its own key): the reauth asks for the new seat's admin token (`<data dir>/admin.token`, the add-on's log, or the add-on's discovery at its next start when the address is the add-on's).

**What carries over**: the **chat history**. A new seat that can read the old seat's folder (`<config>/membrane/`, which the old component leaves alone) imports the old chats by itself, newest first, and offers the model the old seat's name (docs/cliffvm.md, "The history cutover"); `cliffvm serve --legacy <path>` names another folder.
**What does not**: the old field's **entities and goals**, the **settings**, the **model setup** (backend, model, embed model: the new seat answers through the `claude` CLI, docs/auth.md), the **tokens**, and the **`HaAllow` allow-lists** (*The seat uses the house*, below): recreate the allow-list entries on the new seat, since with none every call of the model into the house is denied. The old component's sidebar panel, landing page, relay, speech input, several seats (the zoo) and its 17 metric entities are not in the component (the metric entities are removed from the entity registry at the migration, *Entities* below).

## The seat uses the house

The model writes `HaCall:<id>.request = {domain, service, data, target}` (a service call) or `{read: <entity_id>}` or `{read: "catalog"}`, as it writes any entity. The component, which already talks to the seat, **pulls** the pending calls every 2 seconds (`GET /membrane/ha/calls`), takes one (`POST /membrane/ha/calls {id, claim: true}`: the seat marks it `running`, so two pullers never run one call), runs it with Home Assistant's own `hass.services.async_call` / `hass.states`, and answers it (`POST /membrane/ha/calls {id, result: {ok, response | error}}`): the call is `done` or `error`, and the model reads `HaCall:<id>.state`, `.response` and `.ok` like a driver's receipt (a failed call reads as an Error with the reason). A call the component took and never answered (Home Assistant restarted in between) stays `running` for ever: it may have run, so it is not offered again; the model asks again under a new id.

**No Home Assistant secret is on the seat, and none of the seat's is in Home Assistant but the token you pasted.** There is no `ha_base_url` and no token pushed to the seat; what runs is Home Assistant's own service call, inside Home Assistant. Home Assistant is not a person of the seat: the component signs in with the token you pasted.

**Every call runs as a Home Assistant user, "Membrane".** At setup the component finds or creates the hidden Home Assistant **system user** `Membrane` (the old component's own: `system_generated`, non-admin, `local_only`, group **Users** = `system-users`; it has no login and no token, and shows in no People list) and runs every service call with `Context(user_id=<its id>)`. Home Assistant's own permission model therefore decides what the model may touch, and the model cannot write that policy (it lives in Home Assistant, not on the seat). The user is found by the id kept in the config entry (`ha_user_id`, written at the first setup; an entry without it finds the user by its name), made again if someone deleted it (at the next call), and removed when the integration is removed. A call Home Assistant refuses (`Unauthorized`) is an `error` receipt on the seat with the reason (who was refused what: `Home Assistant refused light.turn_on for its Membrane user (unauthorized: entities control on light.kitchen)`), never silent. What the group allows: Home Assistant's built-in **Users** group gives control of **all entities** (`entities: all`) and none of the admin-only services (user, config and integration management, and the like). Read-only reads (`{read: ...}`) are plain state reads and are not run as the user. To narrow it, change nothing in the seat: this is Home Assistant's policy to grow or limit.

**Three lines hold what the model may do** (1 and 2 below, and Home Assistant's permissions for the Membrane user above):

1. **The seat's allow-list**, `HaAllow:<name>` entities (`services`: `light.turn_on`, `light.*`, `states.read`; `entities`: `light.kitchen`, `light.*`), written by you or the model. **Default deny**: with no entry every call is an `error` at once and the component never sees it. One entry must list the call's service and every entity it names (in `target.entity_id` or `data.entity_id`); a target by device, area, floor or label is denied (it expands to entities the list cannot check). The catalog refresh needs no entry. The model can write an entry too (it is the admin; docs/c1f-layers.md, "Privilege"); the entry is journalled and visible. Home Assistant's own line below is the one the model cannot write.
2. **What the person exposed to Assist** (*Settings -> Voice assistants -> Expose*). The component refuses to read, or to name in a call, an entity that is not exposed, whatever the seat allowed, and the catalog lists only exposed entities.

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

The entry is `{serve_url, serve_user, serve_pass}`; `serve_pass` holds the token; its unique id is `membrane` (made by the form) or `<host>:<port>` (made or repaired by the add-on's discovery). The component has no files of its own on disk and starts no process (one background task per entry pulls the seat's calls). The old component's seat directory `<config>/membrane/` (data of a seat it started) is left alone; a new seat finds its chat history by itself (`<data>/.membrane`, `/config/membrane/.membrane`, or `cliffvm serve --legacy <path>`; docs/cliffvm.md, "The history cutover").

## Tests

`pytest tests/ha` runs the component against a fake Home Assistant (tests/ha/fakeha.py; the discovery: `test_discovery.py`, the two reauth forms: `test_auth.py`) and, where a `cliffvm` binary is built (`cd native && cargo build --release --no-default-features --features bin`; or `CLIFFVM_BIN=`), against a real seat with a fake `claude`. `native/tests/session_1143.rs` and `native/tests/ha_1143.rs` pin the routes the component calls (the calls, the allow-list, the journal replay).
