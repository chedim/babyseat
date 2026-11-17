# babyseat in Home Assistant

Status: describes the component in `custom_components/membrane/` as it ships in this release (its version in `manifest.json` is the package's; the domain stays `membrane`). Tests: `pytest tests/ha` (a fake Home Assistant, a fake seat binary, and a real private seat with a fake `claude`),
`seat-tests/tests/ha_1143.rs` and `seat-tests/tests/session_1143.rs` (the routes the component calls), and `scripts/ha_core_smoke.py` (a real Home Assistant Core, by hand).

The **babyseat (membrane)** integration (HACS) gives Home Assistant a seat. By default it **runs the seat itself, inside Home Assistant** (operator 2026-11-15): it downloads the seat of its own release, checks it, starts it on
this machine and keeps it running. Or it **connects to a seat you run** elsewhere, with its address and a token. Either way Home Assistant gets three things: the seat as an **Assist conversation agent**, a few **entities and services** for the seat,
and the seat's **use of the house** (the model reads states and calls services through Home Assistant, with no Home Assistant secret on the seat). It installs no pip package (`requirements: []`).

## What it does

| | |
|---|---|
| Setup, the default: *Run a seat inside Home Assistant*: the provider (Anthropic, an OpenAI-compatible endpoint, Ollama) and its key, model or URL; the seat is downloaded, checked, started and supervised by the component | works (checked on a real Home Assistant Core on macOS; see *What is not verified*) |
| Setup, the other way: *Connect to a seat I run*: the seat URL + token, or the add-on's discovery; a health check at setup; a token that ran out asks for a new one (reauth) | works |
| Assist: the seat answers Home Assistant's conversations (one seat session per HA conversation; the speaker's name goes as a first line `[HA user: <name>]`) | works |
| The seat uses the house: the model reads an entity's state and calls services through Home Assistant (`HaCall`), under an allow-list (default deny) and what the person exposed to Assist | works |
| Entities: the seat reachable, a turn running, the model, turns today, last answer, last error; a select of the seat's model | works |
| Services: `membrane.send_message`, `membrane.ask` | works |

One entry per Home Assistant: one seat.

## Install

1. HACS -> the three dots -> *Custom repositories* -> `https://github.com/chedim/membrane`, category *Integration*. Install **babyseat (membrane)** and restart Home Assistant.
2. *Settings -> Devices & services -> Add integration -> babyseat (membrane)*. The first step asks where the seat runs:
   - **Run a seat inside Home Assistant** (the default, next section): pick the provider, give its key, model or URL, *Submit*. Done.
   - **Connect to a seat I run** (*Connect to a seat I run*, below): the seat's URL and its token.
3. *Settings -> Voice assistants*: pick the **Membrane seat** conversation agent for an assistant.

## Run a seat inside Home Assistant

The component runs the native seat (`cliffvm serve`) as a child process of Home Assistant, on every Home Assistant flavour (OS, Supervised, Container, Core) whose machine is one of the release's targets (`seat_runner.py`).

**The provider** (the form, then *Configure* to change it later; `seat_options.py`, the same mapping as the add-on's `addon/options.sh`):

| Provider | Fields | What the seat is started with |
|---|---|---|
| **Anthropic** (the default) | Anthropic API key (required; from the Claude Console, scoped to a workspace), model (default `claude-sonnet-5-5`) | `--fact cloud_models=[{name: <model>, backend: anthropic, base_url: https://api.anthropic.com, api_key_env: ANTHROPIC_API_KEY}]` (Anthropic's OpenAI-compatible endpoint) and `--fact minds=...` (every mind on the model); the key in the seat's environment as `ANTHROPIC_API_KEY` |
| **OpenAI-compatible** | endpoint address (its root: the seat adds `/v1/chat/completions`), model (required), API key (optional) | `--fact cloud_models=[{name, backend: openai, base_url, api_key_env: OPENAI_API_KEY}]` + `minds`; the key as `OPENAI_API_KEY` |
| **Ollama** | Ollama URL, model (both prefilled from Home Assistant's own Ollama integration when it has one: its server and its conversation agent's model) | `--fact seat_backend=<url>` + `minds`; no key |

Every form also takes an optional seat name (`--fact seat_name`; without one the seat asks for its name, or takes the 0.7 seat's). The form checks what it can without the network: a key that is not empty, an `http://` or `https://` URL, a model.
A key is never on the seat's command line (any process of the host can read that), never in a log, and never shown back in a form: it is kept in the config entry (Home Assistant's `.storage`, as every integration's key is) and handed to
the seat in its environment. The seat's environment is its own: `PATH`, `LANG`, `LC_ALL`, `TZ`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `HOME=<config>/membrane/home` and the key; never Home Assistant's (Home Assistant OS gives Home Assistant a `SUPERVISOR_TOKEN`; the seat runs model-written commands and must not hold it).
The add-on's `claude_cli` provider is not offered: there is no `claude` CLI inside Home Assistant.

**What it downloads, from where, what it checks.** The tarball of the component's OWN version (`manifest.json`) for this machine: `babyseat-<version>-<target>.tar.gz` and its `.sha256` from `https://github.com/chedim/membrane/releases/download/v<version>/`, the files `scripts/dist/build.sh --archive` makes and the release workflow publishes. The target:

| Host | Target |
|---|---|
| Linux x86_64 | `x86_64-unknown-linux-musl` (static: runs on glibc and musl alike, Home Assistant OS and Container included) |
| Linux aarch64 (a Raspberry Pi 4/5 on a 64-bit OS, ...) | `aarch64-unknown-linux-musl` (static) |
| macOS arm64 / x86_64 (developers) | `aarch64-apple-darwin` / `x86_64-apple-darwin` |
| anything else (32-bit ARM, i686, Windows, ...) | none: the menu's *Run a seat inside Home Assistant* aborts with the reason; connect to a seat on another machine |

As `install.sh` does: the tarball's sha256 is checked against the `.sha256` file BEFORE it is unpacked; it is unpacked (no absolute path, no `..`, no link out of the folder) into a staging folder; its one folder must say the version in `VERSION` and match its own `MANIFEST.sha256` file by file;
only then it is moved to `<config>/membrane/app/<version>/`. A failure leaves nothing (the setup is retried by Home Assistant, the reason shown on the entry). At every start the installed folder is checked against its manifest again (no download); a folder that no longer matches is downloaded again.
A new release of the component downloads its own version's folder; old version folders are left on disk. All of it is off Home Assistant's event loop (aiohttp for the download, the executor for the hashing and the unpacking).

**The process.** `<config>/membrane/app/<version>/bin/cliffvm serve --port <port> --bind 127.0.0.1 --data-dir <config>/membrane --fact ...`, its working folder `<config>/membrane`. The port is kept in the entry (`serve_port`): 4096 when it was free at the setup, else a free one; a port taken at a later start is replaced by a free one (and kept).
The seat is ready when `GET /global/health` answers with the admin token it writes at its start (`<config>/membrane/admin.token`), within 180 seconds; the component then talks to `http://127.0.0.1:<port>` with that token, and mints a fresh one (`cliffvm token mint`, 30 days) every day so a seat that runs for months never holds an expired one.
The seat's output goes to Home Assistant's log as `membrane seat: <line>` (stdout at debug, stderr at warning; anything that looks like a key or a token is masked). When the seat exits it is started again after 5 s, doubling up to 300 s for each exit that came within 300 s of its start;
after 5 such exits in a row it is not restarted, and a **repair** (*Settings -> Repairs*) shows its last words: fix the key, model or URL (*Configure*), then reload the integration. Unloading or reloading the entry, and Home Assistant's stop, send the seat SIGTERM (and SIGKILL after 30 s) and wait for it. A seat left running by a Home Assistant that died
(its pid in `<config>/membrane/seat.pid`, its command line naming this data folder) is stopped before a new one starts, so two seats never share the store.

**Where its data is.** `<config>/membrane/`: `seat.sqlite` (the store), `door.secret`, `admin.token`, `home/` (its HOME), `app/<version>/` (the seat itself), `seat.pid`. The 0.7.53 component's seat kept its store in `<config>/membrane/.membrane/`: the new seat finds it there by itself (*Upgrading from 0.7.53*).

**Change the model.** *Settings -> Devices & services -> babyseat (membrane) -> Configure*: the provider, then its fields (an empty key keeps the stored one). The seat restarts with them. The seat itself keeps its pick in its store and takes the `minds` fact only at its first start, and its options route (what the model select posts) takes only an Ollama model or a claude rung; so at every start, when the seat runs on another model than the entry's, the component writes the entry's model to every mind (`Minds:seat` `loops`, `cortex`, `workers`, `seat_default`, with `entity_write` on the seat's `/mcp`, as the `minds` fact would). The seat's own model select (*Entities*) picks among the models the seat lists without a restart, until the next start (it does not list an Anthropic or OpenAI-compatible model).

## Connect to a seat I run

The seat is `cliffvm serve` (docs/cliffvm.md; the README has the install and the quick start) on a machine Home Assistant can reach (the same machine, or one on your network). Give the form its URL (for example `http://192.168.1.20:4096`) and its admin token (*The token*, below).

**The seat listens on 127.0.0.1 by default.** Home Assistant on the same machine reaches it as it is. A seat on another machine must be started with `--bind <address>` (for example `cliffvm serve --bind 0.0.0.0` on a network you trust:
every route still needs a token, but the port is open to the network). `scripts/seat` (the dev wrapper) passes every `cliffvm serve` option through, `--bind` included.

A Supervisor add-on that runs the seat (`addon/`) exists in the repository; it is not supported, and its discovery (below) is the only part of it the component knows.

## The add-on's discovery

The add-on announces its seat through the Supervisor as the service `membrane` with the config `{host, port, token}`: `host` is the add-on's name on the Supervisor's network, `port` the seat's port (4096 when it names none), `token` the seat's admin token.
Home Assistant hands it to the component's `async_step_hassio` (`config_flow.py`), which:

- builds `http://<host>:<port>` and keys the entry by `<host>:<port>` (the unique id);
- **when no entry exists**: checks the seat's health with the token (as the form does; a refused token aborts with *invalid_auth*, a seat that does not answer with *cannot_connect*) and asks you to confirm (Home Assistant's rule: a discovery never makes an entry by itself); the entry is `{serve_url, serve_user, serve_pass}` like the form's;
- **when the entry of this seat exists** (the same unique id, an entry typed by hand with the same address, or an old entry with no address yet): stores the announced address and token and sets the entry up again, with no step of yours. An announcement that changes nothing does nothing;
- **when the entry runs its own seat inside Home Assistant, or names another seat**: leaves it alone (one entry per Home Assistant).

**The Ollama hint.** When Home Assistant has its own Ollama integration, the discovery's confirmation and the connect form say which server and model it uses (`ha_ollama` in `config_flow.py`: the first `ollama` entry with a `url`; the model of its conversation subentry, else the entry's `model`). The inside path's Ollama form is prefilled with the same two. Reading them makes no network call.

An announcement without a host or a token aborts (*invalid_discovery*).

## The token

Every route of the seat needs a token, Home Assistant's calls too (docs/auth.md). A seat run inside Home Assistant needs nothing from you: the component reads the token the seat writes. For a seat you run, the simplest token is the one the seat writes at every start, `<data dir>/admin.token` (the path is printed at start). Or mint one on the seat's machine:

```
cliffvm token mint --sub ha --name "Home Assistant" --ttl 30d --secret-file <data dir>/door.secret
```

It prints the token (`--ttl` is required and yours to choose; `12h`, `30d` or seconds). Paste it in the form's *Token* field: the component sends it as the Basic password (`Authorization: Basic opencode:<token>`), which the seat reads as the token.
There are no users: every valid token is the admin, so any token of the seat gives Assist, the seat's use of the house, the sensors and the model select alike. Treat the token as the seat's key.

## When the token runs out (reauth)

Home Assistant polls the seat's `GET /global/health` every 60 seconds with the token. When the seat answers `401` (the token ran out, or the seat's secret changed), Home Assistant shows the integration as needing attention
(*Settings -> Devices & services -> Membrane -> Reconfigure*): paste a new token. A turn that meets the `401` first tells the person in Assist, in words, to do the same. A seat that is down is a retry, not a reauth.

The reauth form says where the new token is: `<data dir>/admin.token` (new at every start of the seat), a minted one, or, for the add-on, the add-on's log (and the add-on's discovery stores it by itself at its next start). For a seat run inside Home Assistant (its `door.secret` was changed or removed) the reauth is one *Submit* that restarts the seat: the component reads the token the restarted seat writes.
An old entry with no seat and nothing to run gets its own form (*Upgrading from 0.7.53*).

## Upgrading from 0.7.53

0.7.53's "Inside Home Assistant" mode ran the Python seat as Home Assistant's child process, thinking with an Ollama server. HACS updates the component; at the first start the entry is migrated (`async_migrate_entry`, entry version 1.5):

- **An entry that ran the seat inside Home Assistant comes back by itself.** It becomes an inside entry with provider **Ollama**, its old Ollama URL (`backend`) and its model (the options' pick, else the setup's; the embedding model is dropped: the new seat has none of its own). The setup downloads the new seat and starts it: nothing for the person to do.
  The old entry of the "zoo" (several seats) whose active seat was the inside one is handled the same way. An inside entry whose stored settings hold no Ollama URL or model (none was ever written by 0.7.53's form, but a hand-edited entry may) has nothing to run: it is kept with an empty address and Home Assistant asks for attention with the form *This entry has no seat*: remove the integration and add it again with *Run a seat inside Home Assistant*, or give the address and token of a seat that runs elsewhere.
- **An entry that connected to a seat** keeps its address, but **the old seat's tokens are not valid on the new seat** (the new seat has its own key): the reauth asks for the new seat's admin token.

**The history.** The new seat's data folder is `<config>/membrane/`, the folder the 0.7.53 component's seat used; its old store is `<config>/membrane/.membrane/field.sqlite/field.sqlite3`. The history cutover (docs/cliffvm.md, "The history cutover") looks in `<data>/.membrane` among its places, so the new seat finds the old store by itself, opens it read-only and imports the chat history in the background, newest first, and offers the model the old seat's name.
The new seat's own files (`seat.sqlite`, `door.secret`, `admin.token`, `app/`, `home/`, `seat.pid`) sit beside `.membrane/`, which is never written. The 0.7.53 seat's `home/` folder is the new seat's HOME as well; its `seat.log` is left as it was.
**What does not carry over**: the old field's **entities and goals**, the **settings**, the **embedding model**, the **tokens**, and the **`HaAllow` allow-lists** (*The seat uses the house*, below): recreate the allow-list entries on the new seat, since with none every call of the model into the house is denied. The old component's sidebar panel, landing page, relay, speech input, several seats (the zoo) and its 17 metric entities are not in the component (the metric entities are removed from the entity registry at the migration, *Entities* below).

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

A seat run inside Home Assistant: the entry is `{mode: inside, provider, model, <the provider's key or URL>, seat_name?, serve_port}` (the options flow's settings, when there are some, replace the provider's fields); its unique id is `membrane`. Its files are `<config>/membrane/` (*Run a seat inside Home Assistant*, "Where its data is"); its process is the one child of Home Assistant the component starts.
A seat you run: the entry is `{serve_url, serve_user, serve_pass}`; `serve_pass` holds the token; its unique id is `membrane` (made by the form) or `<host>:<port>` (made or repaired by the add-on's discovery); the component has no files of its own on disk and starts no process.
Either way one background task per entry pulls the seat's calls.

## What is not verified

- **The Linux builds inside a real Home Assistant.** The inside path ran on a real Home Assistant Core on macOS (the apple-darwin build, `scripts/ha_core_smoke.py`). The static musl builds are what Linux hosts download; they have not been run by this component on Home Assistant OS, Supervised, Container or Core on Linux, nor on a Raspberry Pi, nor under an Android proot.
- **A real GitHub download.** The download, its sha256 and manifest checks ran against a local release folder (the entry's `release_base`, which only the tests set). The GitHub release of a version must carry the tarballs and their `.sha256` files for the inside path to start; before a release is published, an inside entry stays *not ready* (Home Assistant retries) with the download's error.
- **The Anthropic provider end to end.** The tests use a fake OpenAI-compatible endpoint; Anthropic's OpenAI-compatible endpoint with a real key was not called.
- **Home Assistant OS's own limits** (the size of `/config`, the memory of a small board while the seat runs next to Home Assistant).

## Tests

`pytest tests/ha` runs the component against a fake Home Assistant (tests/ha/fakeha.py; the discovery: `test_discovery.py`, the two reauth forms: `test_auth.py`) and, where a `cliffvm` binary is built (`cd native && cargo build --release --no-default-features --features bin`; or `CLIFFVM_BIN=`), against a real seat with a fake `claude`. `seat-tests/tests/session_1143.rs` and `seat-tests/tests/ha_1143.rs` pin the routes the component calls (the calls, the allow-list, the journal replay).

`test_seat_options.py` (the provider -> facts mapping, no secret in argv), `test_seat_runner.py` (the platform map; the download's checks: a wrong sha256, a file changed after the manifest, no sidecar, another version, a member outside the folder; the process with a fake `cliffvm`: the environment, the start error, the boot timeout, SIGTERM then kill, the restart backoff and the give-up, the token refresh, the orphan) and `test_inside.py` (the menu, the provider forms, the setup that downloads and starts the seat, the stop, the repair, the options flow, the migration of a 0.7.53 inside entry).

`scripts/ha_core_smoke.py` (by hand, not in CI) runs the same component in a REAL, throwaway Home Assistant Core: a venv with `pip install homeassistant`, run with that venv's
python and `--cliffvm <binary>`. It starts a private seat (the launcher, a fake `claude`, no model server), a `hass` on 127.0.0.1 (onboarded over REST), and drives the connect form,
Assist, the services, a refused token and its repair, a restart and the removal over REST, then the add-on's discovery, the migration of an old entry and the Ollama hint in-process
through Home Assistant's own flow manager, then the inside path in another `hass`: a release tarball built from `--cliffvm`, a fake OpenAI-compatible endpoint, a fake 0.7 store, the menu -> inside -> openai flow over REST,
Assist through the seat the component started, the history cutover, the options flow, a reload, Home Assistant's stop, a start that downloads the tarball from a local release folder, and the removal (the seat process gone after each);
it removes everything it started (`--keep` keeps the logs). Last run (2026-11-15): Home Assistant 2026.11.1 on Python 3.14, macOS arm64, 76 of 76 checks passed.
