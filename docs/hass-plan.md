# Home Assistant on cliffvm: scoping and design (#1143)

*Docs only; nothing built, run or installed. Operator, 2026-11-14: "we need to migrate hass support to c1f". Baseline: tag `v0.7.53` (RP D12). `RP` = docs/release-parity.md, `PP` = docs/packaging-plan.md, `PY` = `membrane/server.py` at the tag. Every number, port, default and platform below is a DECISION (section 10); I chose none, picks in brackets are recommendations.*

## Important first

1. **HA never calls the seat's Ollama surface.** `config_flow.py:28` (`/api/tags`) asks the user's own Ollama MODEL server (inside mode, `ollama_models` at `config_flow.py:95,106,191`), not the seat's :11500. The zoo stores a `proxy_port` (`zoo.py:38,104,128`) and nothing reads it. RP row 76 names HA as a caller of `:11500`: that is wrong. So D3 ("`:11500` served: no", PP decision 5; RP D3 says "keep, last": the two docs disagree) is **not reopened by HA**; only an outside client (HA's built-in Ollama integration pointed at the seat) would.
2. **A second listener needs no new native piece**: `Http:<name>.listen` is keyed per entity and every `Request` carries `http` (`http.rs:109,152`). What does need native work is Ollama's streaming: `Request.send` only writes SSE frames (`event:`/`data:`, `http.rs:276-285`), never NDJSON lines (decision N1, section 5).
3. **The integration is much more than "conversation + sensors"**: `custom_components/membrane` is 2,929 lines: seat supervisor, per-user relay and event filter, panels and landing page, STT, zoo, and the seat's hidden HA user with state push, catalog and MQTT (`ha_access.py`). `integrations/homeassistant/.../membrane_ai` (1,037 lines incl. deploy scripts, domain `membrane_ai`) is the boat's older copy: DEAD, delete (decision D-copy).
4. **Today a new component cannot talk to the new seat**: the 5 `/membrane/ha/*` routes and `/membrane/seat/event` are 404 (RP row 75), `/membrane/state` has empty values (RP row 41), the surface needs a token HA never sends, and the supervisor spawns `python -m membrane.opencode` (`supervisor.py:81-92`) that the new wheel does not have.
5. **Auth is easy because the header already fits**: cliffvm accepts `Authorization: Basic <anything>:<jwt>` (`http.rs:45-56`). The component's `serve_pass` field can simply carry a minted token: no config-flow shape change, and the same entry works against a 0.7.5x Python seat.
6. **Estimate: slice 1 (Assist + connect/inside + token) 6-8 rounds; slice 2 (the seat's use of HA: states, catalog, MQTT, acting) 8-12; slice 3 (relay, panels, STT, zoo; waits for the page rebuild) 5-8; slice 4 (Ollama port, optional) 4-7.** Whole 19-28 (+4-7). RP D6 said 4-6 for the routes alone: that was low.
7. **The freeze (PP decision 3 is already answered NO) ends only when slice 1 ships in the same release as the 0.7.60 wheel**; until then `requirements: babyseat==0.7.53` must stay (section 8).

## 1. Inventory: what the component needs from the seat

Class: MUST (the feature is dead without it), NICE, DEAD (drop). "Cliffvm" = status today (RP). User-visible effect in the last column.

| # | what | component (`custom_components/membrane/`) | Python seat (v0.7.53) | cliffvm | class | the user sees |
|---|---|---|---|---|---|---|
| I1 | GET `/global/health` (config flow check, readiness, watchdog) | `config_flow.py:138`, `supervisor.py:168`, `zoo.py` | `PY:4306` | PRESENT (needs the token) | MUST | "cannot connect" in the setup form; "the seat is starting" in chat |
| I2 | POST `/session {title:"hass-<conv id>"}` | `conversation.py:79-91` | `PY:4992` (RP 16) | PRESENT | MUST | Assist keeps one thread per HA conversation |
| I3 | POST `/session/<sid>/message {agent:"build", parts:[speaker line synthetic, text]}` sync; reads `parts[type=text]` | `conversation.py:108-127`, speaker line `:30-33` | `PY:5051` (RP 24) | PARTIAL (`{info, parts}`; the speaker line part is UNVERIFIED) | MUST | the voice/Assist answer |
| I4 | GET `/membrane/state` + `/agent` every 60 s into 16 sensors | `coordinator.py:28-52`, `metrics.py:13-104` | `PY:4163`, `PY:4621` | PARTIAL: state is empty, `/agent` has no loops/thinker | NICE (rebuilt, I12) | `sensor.membrane_*`, `binary_sensor` paused |
| I5 | GET `/ui/conf` (name) and `/membrane/self/card` (zoo cards) | `zoo.py:259,264,268,274` | ui_serve, `PY:4661` | PARTIAL / NOT-NEEDED (Blurp out, RP D10) | NICE / DEAD | seat name in the panel; zoo card (dead) |
| I6 | GET `/membrane/ha/subscriptions` -> `{entity_ids, mqtt_topics}`, polled 60 s | `ha_access.py:307`, `:301-322` | `PY:4312`, `ha_bridge.py:100,341` | MISSING | MUST for "sense" | the seat's watches fire on HA entities |
| I7 | POST `/membrane/ha/states {states:[{entity_id,state,attributes,last_changed}]}`, batch 2 s | `ha_access.py:375-398` | `PY:4785`, `ha_bridge.py:104` (attrs cap 4000 B) | MISSING | MUST | same |
| I8 | POST `/membrane/ha/catalog {entities[exposed], services{dom:{svc:{target,fields}}}, mqtt}` when its digest changes | `ha_access.py:324-351` | `PY:4790`, `ha_bridge.py:237` (classes `ha:<domain>`, live entities, methods `<svc>`/`<svc>_with`) | MISSING | MUST for "act" | the seat can name and call every exposed light, lock, scene |
| I9 | POST `/membrane/ha/mqtt {messages:[{topic,payload}]}` (HA subscribes the topics the seat marked; flood keeps newest 500) | `ha_access.py:353-373,380-388` | `PY:4797`, `ha_bridge.py:341,345` | MISSING | NICE (only with HA's MQTT) | MQTT in the field with no broker config |
| I10 | POST `/membrane/ha/token {url, token}` -> `~/.config/membrane/hass.env`; the seat's curl templates read it (`ha_bridge.py:181-189`) | `ha_access.py:182-212,146-175` | `PY:4779`, `ha_bridge.py:35` | MISSING; the Command driver reads secrets only from the seat's OWN env at start (`env_from`, `proc.c1f:4`), `Fetch` only `bearer_env` (`fetch.rs:4`) | MUST for "act" in connect mode | the hidden user "Membrane"; see D-act |
| I11 | POST `/membrane/seat/event {kind: ha-start / ha-stop}` | `__init__.py:163-175`, `ha_access.py:215-254` | `PY:4812`, `restarts.py:184` | MISSING | DEAD as written (no thinker or loops on cliffvm to misread a quiet world); NICE as a fact on an entity | nothing |
| I12 | the sensor set itself (16 keys) | `metrics.py:13-48` | `efield`, thinker, loops, `/agent` minds | no source: `efield.*`, `think.*`, `loops-*`, `ambient`, `scripts.failing`, `entity_tracks` do not exist on cliffvm | DEAD for 13 of 16; keep the idea (D-sensors) | fewer sensors |
| I13 | the relay: `/api/membrane/serve/<path>` allow-list (`relay.py:58-104`), per-user session owners in an HA Store (`:294-312`), per-user filtered SSE `/event` (`:366-407`), `[HA user: X]` stamping (`:141-184`), 502 "offline" words | `relay.py` | `/session*` `PY:4322`, `/event` `PY:4308`, `/membrane/*` | PARTIAL (`/event` frames: RP row 30: no keep-alive ping, no `session.diff`; the filter reads `properties.sessionID`, UNVERIFIED on cliffvm frames) | MUST for panels | membrane home and the chat inside HA |
| I14 | page files served from the installed package: `views.py:35-61` (`membrane/ui/`, vendor dir, `assets.ensure` voice models), rewrite `/ui/*` -> `/api/membrane/*` (`:23-25`), `entry.js`, `/api/membrane/name`, `/conf` | `views.py`, `www/entry.js`, `__init__.py:37-47,94-103` | `membrane/ui/` | the page shell is public on the door (`/ui/www/*`) | MUST for panels (depends on the page rebuild, `docs/ui-rebuild-plan.md`) | the "Membrane" sidebar panel, landing page |
| I15 | STT proxy to a whisper server (WAV, <= 15.4 MB, 180 s), `clean_text` | `stt.py:29-99` | none (HA side) | n/a: pure HA-side | NICE (leave; only its `clean_text` is policy) | voice input by server |
| I16 | zoo: several seats, `select.membrane_seat`, `/api/membrane/zoo*` | `zoo.py`, `select.py`, `views.py:122-163` | `/membrane/self/card` | card dead | NICE (seats list and switch yes; cards no) | the 🦠 menu, the select |
| I17 | spawn the seat as HA's child: argv `supervisor.py:81-92`, env whitelist `:41,66-78` (never `SUPERVISOR_TOKEN`), ports 18096/18500 `:134-135`, `MEMBRANE_LOOPS_MAX`, `HASS_URL`/`HASS_TOKEN` `__init__.py:113-119`, health 30 s / boot grace 180 s / TERM then KILL 30 s / backoff 5-30-120 s `:34-37` | `supervisor.py` | the Python seat | N/A: replaced (section 6) | MUST for inside mode | "Inside Home Assistant" install |
| I18 | config flow: menu inside/connect; Ollama models picker (`config_flow.py:26-69,117-130`: chat model, embed model); connect form url/user/password; options (landing, STT, loops 1-3, ha_access, ha_base_url) and zoo editor | `config_flow.py` | `/api/tags` of the model server | `seat_backend`/`minds`/`cloud_models` facts (PP table) | MUST (slimmed) | the setup forms |
| I19 | the `ha_url` the seat is told (#529: local/LAN/external choice) | `ha_access.py:93-175` | `ha_bridge.py:57` | N/A if the pull design is taken (D-act) | DEAD under "pull" | nothing |
| I20 | Ollama surface of the seat (`/api/tags`, `/api/chat`, `/api/generate`, `/api/show`) | none | `lanes.py:4680`, `ollama_api.py:24` | MISSING | **DEAD for HA**; NICE for outside Ollama clients | nothing from this component |

**Files and services the component touches**: `<config>/membrane/{.membrane,home,seat.log,assets}` (`const.py:28`, `supervisor.py`, `views.py:50`), the HA Store `membrane.sessions` (`relay.py:108`), the hidden HA system user "Membrane" and its 3650-day token (`ha_access.py:49-77`), HA's REST API from the seat, HA's MQTT client, the sidebar panels and `add_extra_js_url`, `select.membrane_seat`. No HA services are registered; the component exposes entities and views only.

## 2. Mapping to cliffvm (MUST items)

Existing mechanisms only: packs, `Request.override` (a getter that wins before the shipped routes, `door.c1f:8,237`) so no edit to `door.c1f`'s reserved family list, `this.route`, entities, `Fetch`/`Command`/`File`.

| items | status | pack and shape |
|---|---|---|
| I1, I2, I3 | present / partial | `door.c1f`, `door_session.c1f`. Work: the synthetic speaker part and the `Person` of the token (does the first text part with `synthetic: true` reach the model, as `tests/test_chat_speaker_id_line.py` pins on Python?) UNVERIFIED |
| I6, I7 | missing | new `ha.c1f` + `door_ha.c1f` (`Request.override` arms): class `HaMirror` with the entities the model marks (`ha_entity` prop). `GET /membrane/ha/subscriptions` = a getter listing the `ha_entity` and `mqtt_topic` values; `POST /membrane/ha/states` = writes `ha_state`, `ha_attributes`, `ha_changed` on the marked entity (so its watch getters fire as today) |
| I8 | missing | `POST /membrane/ha/catalog`: one class per domain under a root class `ha`, one entity per HA entity (only `exposed` ones live), one method per service. The Python used a bash+curl template (`ha_bridge.py:181-189`); here it is an entity write that the actuator (I10) consumes. The digest no-op is a prop compare. The method-define surface exists (`methods.c1f`); how a service's `target`/`fields` become a method signature needs one round of reading `methods.c1f` first |
| I9 | missing | `POST /membrane/ha/mqtt`: write `mqtt_payload`, `mqtt_last_topic`, `mqtt_changed` on entities whose `mqtt_topic` filter matches (`+`/`#` wildcards: a c1f getter, the Python one is `ha_bridge.py:318`) |
| I10, I11 | missing | see D-act below; I11 = one write to an entity `Ha:home` {`up`, `since`} (no route needed if the component writes it through the same states route) |
| I4, I12 | partial | `GET /membrane/ha/metrics` returning rows `{key, name, platform, unit, state_class, icon, value}` built by a c1f getter from what cliffvm has (entities by class, messages, busy sessions, paused, seat uptime: `c_field` SQL as `door_session.c1f:132` does). The component builds one entity per row, so the list lives in c1f, not in `metrics.py` |
| I13 | partial | relay stays Python (it is HA's door: HA auth, HA users, HA Store): its only seat-specific content is the allow-list and the owner map. The thin form keeps the owner map (HA user -> sid) and forwards with one token (section 3) |
| I14 | partial | the component stops reading `membrane/ui/` from a package: it proxies `/ui/www/<file>` (public on the door) with the path rewrite it already does. Removes `package_ui`, `VENDOR`, `assets.ensure` |
| I17 | replaced | section 6 |

**D-act: how the seat acts on HA (I10).** Option A, as today: HA mints a token for its hidden user and gives it to the seat. On cliffvm a pushed token has nowhere to live: `bearer_env`/`env_from` read the process environment at start (no `bearer_file`; a token in a property breaks the secret rule, `docs/auth.md`). Inside mode can pass `HASS_TOKEN` in the child's env as now; connect mode cannot (needs a new driver option `bearer_file`: native work). Option B **[recommended]**, pull: the seat writes a `HaCall` entity ({domain, service, data, entity_id}); the component holds a long GET `/membrane/ha/calls` (SSE, exists) or polls it, runs the service through HA's own in-process `hass.services.async_call`, and writes the receipt back. No token leaves HA, no hidden user, no `ha_base_url` (I19 gone), works the same for inside and connect, and HA's own permission model stays in HA. Cost of B: the component grows one small loop (about 60 lines, no policy) and a call is not instant. Cost of A in connect mode: one native driver option (1-2 rounds) plus keeping `ha_access.py` mint/push/URL logic (~250 lines of Python policy). Not built either way yet; **D-act is the operator's.**

## 3. Auth (never tokenless)

A localhost peer is not the owner (docs/auth.md, #842): every route but the shell needs a verified JWT, HA's own host included.
- **Connect mode**: the operator mints once, on the seat: `cliffvm token mint --sub ha --name "Home Assistant" --ttl <T> --secret-file <seat>/door.secret`, and pastes it into the existing form's password field. The component keeps sending `Authorization: Basic base64("opencode:<token>")` (`config_flow.py:139`, `coordinator.py:34`): cliffvm reads the password as the token (`http.rs:49-55`). The form changes only its label and a hint; the entry shape (`serve_url`, `serve_user`, `serve_pass`) is unchanged. A token has a required life (`--ttl`, no default), so the component adds the standard reauth step (HA `ConfigEntryAuthFailed` on a 401, `async_step_reauth` asking for a new token). The first verified request creates `Person:ha`.
- **Inside mode**: the component started the seat, so it reads `<data>/owner.token` (0600, rewritten at every boot, `docs/auth.md`) from disk on each request: no expiry to manage, no secret in the entry. The relay holds the token, never the browser, as today.
- **Who may write the HA routes** (I7-I9, I10 calls): reads are open to every verified person; the `/mcp` `entity_write` is owner-only. A pack route decides for itself: owner only, or the sub named in a fact `ha_sub` [owner or `ha_sub`: decision].
- **Per-HA-user identity** stays what it is: one transport token, `[HA user: X]` stamped by the relay, ownership in HA's Store. Per-user tokens (`--sub ha:<uid>`) would give the seat real `Person`s but need one mint per user and a rotation story: NICE, not first.

## 4. Platforms: how HA users get a seat

| host | design | status |
|---|---|---|
| HA Core / Supervised / OS / Container | **[recommended] keep the pip `requirements: ["babyseat==<v>"]`**: HA installs the platform wheel (musllinux on the Alpine-based HA images, `PP section b/d`), whose `dist/` holds `cliffvm` + packs + ui; the component finds it (`importlib` -> `dist/bin/cliffvm`) and spawns `cliffvm run <dist packs> --store <config>/membrane/cliffvm.sqlite3 --port <p> --bind 127.0.0.1 --fact ...` (PP launcher option 3: `cliffvm serve` does the defaults, the component passes ~5 facts). The `python -m membrane.opencode` line goes; readiness = `/global/health` with the owner token | musl `bin` build never run (PP risk table); `package_found` (`supervisor.py:46`) becomes "binary found" |
| HA OS add-on | a Supervisor add-on is a separate repo layout and image; it is the Docker image below with a `config.yaml`. NICE, later (est. 3-4 rounds) | not designed |
| seat on another host / Docker (`chedim/babyseat`, musl or glibc) | connect mode (exists); the image must be the new one (no Python, PP section d) | needs the image |
| Termux / Android (ThinkPhone, boat) | the boat's HA runs in a proot Debian (`/opt/hass/venv`, aarch64 glibc userland, per memory 2026-10-21), so a **manylinux aarch64 wheel installs there** (UNVERIFIED: proot, RAM; the phone was short on RAM, the in-HA seat was dropped). A native Termux seat is Bionic: the Linux musl-static aarch64 binary may run on the Android kernel without libc (UNVERIFIED), but DNS reads `/etc/resolv.conf` (absent on Android) so a hostname in `seat_backend` may fail; TLS roots are compiled in (`webpki-roots`), sqlite is bundled, no OpenSSL. A separate `aarch64-linux-android` target needs the NDK for `ring` and bundled sqlite: not planned (PP decision 4: Android/Termux none) | UNVERIFIED all; a 1-2 round spike if the operator wants Termux |
| no `claude` CLI on HA hosts | the turn then needs an OpenAI-compatible backend (`chat_oai.c1f`, Ollama via `seat_backend`, w56 first slice) = RP D1 (b); without it an HA user's Assist has no model | gate (section 8) |

The component still never passes HA's own environment to the child (keep `ENV_KEEP`, `supervisor.py:41`; `SUPERVISOR_TOKEN` stays out).

## 5. The Ollama-compatible endpoint (only if wanted: slice 4)

- **One port or two**: the VM already supports a second listener with no native change: a pack on `Boot.started` writes `('Http:ollama').listen = {port: this.ollama_port, secret_file: ...}` beside `Http:door` (`door.c1f:255`); `Request.http` names the listener, so the `/api/*` arms answer only there (`Request.override` guarded by `this.http == 'Http:ollama'`). A single port (everything on :4096) also works and avoids a fact; the cost is that `/api/*` would sit beside the page's routes. [one extra port, off by default, fact `ollama_port`: decision.]
- **Mapping `/api/chat` to the turn**: `model` -> `model_eff` of a `ChatMessage` (`chat_turn.c1f`, `chat_oai.c1f`), the LAST user message -> the prompt (no history: the turn is stateless, `chat_oai.c1f` header), a throwaway `Chat:session` per call (or per `X-Conversation-Id` header), answer = the `ChatResponse` text, `done_reason` and counts from the turn's usage. Tools: the door's own field tools run inside the turn; Ollama `tools` in the request body are ignored by default [decision: ignore vs pass through], and `tool_calls` are never returned to the client.
- **`/api/tags`**: a getter over `Model:*` entities (`router.c1f`): `{models:[{name, model, size?, details?}]}`; unknown fields omitted, not invented. **`/api/show`, `/api/generate`**: `generate` = `chat` with one user message [NICE]; `show`: DEAD unless a client needs it.
- **Streaming (the real gap)**: Ollama's default is `stream: true`, NDJSON (`ollama_api.py:10`). `Request.send` writes `data: <json>\n\n` only (`http.rs:276-285`), so NDJSON needs a native frame option (`raw`/`ndjson`), 1-2 rounds. `stream: false` works today with a plain `respond`. Decision N1: add the frame option [yes if slice 4 is wanted], or serve `stream:false` only.
- **Auth**: the same door secret and gate; a client's API key is the JWT (Bearer). `GET /api/tags` is open on the Python seat (`docs/auth.md`); under #842 it should not be [keep gated: decision].

## 6. The thin component and how it finds the seat

What stays Python (HA runs Python): config and reauth flow, one `SeatClient` (aiohttp, one auth header, timeouts), the conversation entity (text in, text out), the entity builder for `/membrane/ha/metrics` rows, the HA-side collectors (state change listener, `async_get_all_descriptions`, `async_should_expose`, MQTT client: these only read HA and forward raw rows), the relay (HA auth + owner map), the supervisor, `clean_text` for STT. What moves to c1f: the metric list and names, the catalog-to-graph mapping, the mirror/MQTT logic, the call queue, the seat's name and card, any wording.
Supervisor (I17): keep the loop and its numbers (decisions), change only argv, the env whitelist, one port instead of two (the old second port was the :11500 proxy), the ready probe with the owner token, and the child's data path. The seat's own restart-for-new-code (`restarts.py`) is Python-seat only: a crash or SIGTERM is simply respawned.
`membrane_ai` (the boat's second copy, domain `membrane_ai`, basic-auth pair, `/session` + `/membrane/state` only): **delete** it in the slice-1 PR [decision]; HACS ships one domain.

## 7. What to test

Existing HA tests (repo `tests/`, pytest, Home Assistant stubbed, `integrations/homeassistant/tests/conftest.py:1-60` is the fake-HA harness; there are no browser tests for HA, `tests/js/*.mjs` test the page with a fake fetch):

| tests | needs the Python seat? | fate |
|---|---|---|
| `test_ha_supervisor.py` (90), `test_ha_relay_paths.py` (354; scans `membrane/ui/*.js` for fetched paths against the allow-list), `test_ha_detached.py` (215), `test_ha_routable_url.py` (148), `test_blurp_zoo_283.py` (565), `test_ha_brand.py` | no: they import `custom_components/membrane` with stubs | adapt (argv, auth, rows); routable_url and detached token tests die with D-act B |
| `test_ha_bridge.py` (74), `test_ha_graph_map.py` (245), `test_restarts_ha.py` (34) | yes: import `membrane.ha_bridge`, `membrane.restarts` | replaced by Rust process tests on the c1f packs |
| `integrations/homeassistant/tests/test_metrics.py` (79) | no | replaced by a test of the metrics rows |

New: (1) **golden fixtures from the oracle**: with the Python 0.7.53 seat, record the replies the component consumes (health, session create, message `{info, parts}`, subscriptions, the `ha/*` POST receipts for a fixed catalog and fixed states) into `tests/golden/ha/`, the way `tests/golden/{boot,session}` were made (RP D12: record from the tag, never HEAD). (2) **`native/tests/ha_1143.rs`** in the style of `chat_openai_1143.rs` / `door_auth_1143.rs`: boot `cliffvm` on the shipped packs, mint a token, replay the fixtures, assert the catalog creates the classes/entities/methods, a state push fires a watch, a refused token is 401, the pull queue round-trips. (3) **A component-against-cliffvm test without HA installed**: extend the stub harness with a tiny fake `homeassistant` surface (already stubbed for the 6 suites above) and run the real `SeatClient` and conversation entity against a real `cliffvm` process (and a fake `claude`/OpenAI endpoint as in `chat_openai_1143.rs`; no real model, no real HA, no network beyond 127.0.0.1). (4) A fake-binary supervisor test (a script that listens and exits) for spawn, backoff, TERM then KILL. (5) The release gate rehearsal in a clean venv: the component's `requirements` resolve to the new wheel and `cliffvm --version` runs (a throwaway tag, never PyPI; `PP` P10).

## 8. Upgrade path and the freeze

- **Hazard** (`PP` section 1): the manifest pins `babyseat==0.7.53` and spawns `membrane.opencode` (`manifest.json`, `supervisor.py:81-92`). The 0.7.60 wheel has neither a Python seat nor that module, so an HA user who updates HACS gets a dead integration. PP decision 3 (freeze) was answered NO on 2026-11-14; this note is its replacement.
- **The freeze ends** when slice 1 is in the 0.7.60 release tree, `manifest.json` carries `babyseat==0.7.60` and `version` 0.7.60 (the `tests/test_version.py` guard already ties them; no exception needed), and the gate below passes. Until then the tree keeps the 0.7.53 pins, which is exactly the PP P7 line. HACS reads the GitHub release, so PyPI must carry the wheel first (the publish lane orders it).
- **Entries**: `async_migrate_entry` bumps the minor version (1.2 -> 1.3, same mechanism as #283, `__init__.py:65-71`): inside entries keep `<config>/membrane/.membrane/` (the old store is the legacy import's input: `legacy_import.c1f`, MD R8, `legacy.v0753` snapshot first; never overwritten); connect entries keep `serve_pass`, which now must be a JWT: the entry goes to **reauth** (one HA repair card) rather than silently failing; a 0.7.5x Python seat keeps working with the same header in JWT mode, in seat-token mode its token is the password as before (so a boat seat on 0.7.5x is untouched until it is replaced).
- **Release gate** (add to PP section g): a clean HA venv installs the component and the wheel, inside mode boots the seat and Assist round-trips against a fake model; connect mode round-trips with a minted token; slice 2 is NOT a gate (the seat's use of HA may arrive as a patch 0.7.6N if the operator accepts that "the seat acts on HA" is missing in the first release: decision), but the release notes must say so.

## 9. Risks and unknowns

1. Which HA feature is most used is **unknown to me** (no telemetry): my guess is Assist (I2-I3) and the panel (I13-I14), by the operator's 2026-10-12 words; confirm.
2. The panels depend on the page rebuild (`docs/ui-rebuild-plan.md`, RP "page parity"): relay work without a stable page is wasted; slice 3 waits.
3. `/event` frames on cliffvm are PARTIAL; the per-user filter reads `properties.sessionID` (`relay.py:201-217,223-235`): UNVERIFIED.
4. Musl `bin` build, aarch64 Linux, Android: never built (PP); wheel layout `dist/bin/cliffvm` is a proposal.
5. Model on HA hosts: without RP D1 (Ollama turn path, first slice w56) an HA Assist user has no answer; the claude CLI is not on HA hosts.
6. The catalog-to-methods step (`ha_bridge.py:237-311`) leans on Python `method_define` and a bash+curl actuator; the c1f equivalent is not read yet (1 round to read `methods.c1f`).
7. The relay forwards `/api/membrane/serve/*` to the door: under the owner token every HA user then has owner reach on the seat (unlike the Python seat's separate admin gating only in HA, `relay.py:427-432`): the HA-side admin gates must stay, and `ADMIN_WRITE_EXACT` must be re-checked against the new route set.
8. A token with a ttl in a config entry silently expires without the reauth step; slice 1 must include it.

## 10. Build plan (red test first, each round a failing test then the fix; PRs based on main, no stacks)

**Slice 1: Assist, config, inside and connect (restores the Assist agent and the seat life): 6-8 rounds**
- R1 (1): record `tests/golden/ha/` from the 0.7.53 tag; red `native/tests/ha_1143.rs` for I1-I3 with a minted token.
- R2 (1): `SeatClient` + config flow (label/hint, reauth, health check with the token); fake-HA tests.
- R3 (1): conversation entity on `SeatClient`; seat gap: the speaker part and error words.
- R4 (2): supervisor on `cliffvm` (argv, facts, owner-token readiness, backoff kept), fake-binary tests; wheel finder.
- R5 (1): `membrane_ai` copy removed, manifest/pins, `async_migrate_entry` 1.3, docs/home-assistant.md rewritten.
- R6-R8 (0-2): the clean-venv gate rehearsal, fallout (shared with packaging P10).

**Slice 2: the seat's use of HA (sense, map, act): 8-12 rounds**: `door_ha.c1f` subscriptions + states (2), catalog to classes/entities/methods (3), MQTT (1-2), acting per D-act (B: call queue + component loop 2-3; A: `bearer_file` native option 1-2 + token delivery), metrics rows + sensors (1-2).

**Slice 3: relay, panels, STT, zoo: 5-8 rounds** (after the page): relay on the new routes and owner map, page file proxy, `/event` per-user filter, zoo without cards, STT unchanged.

**Slice 4: the Ollama port (optional, not HA): 4-7 rounds**: `/api/tags` (1), `/api/chat` non-stream (2), NDJSON frame option (1-2, native), `/api/generate` (1), second listener fact (1).

## Open decisions (operator)

1. **D-first**: the first slice = Assist + config + spawn (slice 1) [yes] vs panels first.
2. **D-act**: the seat acts on HA by A (token to the seat; needs a native `bearer_file` for connect) or B (pull queue run by the component) [B].
3. **D-token-ttl**: the connect token's life (30d? longer?) and the reauth flow [reauth yes; the number is yours].
4. **D-write-who**: who may POST the `ha/*` routes: owner only, or owner plus a fact `ha_sub` [owner plus `ha_sub`].
5. **D-copy**: delete `integrations/homeassistant/custom_components/membrane_ai` and its deploy scripts (`deploy/agdroid`, `deploy/brain`: Python-seat Termux supervision, DEAD with it) [yes].
6. **D-sensors**: keep sensors rebuilt from c1f rows (counts, busy, paused, uptime) vs drop them; the 13 efield/thinker/loop keys die [rebuilt, small].
7. **D-zoo**: keep the seat list and `select.membrane_seat` without Blurp cards [yes].
8. **D-platform**: musl-static first for HA hosts; Android/Termux: out of 0.7.60 [yes] vs a 1-2 round spike; HA OS add-on later [yes].
9. **D3-reconcile**: RP D3 says "keep :11500, last", PP says "[no]": make it one line: **not served in 0.7.60; HA does not need it** [yes]; slice 4 only if you want HA's built-in Ollama integration (or other Ollama clients) to use the seat.
10. **N1**: if slice 4: add an NDJSON frame option to `Request.send` (native) vs `stream:false` only; `/api/*` gated by the token (not open like the Python `/api/tags`).
11. **D-ports/numbers**: one seat port in inside mode [18096 first, walk if taken], the ports 18500/11500 gone; `ollama_port` default (slice 4) 11500; bind `127.0.0.1` (PP decided); poll 60 s, subscriptions 60 s, flush 2 s, MQTT batch cap 500, attribute cap 4000 B, health 30 s, boot grace 180 s, stop grace 30 s, backoff 5/30/120 s, request timeouts 600 s / 1800 s, STT limits 15.4 MB / 180 s: **kept as they are unless you change them**; the token-ttl above; HA minimum `2025.1.0` (`hacs.json`; the code needs a newer HA for `AddConfigEntryEntitiesCallback`: check).
12. **D-version**: the component is 0.7.60 with the wheel (PP decision 4) [yes]; the domain `membrane` stays.
13. **D-gate**: slice 2 is not a release gate; the first release says "the seat acting on HA is missing" in its notes [your call], and the model backend rule (RP D1: Ollama first slice) is a gate for HA users [yes].
