# Release parity: what the cliffvm seat lacks to replace the released babyseat unnoticed

Investigation, no code changed (#1143). Tree: `w29-parity` = `memo-hotfix-0753` at `fd779cf4` (2026-11-13). **Supersedes** the scratchpad
inventories written for the barest scope (`modes-inventory.md`, `keep-list-inventory.md`) for the release scope.
Agreed order (operator): (1) server swap, (2) no-history chat backend behind the same routes, (3) UI rebuilt as view packs,
(4) data migration and cutover. One "round" = one agent round of 20-45 min.

Conventions. `PY` = `membrane/server.py` at HEAD; `PY53` = the same file at tag `v0.7.53` (the release users run: it still serves the
decision routes that HEAD moved to the door); `LN` = `membrane/lanes.py` (the :11500 proxy and the field map); `UIS` = `membrane/ui_serve.py`;
`P/` = `membrane/packs/`; `N/` = `native/`; `T/` = `tests/`. "Used" = called by something in the tree (UI JS, HA integration, TUI attach
stubs), found by grep, never "exists in Python". Status on cliffvm: **PRESENT** = a c1f/Rust route exists and a test covers it;
**PARTIAL** = answers but not with the Python behaviour; **MISSING** = 404/501/405 or no route; **NOT-NEEDED** = with why.
Everything under "dry run" was observed on `cliffvm` built from this tree, a fresh store, port 4314.

## 0. Headline

| | |
|---|---|
| Used routes | **121 method+path pairs in 76 table rows** (section 1); 111 are needed (10 NOT-NEEDED) |
| PRESENT / PARTIAL / MISSING / NOT-NEEDED | pairs **30 / 43 / 38 / 10**; rows 22 / 36 / 12 / 6 (w46: `/membrane/stats` and `/membrane/minds` answer the empty released shape) (the session API and its streams built in w31: rows 15-25, 30, 31; the page's boot calls and the field views' empty answers in w33: section 6) |
| UI views | 9 (shell, chat, decisions cockpit, home, voice, 2D field, 3D field, work graph, boot animation), about 15,200 lines of own JS (section 2) |
| Rounds: server swap | 73-110 (map through a Python bridge, model backends included 14-22); 62-91 for a first swap with the claude CLI as the only model |
| Rounds: no-history backend behind the same routes | 8-12 |
| Rounds: UI rebuild (VM growth + 9 views + test rewrite) | 112-169 |
| Rounds: data migration and cutover | 14-22 |
| The finding that changes the plan | (w30: the `/ui/serve/` prefix, the owner cookie and a full-set launch line now exist; the page boots on `cliffvm`, section 6 lists what still 404s) the current page cannot be served by `cliffvm` with ANY pack set today: every page call goes through `/ui/serve/<path>` (`panel.js:34`, `home.js:20`, `field.js:20`) and the door has no such prefix (dry run: `/ui/serve/global/health` 404); the whole session/event API the chat speaks (`/session*`, `/event`) does not exist (barest chat is `/membrane/chat/*`, `P/chat.c1f:58`) |

## 1. Route inventory (used routes)

Callers: UI = browser page (all through `/ui/serve` + the path, `UIS:445-463` strips it); HA = `custom_components/membrane` (relay.py proxies
the UI's paths under `/api/membrane/serve/`, `relay.py:320-340`, so HA adds only its own calls); TUI = `opencode attach` (a third-party
binary: its probe set is what `PY` stubs and `T/test_server.py:831-860` pin; its live traffic was **not recorded**, a real TUI run was forbidden).
Shape pointer = where the Python handler (request/response) is; "spec" = a JSON spec in `T/specs/`, "rs" = a Rust test in `N/tests/`.

### 1.1 Shell, auth, config (page boot)

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 1 | GET `/`, `/index.html`, `/membrane` | browser | `UIS:467-481` (also mints an owner cookie on loopback, `UIS:468-479`) | PRESENT | `P/door.c1f:21-60`; `N/tests/seat_process.rs`, `door_static.rs`; dry run 200 only with `--ui-dir membrane/ui` (404 without). **Owner cookie on loopback: PRESENT (w30)**: `P/door_auth.c1f`, `Request.mint`, test `N/tests/door_auth_1143.rs` `the_page_fetched_from_loopback_sets_the_owner_cookie`; a forwarded request gets the page and no cookie |
| 2 | GET `/favicon.ico` | browser | `UIS:482` | PRESENT | `P/door.c1f:23`; dry run 200 |
| 3 | GET `/ui/www/<file>` (the page's JS (`index.html:42-145` loads 7 by name, `home.js` imports the rest)) | browser | `UIS:506-511` | PRESENT | `P/door.c1f:24-36`; dry run 200 for 16 files (with `--ui-dir`) incl. the 933 KB wasm |
| 4 | GET `/ui/www/voice-ml.mjs`, `vosk-model-en.tar.gz` | `home.js:519,607` | `UIS:63-64` (Python looks in `<data dir>/assets/`, `DEV_WWW`) | PARTIAL | the door searches only `ui_roots` (`P/door.c1f:15-20`); files are not in `membrane/ui`; dry run 404 |
| 5 | GET `/ui/conf` | `index.html:47`, `panel.js:2710`, HA `zoo.py:284` | `UIS:243-252, 484-494` | PARTIAL | spec `door_conf.json`; dry run 200; (the seat-token mode is DROPPED by decision, so `auth: "jwt"` is final and `owner` is not needed) `auth` is hard `"jwt"` and `owner` is missing (`P/door.c1f:75`), Python says `seat` + `owner` when JWT is off |
| 6 | POST `/ui/login` | `host.js:66` | `UIS:334-380` | PRESENT | `P/door.c1f:100-107`; spec `door_login.json`; `N/tests/jwt_door.rs`; JWT mode only (the seat-token mode, `UIS:337-352`, `seatauth.py`, is DROPPED by the operator's decision); its 401 now has the released shape (w30) |
| 7 | GET `/ui/fieldvm` | `fieldvm.js:274` | `UIS:495-498` (`fields_live.snapshot`) | PARTIAL | dry run 404; the page's field VM (About window) cannot start **w33**: `P/door_fieldvm.c1f` (family `fieldvm`): `{packs, entities}` with the packs that `IMPORT dom` (read once at boot from the apps' folders); `entities` is `[]` (nothing feeds `About:seat`), tests `N/tests/boot_routes_1143.rs`, `tests/js/fieldvm_snapshot.test.mjs` |
| 8 | GET `/ui/serve/event` (SSE `field.write`/`field.pack`) | `fieldvm.js:283` | `PY` `_sse` | PARTIAL | dry run 404 **w33/w31**: the stream exists (`P/door_session.c1f`: the session events); the `field.write` / `field.pack` / `field.remove` frames the page's field VM follows are not sent, so the page's field never changes after the snapshot |
| 9 | the `/ui/serve/<path>` prefix itself (every row below) | `panel.js:34`, `home.js:20`, `field.js:20` | `UIS:445-463` | **PRESENT (w30)** | `P/door.c1f` `ui_served`/`ui_path`/`route`; every family reads `this.route`; test `N/tests/door_serve_prefix_1143.rs` (a route answers on both paths), `door_auth_1143.rs`; a prefixed call needs a verified JWT. A route that does not exist behind it answers 404 (`/ui/serve/session` still does: row 15) |
| 10 | POST `/ui/log` | `host.js:10` (beacon) | `UIS:302-320` | PRESENT | `P/door_voice.c1f:92-110`; spec `door_voice.json`; fresh store OK (a file write) |
| 11 | POST `/ui/stt` | `home.js:4242,4443,4549` | `UIS:272-300` | NOT-NEEDED (default) | `/ui/conf` says `stt: browser` unless the settings name a whisper server (`UIS:243-250`); then it is a 501 on cliffvm (`P/door_voice.c1f:21-22`: a WAV is bytes) |
| 12 | GET `/ui/zoo`, POST `/ui/zoo/active` | `home.js:699,3087`, `panel.js:2101,2674` | HA only (`custom_components/membrane/zoo.py`, `views.py:127-144`) | NOT-NEEDED | standalone answers 404 (`UIS:513`); the page catches it (`panel.js:2674`) |
| 13 | GET `/__boot/shape` | `fieldanim.js:175` | `bootprof.BootDoor` (only while the Python seat loads) | NOT-NEEDED | the page ignores a failure (`.catch`, `fieldanim.js:175`); cliffvm is healthy 0.84 s after start |
| 14 | GET `/global/health` | `fieldanim.js:152`, `panel.js:2718`, HA `config_flow.py:138`, `supervisor.py:168` | `PY:4396` | PRESENT | `P/door.c1f:125-128`; spec `door_probes.json`; dry run 200; also on `/ui/serve/global/health` (w30). The Python 503 `{loading}` JSON the page's SeatWatch reads (`fieldanim.js:152-160`) is not produced |

(`POST /ui/logout` exists in both but nothing calls it: not counted.)

### 1.2 Chat: the opencode session API and its event stream

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 15 | GET `/session` (list) | `panel.js:2893` | `PY:4408`; the person's sessions only (JWT) `PY:4274` | PRESENT | `P/door_session.c1f`; `N/tests/session_1143.rs` (shape against `T/golden/session`) |
| 16 | POST `/session` {title} | `panel.js:2921`, HA `conversation.py:81` | `PY:4992` | PRESENT | same (a session is a chat: `membrane::Chat:session:<sid>`; no one-chat-per-person rule, no owner) |
| 17 | GET `/session/{sid}` | `panel.js:2812` | `PY:4458` | PRESENT | same |
| 18 | PATCH `/session/{sid}` {title, model} | `panel.js:3047,3067,3428` | `PY:5277-5312` | PARTIAL | **w41**: the title and the MODEL (`{model: {modelID}}` becomes the session's and the turn runs on it, `N/tests/session_1143.rs`, golden `session_patch_model.json`); the agent pick (`build`/`plan`) is ignored: the turn has no read-only mode |
| 19 | DELETE `/session/{sid}` | `panel.js:3054` | `PY:5325` | PRESENT | same; the session and its messages are retired |
| 20 | GET `/session/status` | `panel.js:3018,3318` | `PY:4442` | PARTIAL | `{type: idle|busy}` per session from the store, as the released status is (the released `elapsed_ms` is on the event stream's heartbeat, not here); no queue depth: turns of one session run side by side, the released seat queues them (`queued: true, depth`) |
| 21 | GET `/session/{sid}/message[?limit&before]` | `panel.js:2984,3017,3612` | `PY:4488-4510` (rows `{info, parts}`, order healed) | PRESENT | same, with `?limit` (newest N rows) and `?before=<message id>`; rows `{info, parts}` oldest first; the finished turn lists its tools, then its answer text |
| 22 | GET `/session/{sid}/message/{mid}` | TUI | `PY:4458-4468` | PRESENT | same (the user row or the assistant row by id) |
| 23 | POST `/session/{sid}/prompt_async` {agent, parts, voice?, stream?, final?, claudeContinue?, model?} | `panel.js:3348,3362,3380,3404` | `PY:5051`, `_message` `PY:5080+` | PARTIAL | 204 and the turn starts; **w41**: `model` is read (it sticks to the session and reaches `claude --model`; a model that is no claude rung is refused in the turn), `stream` + `final: false` (a voice partial) answers 204 and starts no turn; `agent`, `voice`, `claudeContinue` are ignored (no resume: the turn has no history, "Continue" is a fresh message); the text parts are joined |
| 24 | POST `/session/{sid}/message` (sync, returns the reply) | HA `conversation.py:114` | `PY:5051` | PARTIAL | 200 `{info, parts}` when the turn ends; the same fields as row 23 (a voice partial answers 204) |
| 25 | POST `/session/{sid}/abort` | `panel.js:3437` | `PY:5054` | PRESENT | same: the running turns of the session are stopped (`Command.stop`), they end as `MessageAbortedError` |
| 26 | GET and POST `/session/{sid}/voice` | `panel.js:3019,3966` | `PY:4476`, `PY:5042` (the vote/rewrite teaching) | PARTIAL | **w33**: GET answers `[]` (no voice lines of its own, as a released seat with none); POST (the vote / rewrite teaching) is not built (405) |
| 27 | GET `/session/{sid}/predict` | `panel.js:3020` | `PY:4472` (`predict.phantoms`) | PARTIAL | page tolerates a failure (`.catch`, `panel.js:3020`) **w33**: answers `[]` (no predictor here: the released answer with no phantoms) |
| 28 | GET `/session/{sid}/diff`, `/todo`, `/children` | TUI | `PY:4470-4486`, pinned `T/test_server.py:818-826` | PRESENT | **w33**: `[]` for the three and `/voice`, `/predict` (the released answers), `P/door_session.c1f`, `boot_routes_1143.rs` |
| 29 | POST `/session/{sid}/init`, `/share`, `/unshare`, `/summarize`, `/permissions` | TUI | `PY:5058-5069` (stubs returning `true`) | MISSING | |
| 30 | GET `/event` (SSE: `server.connected`, `session.*`, `message.updated`, `message.part.updated`, ...) | `panel.js:3105`, `field3d.js:1716`, `home.js:3593`, TUI | `PY:4398`, `SSEHub` `PY:885`; frames pinned `T/test_server.py:798-815`; per-person filter `PY:4284` | PARTIAL | the events the chat view uses (below); no `session.diff`, no keep-alive ping, no step parts, no `membrane.*` events |
| 31 | GET `/global/event` (the same, wrapped `{directory, project, payload}`) | TUI | `PY:4400`; `T/test_server.py:798` | PARTIAL | same, wrapped `{directory, project, payload}` |

### 1.3 opencode boot probes and stubs (the TUI; also the Python-seat reads the HA coordinator makes)

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 32 | GET `/agent` | `panel.js:2757`, `field.js:703`, HA `coordinator.py:46` | `PY:4692`; must carry a primary agent `T/test_server.py:855-868` | PARTIAL | dry run 404 **w33**: `P/door.c1f` `ag_body`: the five released rows (`build` and `plan` primary), golden `tests/golden/boot/agent.json`; the live mind rows (loop workers, thinker, dreamer) are not there |
| 33 | GET `/config`, PATCH `/config` | `panel.js:2771`, TUI | `PY:4511`, `PY:5313` | PARTIAL | **w33**: GET answers the opencode config (provider `membrane` on this door, `model`, `small_model`, from `Boot:seat.minds.seat_default`); PATCH is not built; the released config is written by `opencode.py` at launch, not recorded |
| 34 | GET `/config/providers`, `/provider`, `/provider/auth` | TUI | `PY:4513-4545` | MISSING | |
| 35 | GET `/experimental/{capabilities,console,resource,workspace,workspace/status}` | TUI | `PY:4528-4543, 4590`, pinned `T/test_server.py:831-853` | MISSING | |
| 36 | GET `/formatter`, `/lsp` | TUI | `PY:4547-4550` | MISSING | |
| 37 | GET `/mcp` (opencode's MCP status, `{}`) | TUI | `PY:4545` | MISSING | **collision**: cliffvm owns `/mcp` for the MCP door (POST only, `P/door_tools.c1f:23-24`); GET answers 405 (dry run) |
| 38 | GET `/api/model`, `/api/provider`, `/api/*` | TUI | `PY:4553-4590` | MISSING | |
| 39 | GET `/command`, `/path`, `/project`, `/project/current`, `/project/{id}/directories`, `/vcs` | TUI | `PY:4694-4720` | PRESENT | `P/door.c1f:125-138`; spec `door_probes.json`; dry run 200 |
| 40 | POST `/log`, `/instance/dispose`, `/tui/*` | TUI | `PY:5071-5075` | MISSING | |

### 1.4 Seat state, controls, models, options

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 41 | GET `/membrane/state` | `field.js:703`, HA `coordinator.py:44` | `PY` seat read -> the :11500 proxy (`SEAT_READ_PATHS`, `PY:4163`) | PARTIAL | `P/door_state.c1f:65-80`; spec `door_state.json`; dry run `{"mode":"DEV","paused":false,"seat":{}}`: it reads `membrane::presence`, which nothing writes on cliffvm; `?section=` 501 |
| 42 | GET `/membrane/seat` | `panel.js:3896` | `PY:4596` | PARTIAL | same presence gap (dry run `{}`) |
| 43 | GET and POST `/membrane/seat/pause` | `home.js:1273,1287` | `PY:4606`, `PY:4909` | PARTIAL | POST answers 202 and records `presence.control`; nothing consumes it (`P/door_state.c1f:6-11`) |
| 44 | POST `/membrane/seat/control` {paused, self, accept, loops_max, self_name} | `panel.js:3850,3863,3880` | `PY:4894` | PARTIAL | same gap |
| 45 | POST `/membrane/seat/event`, `/membrane/seat/restart-ask` | HA `ha_access.py:223,243` | `PY:4883` | MISSING | |
| 46 | GET and POST `/membrane/options` | `index.html:78,101`, `home.js:876,892,909` | `PY:4625`, `PY:4961` (settings.json + energy + runner) | PARTIAL | dry run 404 **w33**: `P/door_models.c1f`: GET in the released shape (golden `options.json`, `options_bg.json`), POST validated first (400 with the reason), `self_cpu_share` and `code_runs_keep` kept on `Settings:seat` (nothing reads them here yet), the minds' keys go to `Minds:seat`; an unknown key is refused without its name (no `keys()`) |
| 47 | GET `/membrane/stats` | `home.js:1028` | `PY:4630` (`stats.py`, tokenomics) | PARTIAL | **w46, DEGRADED**: 200 in the released shape of a seat that counted nothing (oracle `tokenomics._assemble`, `server.py:4559`; golden `tests/golden/boot/stats.json`): every window zero, `minds`/`models` `{}`, `claude.answers` 0 (the window draws its zero rows and "no Claude answers since the seat started", `tests/js/stats_empty.test.mjs`), `paused: []`; the counters (message-store token rows, `bg-usage.json`, the claude ledger) are Python state. Decision A below |
| 48 | GET `/membrane/quota`, POST `/membrane/quota/resume` | `panel.js:2592,2625,2640`, `home.js:1307` | `PY:4634`, `PY:4937` (`quota.py`) | PARTIAL | 501 / 404; keep-list question 1 (is quota dropped?) is still open **w33**: `P/door_state.c1f`: GET and POST `/resume` answer the released shape of a seat with nothing paused (`paused: []`, `probe_s: 600`); no model is paused here |
| 49 | GET `/membrane/minds` | `home.js:4840` | `SEAT_READ_PATHS` (`PY:4163`), relayed to :11500; admin only (`relay.py` `ADMIN_READ_EXACT`) | PARTIAL | **w46, DEGRADED**: 200 `{loops: {}, thinker: [], dreamer: []}`, the released answer with no pulse (`lanes.py:4606-4609`; keys of `fieldpulse.minds()`, golden `minds.json`); the orb chat views read no session, so `home.js:4840` finds no `sid` |
| 50 | GET and POST `/membrane/models` {loops, cortex, workers, think, ...} | `panel.js:2823,2837,2859` | `PY:4592`, `PY:4971` | PARTIAL | `P/door_models.c1f`; specs `minds.json`, `N/tests/models_door.rs`; works only after the launcher tells `Boot:seat.minds` (dry run 503 without, 200 with `--fact minds=...`); applying a pick (runner.model, `minds_models.py`) stays Python |
| 51 | GET `/v1/models` | `panel.js:2782,2824` | `PY` seat read (`SEAT_READ_PATHS`) | PARTIAL | spec `models.json`; **w41**: lists the claude-agent rungs of `cloud_models` (`owned_by: membrane->claude-agent`) with or without `router_on` (golden `boot/v1_models.json`, written from `lanes.py` `_v1_models`); local tags are still listed when `seat_backend` answers, but a pick of one is refused in the turn (claude is the only model the seat runs) |

### 1.5 Decisions, goals, work graph

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 52 | GET `/membrane/decisions` | `decisions.js:654` | `PY53:4523` (deleted from HEAD) | PRESENT | `P/door_decisions.c1f`; spec `door_decisions.json`; dry run 200 |
| 53 | GET `/membrane/goals` | `decisions.js:655` | `PY53:4540` | PRESENT | same |
| 54 | GET `/membrane/work-graph` | `fieldview.js:149` | `PY:4613` | PARTIAL | dry run 200; watch nodes and the rocket runs are not drawn (`P/door_decisions.c1f:20-26`) |
| 55 | GET `/membrane/entity-history?spec=` | `fieldview.js:161` | `PY:4613` | PARTIAL | `N/tests/door_decisions_sql.rs`; **dry run: never answers on a fresh store** (curl 20 s, no byte): the `sql()` reads `c_entities`/`c_entity_deltas`, absent in a cliffvm-made store; `properties` answer `{}` **w33**: no longer hangs: the Python tables are asked for first and a store without them has an empty history (`door_decisions.c1f` `dm_tabs`) |
| 56 | GET `/membrane/item-chat?spec=` | `fieldview.js:162` | `PY:4613` | PARTIAL | same hang on a fresh store **w33**: same (answers `{ok: true, eid, messages: []}`) |
| 57 | POST `/membrane/item-chat/say` {spec, text} | `fieldview.js:167` | `PY:4926` | MISSING | **w46: an honest 501** `not ported yet: ...` (was 404): a write has no empty shape (the oracle writes a Message and a relation edge, `server.py:4889-4899`, `decision_map.item_say`), and answering `ok` would lie; the page catches it (`fieldview.js:167`, "shown by reload"); nothing is written |
| 58 | POST `/membrane/decision/answer` | `decisions.js:1021` | `PY53:4855` | PRESENT | spec `door_decisions.json` |
| 59 | POST `/membrane/decision/drop` | `decisions.js:1001` | `PY53:4855` | PRESENT | same |
| 60 | POST `/membrane/decision/chat` | `decisions.js:963` | `PY53:4855` | PRESENT | same |

### 1.6 The field map (the 3D window and the 2D panel)

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 61 | GET `/membrane/map/field` | `field.js:512` | `LN:4642` (the :11500 proxy; `PY` relays, `PY:4163`) | PARTIAL | dry run 404; the engine is Python (`field3d.py` 403, `fieldmap.py` 240 lines, efield) **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: `{axes: [], nodes: [], edges: []}` (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 62 | GET `/membrane/map/field3d` | `field3d.js:1153` | `LN:4651` | PARTIAL | **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: an empty projection (n_total 0, root tile, current) (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 63 | GET `/membrane/map/tile` | `field3d.js:1221` | `LN:4653` | PARTIAL | **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: the empty tile of the one pid, 410 for another (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 64 | GET `/membrane/map/locate` | `field3d.js:1537,1622`, `home.js:3661` | `LN:4655` | PARTIAL | **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: 404 "not in this projection" (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 65 | GET `/membrane/map/search` | `field3d.js:1606` | `LN:4657` | PARTIAL | **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: `{q, hits: []}` (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 66 | GET `/membrane/map/distance` | `field.js:975` | `LN:4646` | PARTIAL | **w33, DEGRADED on purpose**: `P/door_map.c1f` answers what the released code answers for a field with no live entity: 503 "the field is not loaded" (golden `tests/golden/boot`); both views read "the field is empty" (`tests/js/field_empty.test.mjs`); the engine is a read-only Python bridge later, native recall after |
| 67 | GET `/membrane/map/blurp` | `field3d.js:1092` | `LN:4648` | NOT-NEEDED (default) | Blurp overlay, off by default (`UIS:254-260`, `cli.rs` `seat_blurp=false`) **w33**: answers the empty overlay (`P/door_map.c1f`) |
| 68 | GET `/membrane/field/entity?eid=` | `field.js:673,1024` | `LN:4644` | PARTIAL | **w33, DEGRADED**: 503 "the field is not loaded" (the released answer with no field); the card drawer shows its error line |

### 1.7 Voice, feedback, the seat's JS, Blurp

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 69 | POST `/membrane/voice` (read the person's voice rows) | `panel.js:3832` | `PY:4836` | PARTIAL | spec `door_voice.json`, `N/tests/door_voice.rs`; **dry run never answers on a fresh store** (`sql()` over `c_entities`, `P/door_voice.c1f:37-38`) **w33**: no longer hangs: reads `c_field`, the seat's own table (the data migration brings the Python rows) |
| 70 | POST `/membrane/voice/state` | `home.js:4160` | `PY:4836` | PARTIAL | same, writes through the kind mirror; not run (a write) **w33**: no longer hangs (the page's boot call): same table, answers `{ok, state}` in the released shape (golden `voice_state.json`) |
| 71 | POST `/membrane/feedback` | `panel.js:3734` | `PY53:4876` | PRESENT | `P/door_self.c1f`; spec `door_self.json` |
| 72 | POST `/membrane/jsexec/register`, `/result` | `panel.js:3779,3824` (a per-window toggle, `panel.js:3769`) | `PY:4824` | MISSING | 501 (`P/door_self.c1f:14-16`); the model tool that needs it is a Python tool |
| 73 | GET `/membrane/self/card` | `home.js:2358`, HA `zoo.py:259` | `PY:4661` | NOT-NEEDED (default) | Blurp; the state family answers a presence stub (dry run 200) |
| 74 | GET `/membrane/self/sheet`, `/self/recent`, `/toybox/state`; POST `/toybox/poke` | `panel.js:3949,3961,3954,3933`, HA `zoo.py:264,268` | `PY:4639-4690, 4747` | NOT-NEEDED (default) | Blurp is off unless `MEMBRANE_BLURP=1` (`index.html:125`); **w46**: `/self/recent` answers `{lines: []}`, the released answer with no self record (`server.py:4577`); `/self/sheet`, `/toybox/state`, `/toybox/poke` stay an honest 501 (the oracle's sheet and dock are computed from the self record, energy and the toy files; the page reads a failure as "no sheet" and "no toys", `panel.js:3949-3961`) |

### 1.8 Home Assistant's own calls

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 75 | POST `/membrane/ha/token`, `/ha/states`, `/ha/catalog`, `/ha/mqtt`; GET `/membrane/ha/subscriptions` | `ha_access.py:201,394,346,384,307` | `PY:4850-4873`, `PY:4402`; `ha_bridge.py` (372 lines) | MISSING | dry run 404 |

### 1.9 The second port (:11500), not called by the UI

| # | method path | caller | shape pointer | cliffvm | evidence |
|---|---|---|---|---|---|
| 76 | POST `/v1/chat/completions`, `/v1/embeddings`, POST `/api/chat`, `/api/generate`, GET `/api/tags` | external OpenAI/Ollama clients (`docs/opencode-usage.md` "Pointing clients"), HA `config_flow.py:28` (`/api/tags`), the Python TurnRunner's own loop | `LN:4573-4700`, `LN` 8,014 lines | MISSING | decision D3: keep this port or retire it |

(Row 76 is five routes counted as one family row; see the counts below.)

### 1.10 Counts

Rows 76 (a row may hold several method+path pairs: row 39 has 6, row 35 has 5). Pairs 121.

| status | rows | method+path pairs |
|---|---|---|
| PRESENT | 22 | 30 |
| PARTIAL | 34 | 41 |
| MISSING | 14 | 40 |
| NOT-NEEDED | 6 | 10 |

By area (rows P/PA/M/N; recounted by script after w33): 1.1 shell 7/4/0/3; 1.2 session API and events 8/8/1/0; 1.3 opencode probes 1/2/6/0; 1.4 state, models, options 0/8/3/0; 1.5 decisions 5/3/1/0; 1.6 field map 0/7/0/1; 1.7 voice, feedback, self 1/2/1/2; 1.8 HA 0/0/1/0; 1.9 :11500 0/0/1/0.
Of the 30 PRESENT pairs, every one has a pack spec or a Rust test; the session routes and their event streams have a contract test against the Python response (`tests/golden/session`, section 7), and so do the page's boot routes since w33 (`tests/golden/boot`, `scripts/golden_boot.py`: /agent, options, quota, the empty map, the voice state); the rest are still to record.

## 2. UI inventory

Own JS/HTML in `membrane/ui/` (lines): `home.js` 4,912, `panel.js` 4,113, `field3d.js` 2,641, `field.js` 1,042, `decisions.js` 1,036,
`vosk.js` 815 (vendored), `fieldvm_core.js` 422, `fieldvm.js` 318, `fieldview.js` 204, `fieldanim.js` 196, `index.html` 165, `host.js` 94,
`favicon.js` 80, plus `three.min.js` (vendored) and the wasm (933 KB). Own code without vendored files: about 15,200 lines. The default
view is `?view=field`, the decision cockpit (`home.js:48-70`); `chat` and `graph` are the other two (`home.js:56-63`).

What the browser VM has today (`docs/cliffhanger-fields.md` section 9, `fieldvm.js:23-25`): view packs importing `dom`; `DomNode:<key>` with
tags from an allow-list of 27 (no `input`, `textarea`, `select`, `canvas`, `svg`, `img`, `audio`, `video`), attributes from an allow-list of 7
(`class title role hidden alt href aria-label`: no `value`, `placeholder`, `disabled`, `style`, `data-*`, `draggable`), text only, ONE event
(`clicked` on a node), the page's own DOM mirrored read-write, and "toward the seat the page is read-only" (a view pack cannot call a route; the
page follows the seat by `GET /ui/fieldvm` + SSE `field.write`, `fieldvm.js:274-300`). No fetch effect, no key/pointer/scroll/focus/drag
events, no canvas/WebGL, no list virtualization, no audio/mic, no timers beyond `clock`, no storage. Only one view exists as a pack: About
(`P/about.c1f`, test `T/js/about_view.test.mjs`).

| view | files, lines | what it shows | routes (section 1) | JS features it needs | VM needs (beyond today) | rounds |
|---|---|---|---|---|---|---|
| shell and login | `index.html` 165, `host.js` 94, `favicon.js` 80 | name prompt, token prompt, host object, favicon, theme | 5, 6, 10, 14 | `<form>`, `<input type=password>`, localStorage, focus, `fetchWithAuth` re-login on 401 | input element + value + submit event, focus, localStorage effect, fetch effect | 3-4 |
| chat (`ai-terminal-panel`) | `panel.js` 4,113 | session picker, transcript (text, reasoning, tool parts, markdown, code), composer with slash commands, model picker + think dial, quota popups, voice lines (teach up/down), feedback thumbs, predict ghosts, "Continue", JS-scripting toggle, 18 Blurp/self/toybox/skill-tree parts (`panel.js:647-1714`) | 15-31, 48, 50, 69, 71, 72 | SSE reader, streaming text append, `<textarea>`, keyboard (Enter, Shift+Enter, up-arrow history, `/`), scroll anchoring, virtualized long transcripts (cockpit paging, `panel.js:3612`), clipboard, `contenteditable` rewrite, timers | input/textarea/select + value, key/scroll/focus events, fetch + SSE effects, scroll/virtual list, timers, clipboard | 18-26 (minus 6-8 if Blurp parts are dropped) |
| decisions cockpit (`membrane-decisions`) | `decisions.js` 1,036 | stack of Decision cards, a graph of dependencies, goal ledger, answer / drop / chat per card | 52-53, 58-60 | SVG or absolutely-positioned layout (`DecisionLogic.place`, `decisions.js:369`), click, drag? (not checked), polling | `style`/geometry, pointer events, svg or canvas edges, fetch effect | 8-12 |
| home (`membrane-home-panel`) | `home.js` 4,912 (about 1,500 is voice, 700 is the 3D "brain" ball and Blurp bodies, the rest options / stats / pause / quota / zoo / rung celebration / menu) | top bar, burger menu with the three views, options window, stats window, pause button, quota badge, rung celebration | 41-44, 46-49, 12, 67, 73 | modals, `<select>`, `<input type=range>`, charts (stats), `requestAnimationFrame` for the ball | form controls, a draw primitive for the ball, windows | 14-20 (voice excluded) |
| voice | `home.js` 351-760, `vosk.js`, `voice-ml.mjs` | PTT, hotword, local whisper/vosk, browser TTS, mic | 69, 70, 11 | `getUserMedia`, AudioWorklet, WASM models, `speechSynthesis` | none feasible in a pack: keep as a JS island behind a bridge | 3-5 (bridge) |
| field map 2D (`membrane-field-panel`) | `field.js` 1,042 | the field laid out in 2D, search, entity cards, distance probe | 61, 66, 68, 32, 41 | canvas/SVG, hit-testing, pan/zoom, pointer events | canvas 2D or svg, pointer events | 7-10 |
| field 3D | `field3d.js` 2,641, `three.min.js` | tiles of the field on a sphere, avatars, camera orbit, search, IndexedDB tile cache, Blurp overlay | 62-65, 67, 30 | WebGL (three.js), IndexedDB, pointer/wheel/touch | keep as a JS island with a typed bridge; a pack cannot draw WebGL | 3-5 (bridge) or 25+ native |
| work graph and item card (`membrane-fieldview`) | `fieldview.js` 204 | a goal/task/wait graph, an item's history and mini-chat | 54-57 | SVG/DOM list, click, `<input>` for chat | input, fetch, list | 3-5 |
| boot animation | `fieldanim.js` 196 | the field animation while the seat loads | 13, 14 | canvas, rAF | canvas | 2-3 (or drop: cliffvm boots in 0.84 s) |

Rounds for the VM itself (needed before views 2-8): dom v2 (input/textarea/select/value/disabled/style, key/input/scroll/focus/pointer events,
writes from packs to the seat) 5-8; fetch + SSE effect for pages (a request and a stream as entities, like `Fetch` on the seat) 4-6; canvas 2D
draw entity 4-6; list virtualization (a windowed list node) 3-4; JS-island bridge for WebGL/audio (a typed message channel between a pack and a
named JS module) 3-4; storage effect 1-2. Total VM growth **20-30 rounds**.

Rounds, UI rebuild total: views 61-90 + VM 20-30 + test harness (a headless-Chrome driver exists: `T/js/fieldvm_link.test.mjs`) 3-5 + the two-pages
cutover switch 2-3 = **86-128**; the 94 existing node-based UI test files test the JS logic that the rebuild replaces: assuming about a quarter need a pack-test rewrite, add 26-41 for **112-169** (an assumption, the files were not read one by one).

## 3. Auth and persons

**What the released seat does** (`docs/auth.md`, `jwtauth.py` 650 lines, `seatauth.py` 259, `ui_serve.py`):
- JWT mode is ON by default: HS256, secret >= 32 bytes at `<seat>/.membrane/jwt.secret` (people) and `~/.membrane/jwt.secret` (the seat's internal token `seat:internal`, 1 h);
  `MEMBRANE_JWT_SECRET[_FILE]`, `_ISSUER`, `_AUDIENCE` override; `membrane token mint --sub --name --ttl` (`jwtauth.py:612`); `MEMBRANE_JWT=0` -> the **seat token** mode
  (`~/.membrane/seat-token`, Bearer or Basic password, loopback-only writes, `seatauth.py:138-212`).
- Credentials: Bearer, Basic (password = token; HA and `opencode attach`), the HttpOnly `membrane_jwt_<8 hex>` cookie from `POST /ui/login`
  (`jwtauth.py:467-495`, `UIS:334`). Public: the shell, `/ui/www/*`, `/ui/login` (the favicon too). In JWT mode a bare loopback request with no token passes as the seat's internal identity (`docs/auth.md`, Python seat); on cliffvm that is DROPPED (w32, below).
- Persons: the token's `sub` owns sessions (`GET /session`, `/session/status`, `/event` filter, `PY:4274-4294`); another person's session is a 404; the speaker line `[user#<sub>: <name>]` is stamped by the
  seat, client lines dropped (`jwtauth.py:580-611`, `UIS:106-175`); each person has ONE chat (#770 `readonly` mirror sessions, `PY53` `_message`); in the field the person is `person:<sub>`.
  HA does its own per-user ownership above this (`relay.py:112-125`, store `membrane.sessions`).
- Owner vs guest: the page asks `/ui/conf` -> `owner` (seat-token mode) or `who` (JWT); only the owner may POST models/options/seat control (`seatauth.py:166-212`, `PY` "owner's door above" comments; tests `T/test_owner_identity_966.py`, `test_ui_serve_owner_s1.py`, `test_no_tokenless_842.py`). The page served on loopback gets a minted owner cookie (`UIS:467-481`; the operator's "no tokenless" rule).
- LAN guard when JWT is off: private/tailnet addresses only, proxy headers get 403 (`UIS:75-100`).

**What cliffvm has:** `N/src/jwt.rs` verifies HS256 (`exp`, `sub`, 30 s leeway, <= 8192 bytes, min secret 32; same rules as Python), names the cookie from the
secret like Python (`jwt.rs:64`), reads Bearer / Basic / cookie (`N/src/http.rs:44-62`) and stamps `authed`, `sub`, `claims`, `auth_error` on the Request before a pack sees it
(`http.rs:138-147`); `P/door.c1f:63-114` adds the 401 gate and login/logout cookies. Dry run (w29, before w32) with a secret file: no token from loopback passed as `owner` (no longer, below); `X-Forwarded-For` -> 401; bad token -> 401; the shell stays public.
**Done in w30 (swap step 1, #1143)**: gap 3 (the 401 body and header are the released ones: `{"error":{"message":"missing or invalid token","type":"unauthorized"}}`, `WWW-Authenticate: Bearer realm="membrane"`, one message for a missing and a refused token), gap 4 (the owner cookie: `door_auth.c1f` + the driver verb `Request.mint`, `jwt.rs` `mint`), and the strict gate: a call through `/ui/serve/` needs a verified JWT even from loopback (the page is given one; no tokenless), while the unprefixed routes still pass a bare loopback caller. Gap 2 is closed by the operator's decision (seat-token mode and LAN guard dropped; JWT and the loopback owner cookie are the only modes). `scripts/seat-full` makes the secret (0600) on its first run. Still open: gaps 1 (no `token mint` CLI [DONE w34, `cliffvm token mint`], no internal token for the seat's own calls, no rotation), 5 (persons) and 6 (owner-only writes).

**Done in w34 (persons, #1143)**: `Person:<sub>` on the first verified request (gap 5, entities only: no session ownership, no speaker line yet), `Request.owner` = the fact `operator` (default `owner`), the `/mcp` writing tools (`entity_write`, `run_cliff`) owner-only with the released 403 error shape (gap 6 for `/mcp`; the oracle's JWT mode has NO owner-only write check, so this is stricter than v0.7.53; the other POST routes are not gated), `cliffvm token mint` (gap 1's CLI). `native/tests/persons_1143.rs`.

**Done in w32 (#1143, operator decision: a localhost peer is not the owner, #842)**: the bare-loopback pass is GONE. With a door secret EVERY route but the public shell (`/`, `/index.html`, `/membrane`, `/favicon.ico`, `/ui/www/*`, `/ui/login`, `/ui/logout`) needs a verified JWT, unprefixed routes included
(`/global/health` and the opencode probes are not public: the released JWT mode left only the shell and the login pair open, `ui_serve.py` `public_path`). The released seat's auto-made `~/.membrane/seat-token` is replaced by a JWT: `cliffvm run` makes the secret if missing (gap 1, secret generation: DONE) and writes a fresh owner token to `<seat_data_dir>/owner.token` (0600) at every boot
(sub = `operator`, life = the fact `owner_token_ttl_s`, default 30 days: flagged; rotation = the restart); the chat turn's CLI reaches `/mcp` with it (`headers` + `env_from`, never argv). The seat's internal token for its own other children (the TUI, peers) and `token mint` for other people stay open (gap 1). The barest seat (no secret) is unchanged: open by decision.
Tests: `native/tests/owner_token_1143.rs`, `door_auth_1143.rs`, `chat_process.rs` (the fake CLI), `seat_full_1143.rs`, specs `door_auth.json` and `door_conf.json`. Open for the operator: the `claude` CLI expanding `${VAR}` in a `--mcp-config` string is shown for its user config (`claude mcp add-json` then `mcp list`: the header arrived as `Bearer <value>`) and not yet for `--mcp-config` (no real run was made).

**Gaps for the swap:**
1. no secret management (generation at boot, `token mint`/`seat` CLI, the internal token for the seat's own calls and the TUI/MCP children, rotation): Python-only today (`jwtauth.py`).
2. the seat-token mode, the LAN guard and `auth: seat`/`owner` in `/ui/conf` (decide: drop, see D5).
3. 401 body and header differ from Python: cliffvm `{"error":"missing or invalid token"}` + `www-authenticate: Bearer` (`P/door.c1f:114`) vs Python `{"error":{"message","type":"unauthorized"}}` + `Bearer realm="membrane"` (`jwtauth.py:535-538`); some clients read the nested message.
4. the owner cookie minted for the page on loopback (`UIS:467-481`).
5. persons: no session ownership, no per-person chat, no speaker stamping, no `person:<sub>` entity convention in the c1f chat (the barest chat has one chat `membrane::Chat:main`).
6. owner-only writes (models/options/control/jsexec): "the door has no owner check yet" (`P/door_models.c1f`, `P/door_self.c1f:12`).

**Smallest explicit design (pack, no Python):** one pack `door_auth.c1f` + a launcher fact `door_secret_file` (exists, `facts.json`):
- `Person:<sub>` entities {name, owner: bool, chat} written on first verified request (a setter on `Request.authed`); `Seat:owner` = the first token minted or the `MEMBRANE_OPERATOR` fact; `Request.owner` getter = `sub == owner or internal`.
- `Request.session_visible` getter used by the session family: `Session.owner == this.sub or this.internal`.
- `Request.speaker` getter = `'[user#' + sub + ': ' + name + ']'`, written by the message setter.
- minting: a native `cliffvm token mint --sub --name --ttl` subcommand (the signer is already in `jwt.rs`), and the secret file created by `cliffvm run` when missing (0600). This is the only Rust, about 1 round.
- the 401 shape pack-side (`Request.gate` already a getter: change the body).
Rounds: pack + native subcommand + owner/guest gating + contract fixtures **5-7**; the seat-token mode, if kept (D5), +3.

## 4. Data

**Where the released seat keeps things** (`.membrane/` = `MEMBRANE_DATA_DIR`, default `./.membrane`, `settings.py:34`; `storekind.py`):

| store | where | format | holds |
|---|---|---|---|
| the field store | `<.membrane>/field/field.sqlite3` (SQLite default; `store.json` `{"kind"}`; `.cblite` legacy; `storekind.py:32,46-50`) | tables `c_<collection>` `(k TEXT PRIMARY KEY, body TEXT)`, body `{"o","d":{...}}` (`sqlitedb.py:287`, `P/store.c1f:11`); collections `events, sentences, terms, entity_states, entity_deltas, relations, claims, annotations, origin_stats, chunks, meta, entities, sessions, messages, files, recall_labels` (`storekind.py:36-38`) | the semantic field (recall), the entity store, chats and messages |
| sentence vectors | `<field dir>/vectors/sentences.f16` + `.keys` (`sqlstore.py` header) | f16 matrix, mmap | the recall index |
| settings | `<.membrane>/settings.json` (`settings.py`) | JSON | name, aliases, stt, voice_out, language, models picks, agent_writes, ... |
| people secret / seat token | `<.membrane>/jwt.secret`, `~/.membrane/jwt.secret`, `~/.membrane/seat-token` | text, 0600 | auth |
| router state | `<field dir>/router.json` (`opencode.py:1881`) | JSON | model scores, outages, spend |
| boards | `watches.json`, `intents.json`, `attention.json` (`opencode.py:1083,1111,1292`) | JSON | watch/intent boards |
| old sessions | `serve-sessions.json`, `serve-msgs.json` (+ `.jnl`) (`opencode.py:2246`) | JSON, imported to `c_sessions`/`c_messages` once | chats |
| ledger | `<field>.ledger.jsonl` (`opencode.py:1024`), `page-voice.log` | JSONL | the operator's decisions text, the page's voice log |
| global commons | `~/.membrane/global-field.json` (`opencode.py:1058`) | JSON | shared field |

**cliffvm's store** (`N/src/host.rs:27-30,239-262`, `db.rs`): ONE SQLite file (`--store`), the same `c_<coll> (k, body)` table shape and the same engine family, but its own collections:
`c_field` (eid -> `{cls, props, writers}` plus columns `cls`, `updated`) and `c_journal` (outside writes, replayable). It does not create or read the Python collections;
`sql()` (`P/store.c1f`) reads any `c_*` table read-only on the same file, so a legacy adapter can read the Python rows in place when the file is the same (or attach a second file; not checked).
Packs that already read legacy rows: voice (`c_entities`, `P/door_voice.c1f:37-55`), decisions (`c_entities`, `c_relations`, `c_entity_deltas`, `c_raw`, `P/door_decisions.c1f`, `N/tests/door_decisions_sql.rs`), the chat (`c_events`).
**Failure seen in the dry run:** those reads never answer when the table does not exist (fresh cliffvm store): rows 55, 56, 69 hang instead of returning an empty answer or 500.

**What a read-only legacy source adapter needs:** (a) a `Legacy` pack: `Legacy:<coll>.get(k)` over `sql()` returning the parsed `d`; (b) convert-on-touch: a getter-miss on `<Class>:<key>` reads
`c_entities`/`c_sessions`/`c_messages` and writes the c1f entity (idempotent, constructor by identity: `P/door_voice.c1f` already does this for voice, same recipe); (c) a one-shot reader of `settings.json` (needs the `fs` driver, exists) into a `Settings:seat` entity;
(d) the vectors file stays as is until recall is native (`N/src/recall.rs`, `topk.rs` exist; the f16 matrix reader is **not checked**); (e) the rename/backups: the old store is never written (WAL readers only, `P/store.c1f:7`).
Rounds: **8-12** for (a)-(c) over sessions, messages, entities, voice, feedback, settings; recall data (events/sentences/terms/entity_states) is a separate 4-6 behind the recall decision (D8).

**Entity classes in legacy data with no c1f class** (derived from `eids.py:34-38`, `storekind.py:36`, code greps; a real store was NOT read: the live seat is off limits):
- the semantic field: `w:` words, `p:` phrases, `f:` files, `s:` symbols, `c:` commands, `port:`, `m:` models, `u:` urls, `ha:`, `tool:`, `ctx:`, `person:`, `seat:` (`eids.py`): none has a class (`Word`, `Identifier`, `Path` exist only as text-span classes in `P/mentions.c1f`, not as stored field entities); the rows `entity_states`, `entity_deltas`, `relations`, `claims`, `annotations`, `origin_stats`, `sentences`, `terms`, `events` (recall) have no c1f form.
- chats: `c_sessions` (opencode Session dicts: slug, title, owner, readonly, model, agent, tokens, cost) and `c_messages` (`{info, parts}` rows) vs c1f `Chat`, `ChatMessage`, `ChatTurn`, `ChatResponse`, `ChatTool` (`P/chat*.c1f`): a different, richer-in-tools but one-chat shape; no `Session` class, no per-person chat.
- `Config:voice:in|out:<slug>` (read/written through a `Voice` kind in `P/door_voice.c1f`: has a class), `Config:recall_scorer`, `recall:dials` (read by `P/banner.c1f`), the `Component:*` entities (`files`, `toy`, `ha`: none), `tool:clock` and the watcher entities (`autonode.py`: no class), `Speaker`/`person:*` records, the `Message` rows the item chat refers to, the restarts' `restart:*` entities, `session:now`, `llm:*` (call-llm entity), the peer registry (`peers.py`: `Peer:channel` exists, the roster does not), the model router records (`Model:` exists; `router.json` scores do not), the `watch` entities (`entity_watch`/`field_watch`/`tracks`), `Goal`/`Task`/`Wait`/`Decision`/`Intent` (exist, `native/packs/goalgraph.c1f`, `P/decisions.c1f`, `P/intents.c1f`), `Blurp`/`self_*` (deferred by the operator), `Feedback` (exists).
Not checked: the exact set of classes in a real seat's `c_entities` (would need a copy of a live store).

## 5. Integrations

| item | used by | state on cliffvm | decision needed |
|---|---|---|---|
| Home Assistant integration (`custom_components/membrane`, 3,300 lines Python; `integrations/homeassistant/custom_components/membrane_ai`, a second, smaller copy) | HA users | needs rows 5, 14, 16, 24, 32, 41, 75; HA also runs the seat as its child process (`supervisor.py:178`) and proxies the page (`relay.py`, `views.py:23-25` rewrites `/ui/*` to `/api/membrane/*`) | port the 5 `/membrane/ha/*` routes + `ha_bridge.py` (372 lines) as a pack, or keep the HA component as is and make `serve_url` point at cliffvm (it speaks only the routes above): D6 |
| Voice | every page user with a mic | routes PARTIAL (rows 69, 70) and fresh-store hang; the voice itself is in-page JS (`home.js:351-760`) so it needs no seat; STT server path 501 | keep in-page, port only the two rows; STT via `Fetch` driver is possible (a WAV needs bytes in a request body: not supported, `P/door_voice.c1f:21-22`) |
| MCP as integration (servers the seat connects to) | model | `P/mcp.c1f` declares `McpServer`/`McpTool`/`McpResource`/`McpPrompt`; the I/O adapter is `mcpfield.py` (390 lines, stdio/http client): MISSING on cliffvm; the seat's OWN MCP door (`/mcp`, 4 tools) is PRESENT (`P/door_tools.c1f`, spec `door_tools.json`) | port the client driver (stdio spawn + JSON-RPC) as a Rust driver (4-6 rounds) or leave to Memo from inside via `Command` |
| Peers | other seats, `Peer:channel` | `P/peer.c1f` node PRESENT; `/membrane/peer/read`, `/peer/seats` (`LN:4619,4700`) and the roster (`peers.py` 788, `global_field.py` 190) MISSING | decide scope: D7 |
| Quota (`quota.py` 502) | chat pause badge | MISSING (row 48), `door_state` 501 | keep-list item "is quota dropped" still open: D4 |
| Router and models | model picker, minds | `Model:` entities, `Minds:seat`, `/v1/models`, `/membrane/models` present; scoring/ladder/outages/spend (`router.py` 1,057) not; keep-list says router remainder is a delete candidate | follow the keep-list: only the registry survives; D4 |
| The TUI / `opencode attach` | developers | the whole session API + event stream + 9 stubs MISSING (rows 15-40) | D2: is the TUI in the release? If yes all of 1.2-1.3 are needed anyway because the page uses the same API |
| Claude agent rung (`claude_agent.py` 1,337) | claude users | the barest chat runs the `claude` CLI (`P/chat_turn.c1f`): PARTIAL (one chat, no Continue/capped flow; **API-key default DONE (w45)**: the key of the seat's `ANTHROPIC_API_KEY` reaches the CLI in its environment, no key refuses the turn, `--fact chat_auth=login` is the explicit opt-in; docs/cliffvm.md "The claude turn's auth") | D9 |
| Ollama / cloud providers | local-model users | no model backend on cliffvm except via `Fetch` for `/api/tags` (`P/router.c1f`); the turn path (`rag.py` 469, lanes `/v1`) is Python | the biggest hidden item: D1 |
| Self/Blurp, toybox | off by default | NOT-NEEDED for the release (deferred, `P/door_self.c1f` 501) | confirm D10 |
| Watchers / thinker / dreamer / night / goal workers | background minds | Python (`watch_agents.py` 2,280, `night.py` 4,627, `thinker.py`, `dreamer.py`); only the boards (`Goal`, `Intent`, `Attention`, `Memory`) are c1f | out of scope of the swap unless D1 says the minds ship in the release: not checked in depth |
| Restarts (`restarts.py` 716) | HA/boat | MISSING | follows D6 |

## 6. Dry run (observed, read-only)

Build: `nice -n 10 cargo build --release --no-default-features --features bin` in `native/` (27 s, cached deps; `cliffvm 0.1.0`).
The brief's `membrane/boot.c1f` is a typo: the file is `membrane/packs/boot.c1f` (`cliffvm: membrane/boot.c1f: No such file or directory`).
Command: `cliffvm run membrane/packs/boot.c1f membrane/packs --store <fresh>.sqlite --port 4314 [--ui-dir membrane/ui]`.

**Packs.** All 33 `membrane/packs/*.c1f` (boot first, 32 above it: `about attention banner chat chat_turn decisions dev door door_decisions door_models door_self door_state door_tools door_voice entity fetch fs intents mcp memory mentions messages methods peer ported proc router semtext stats store trip turn`) loaded: the process came up and printed one line, `cliffvm: door listening on 127.0.0.1:4314`, with no load message and no `boot plan`
failure (a load error ends the process with exit 2, `N/src/cli.rs:83-123`). Shipped packs have no `Pack:<name>` entity (that is for model packs: `docs/cliffvm.md` "Pack entities"), so there is no `.state`/`.error` to read for them; `entity_query kind:Pack` lists only
`Pack:door_tools`, `Pack:unload` (the grants of `P/boot.c1f`); the singleton nodes the packs birth at start exist (`Attention:seat`, `Intent:board`, `Memory:seat`, `Peer:channel`, `Notify:seat`, `Methods:review`, `Command:git`, `Command:gh`: from `dev.c1f`/`methods.c1f`).
Time to first healthy answer 0.84 s on an empty store; RSS 13.9 MB. Failing packs: none. (`goalgraph`, imported by `decisions`, `intents` and `banner`, is built into the binary from `native/src/fields.rs`, not a file of this set.)

**What the full set needs to start usefully** (not to start: it starts bare):

| fact | who reads it | without it |
|---|---|---|
| `--ui-dir membrane/ui` | `door.c1f` static files | `/` and every `/ui/www/*` 404 (the full pack set has no `ui/` of its own; `apps/barest.c1f` carries the barest page) |
| `minds` | `router.c1f:67-95`, `door_models.c1f` | `/membrane/models` 503 (200 with `--fact 'minds={"loops":...}'`) |
| `seat_backend`, `router_on`, `cloud_models` | `router.c1f:16-60` | `/v1/models` lists only `@explore` |
| `door_secret_file` | the door's JWT verifier; `cliffvm run` makes the secret and the owner token file (w32) | no secret: no token check at all, every caller is `{"sub":"owner"}` (the barest seat, open by decision); with it: no token = 401, loopback included |
| `seat_data_dir` | `door_voice.c1f:23` | `/ui/log` cannot write |
| `seat_stt`, `seat_project`, `nerve_pause_off`, `ledger`, `agent_writes`, `chat_claude_bin` | conf, probes, pause, decisions, memory, chat | defaults: `stt browser`, project null, ... |
| a writer of `membrane::presence` (state, events) | `door_state.c1f` | state routes answer empty (`{"seat":{}}`); controls only record |
| the Python store's tables (or an empty-table fallback) | voice, entity-history, item-chat | those requests never answer (rows 55, 56, 69) |
| a writer of `Intent:board.active` (the dream loop) | `door_decisions.c1f:21-22` | goals unranked |

**GET replay** (85 requests, the used GET routes of section 1 and their stubs; no writes; `curl -m 3`; fresh store, no `--ui-dir`; raw table in the scratchpad `w29/gets_result.tsv`):

| status | count | which |
|---|---|---|
| 200 | 16 | `/ui/conf`, `/global/health`, `/v1/models` (empty), `/membrane/state`, `/membrane/seat`, `/membrane/seat/pause`, `/membrane/self/card` (stub), `/membrane/work-graph`, `/membrane/decisions`, `/membrane/goals`, `/command`, `/path`, `/project`, `/project/current`, `/vcs`, `/membrane/chat/messages` |
| 501 | 6 -> 2 after w46 (`/membrane/minds`, `/membrane/quota`, `/membrane/stats`, `/membrane/self/recent` now 200) | `/membrane/self/sheet`, `/membrane/toybox/state` (each with its reason) |
| 503 | 1 | `/membrane/models` (no minds fact) |
| 405 | 1 | `GET /mcp` |
| no answer (curl timeout) | 2 | `/membrane/entity-history`, `/membrane/item-chat` |
| 404 | 59 | the page (`/`, `/index.html`, `/favicon.ico`, `/ui/www/*` 18, no `--ui-dir`), `/ui/fieldvm`, `/ui/zoo`, `/__boot/shape`, `/ui/serve/*`, `/session*` (7), `/event`, `/global/event`, `/agent`, `/config*`, `/provider*`, `/experimental/*` (4), `/lsp`, `/formatter`, `/api/*` (3), `/membrane/options`, `/membrane/map/*` (7), `/membrane/field/entity`, `/membrane/ha/subscriptions` |

With `--ui-dir membrane/ui`: `/`, `/index.html`, `/favicon.ico`, `/ui/www/*` serve 200 for 16 files, including `three.min.js` (603 KB) and `vosk.js` (5.8 MB); `voice-ml.mjs` and `vosk-model-en.tar.gz` 404 (not in `membrane/ui`).
With a secret file (w29, before w32): no token + loopback -> 200 as `owner` (now 401, see section 3); `X-Forwarded-For` -> 401; a malformed token -> 401; the shell with a forwarded header -> 200 (public).
Read-through of one write-free POST: `POST /membrane/voice` never answers on a fresh store; `POST /ui/login` with a bad token answers 401 (`{"error":"missing or invalid token"}`).
The seat was stopped afterwards (SIGTERM, port 4314 free). The operator's seat on :4096 and the scratchpad `real-run/seat*.sqlite*` were not touched.

**After swap step 1 (w30, `scripts/seat-full`, `tests/js/page_boot.test.mjs`, measured in headless Chrome, 8 s after load).** The page loads all its files, runs with no script exception and makes 13 `/ui/serve/` calls on boot, none refused (the owner cookie is accepted on every one):

| answered | calls |
|---|---|
| 200 | `membrane/seat/pause`, `membrane/work-graph`, `global/health`, `membrane/models`, `v1/models` (and the bare `/ui/conf`, `/global/health`; with the owner token since w32) |
| 501 (a stated "not built") | `membrane/quota` |
| 404 (no route) | `/ui/serve/session` (row 15: w31), `/ui/serve/agent` and `/ui/serve/config` (rows 36, 37: the opencode stubs), `/ui/serve/membrane/map/field3d` and `/map/blurp` (rows 61-68: the field map), `/ui/fieldvm` (row 7), the bare `/membrane/options` (`index.html:78`, row 44) |
| no answer (hang) | `POST /ui/serve/membrane/voice/state` (row 55: the voice tables a fresh store lacks) |
| not reached yet | `/ui/serve/session/status` and `/ui/serve/event` (the page only asks once a session exists), so the chat screen is not testable until w31 |

These are the backlog; each is listed in the test (`MISSING`, `HANGS`), which fails when a call outside the list 404s or hangs and when a listed one starts to answer.

**After swap step 3 (w33, `native/tests/boot_routes_1143.rs`, `tests/js/page_boot.test.mjs`, `field_empty.test.mjs`, `fieldvm_snapshot.test.mjs`).** The page's boot makes 34 `/ui/serve/` calls in 8 s and every one is answered: no hang, no 501, one 404 left. Each answer was made in c1f from the released handler (tag `v0.7.53`), with a golden fixture recorded from the released code (`scripts/golden_boot.py`, `tests/golden/boot/`; shape only):

| call | now | what is missing / degraded |
|---|---|---|
| `POST /membrane/voice/state` (was a HANG) | 200 `{ok, state}` | the voice routes read `c_field` (the seat's own table); the Python seat's `c_entities` is for the data migration; found and fixed the same hang in `entity-history` and `item-chat` (`c_entity_deltas`, `c_relations`) |
| `/agent`, `/config` | 200, the five released agent rows; the opencode config naming the model | no live mind rows in `/agent`; `PATCH /config` not built; `/config/providers`, `/provider*`, the other TUI probes still 404 (TUI only) |
| `/membrane/options` | 200 GET and validated POST | `self_cpu_share` and `code_runs_keep` are kept (`Settings:seat`) and read by nothing here; an unknown key is refused without its name; `energy` is `null`; `chat_auth` is the fact `chat_auth`: `api_key` by default, `login` when the seat is started with the opt-in (w45) |
| `/membrane/quota` (was 501) | 200 `paused: []`; `POST /resume` 200 `resumed: false` | no model is ever paused here; `/membrane/stats` and `/membrane/minds` stay 501 (tokenomics, loop sessions: Python state; the stats window shows "the seat did not answer") |
| `/membrane/map/field3d`, `/blurp`, `/field`, `/tile`, `/search` | the released answers of a field with no live entity; both views read "the field is empty" | DEGRADED on purpose (the operator's decision): no map, no search hit; `locate` is a reasoned 404 ("not in this projection", the page's `_trackNb` catches it, so it stays on the test's `MISSING`), `distance` and `field/entity` 503 "the field is not loaded"; the engine is a read-only Python bridge later, native recall after |
| `GET /ui/fieldvm` | 200 `{packs: {about}, entities: []}` | `entities` is empty (nothing feeds `About:seat`: the About window has nothing to draw); the `field.write`/`field.pack` frames are not on `/ui/serve/event`; a model-written Pack is not sent; read once at boot |
| `/session/<sid>/voice`, `/predict` (tolerated 404s) | 200 `[]` (also `todo`, `children`, `diff`) | POST `/voice` (the vote / rewrite teaching) is not built |

**After w46 (the degraded routes; `native/tests/boot_routes_1143.rs`, `tests/specs/door_state.json`, `door_self.json`, `tests/js/stats_empty.test.mjs`, `chat_submit.test.mjs`).** Release definition (operator): the released UI runs unchanged; where cliffvm has no backend, a route answers the oracle's honest EMPTY shape, never a 501 where the page tolerates the empty shape. TUI rows (29, 34-40, `/tui/*`) are OFF the release and stay unbuilt. Oracle = tag `v0.7.53`; goldens `tests/golden/boot/{stats,minds}.json` are written by `scripts/golden_boot.py`.

| route | oracle shape (v0.7.53) | before | after |
|---|---|---|---|
| GET `/membrane/stats` | `tokenomics._assemble` of nothing, `server.py:4559` | 501 | 200, the empty window (golden `stats.json`) |
| GET `/membrane/minds` | `{loops: {}, thinker: [], dreamer: []}`, `lanes.py:4606-4609` | 501 | 200 (golden `minds.json`) |
| GET `/membrane/self/recent` | `{lines: []}` with no record, `server.py:4577` | 501 | 200 |
| POST `/membrane/item-chat/say` | a write (`server.py:4889`): no empty shape | 404 | honest 501 `not ported yet`, writes nothing |
| GET `/membrane/quota`, `/quota/resume`, `/membrane/options`, the map routes, `/agent`, `/config`, `/ui/fieldvm`, session voice/predict | answered in w33 (above) | 200 | unchanged (rows 7, 32, 33, 46, 48, 61-68) |
| GET `/membrane/state`, `/seat`, `/seat/pause`, `/self/card` | answered; the presence entity is unwritten, so `seat` is `{}` (the oracle's own no-seat answer for `/membrane/seat`) | 200 | unchanged (rows 41-44) |
| GET `/membrane/state?section=`, `/membrane/self/sheet`, `/membrane/toybox/state`, `POST /toybox/poke`, `/jsexec/*` | computed from Python state, no honest empty | 501 | honest 501 (the page tolerates a failure on each: no sheet, no toys; `?section=` and `jsexec` are not called by the page's boot) |
| `/membrane/seat/event`, `/seat/restart-ask` (HA only), `PATCH /config`, `/membrane/{predict,gate-stages,entity-track,intents,coproc,map/cloud,map/entities}` | HA or dev-only: the page never calls them | 404 | 404 (not part of the page; not built) |
| `/membrane/map/locate` | 404 `<eid> is not in this projection` (found, w33) | 404 | 404, the one tolerated 404 (`page_boot.test.mjs MISSING`) |

The page's send box: `submit()` in `membrane/apps/barest.c1f/ui/chat.js` now holds a `sending` flag (cleared when the POST answers or fails; the input and the button are disabled meanwhile), so a slow seat gets one POST per message (`tests/js/chat_submit.test.mjs`).

**Settings (`--fact name=value`, approved).** The constants carried over from the release are launch facts of the packs that serve them; without the fact the released value stands (so `scripts/seat-full` does not need to pass them). Registered in `membrane/packs/facts.json`, tested in `the_released_constants_are_launch_facts_...`:

| fact | default | meaning |
|---|---|---|
| `quota_probe_s` | 600 | the quota answer's `probe_s` (released `quota.PROBE_S`): how often a paused model would be probed; nothing is ever paused here |
| `map_tile_k` | 150 | `tile_k` of the empty 3D projection (released field3d tile size) |
| `field_tau_s` | 180.0 | the field current's fade, seconds: `current.tau_s` of the projection and the read-only `current_tau_s` option |
| `self_cpu_share_default` | 0.5 | the `self_cpu_share` option's default and `defaults` entry (kept, read by nothing here) |
| `code_runs_keep_default` | 30 | the `code_runs_keep` option's default and `defaults` entry (kept, read by nothing here) |
| `chat_auth` | `api_key` | `options.background.chat_auth` and how the claude turn authenticates: `api_key` (default, the release gate) or `login` (the released constant, now the explicit opt-in) |

**Choices left to the operator (not made here):**
- **A. Stats window: zeros or an honest 501?** Built as zeros (the window draws a full panel of 0s and "no Claude answers since the seat started"). That is a true count of what this seat counted (nothing) but reads like "no usage" on a seat that did chat. The 501 shows "the seat did not answer". Pick: zeros until a token counter exists. Also `midnight` is the local midnight to the minute (the `localtime` builtin has no seconds; the page does not read it).
- **B. What feeds the About window?** `/ui/fieldvm` `entities` stays `[]`: nothing writes `About:seat`. The window opens empty. Candidates: the seat's version/build, the model, the data dir (facts the launcher already knows), written as `About:seat` by the launcher. Not invented here.
- **C. The seat panel** (`/membrane/seat`, `/membrane/state`) answers empty because nothing writes `membrane::presence`; the oracle's seat panel carries paused, loops, models, memory. Who writes it (a pack on the heartbeat) is open.
- **D. `/membrane/self/sheet` and the toy dock** answer 501 (Blurp is off the release, D10); the oracle's empty sheet is a large dict computed from the record, so an empty shape would be an invention.

Left on the page's boot: `GET /membrane/map/locate`, 404 by design (above). Page code was not changed. Builtins the work wanted and did not get (each is a proposal in `docs/c1f-stdlib.md`): `dict(rows)` (the models row of `/config` is read from JSON text, the `packs` dict by a `json_group_object` sql), `keys(d)` (unknown option names), `urldecode` (only `+` and `%20` are decoded in the map's query).

## 7. Server-swap test plan

What can run against cliffvm **unchanged**:

| suite | size | runs against cliffvm? | why |
|---|---|---|---|
| `tests/js/*.test.mjs` (fieldvm core, link, clock, about) | 5 files | yes, already; they test the wasm engine and the page link, not the seat | server-independent |
| the 94 Python test files that run `node` on the page's JS (`grep -l '"node"' tests/test_*.py`) | 94 | yes: they test the UI logic (`HomeLogic`, `DecisionLogic`, `AiPanelLogic`), and the swap keeps the JS | server-independent; they prove "the page did not change" |
| `native/tests/*.rs` (78 files) and `tests/specs/*.json` (18) | 96 | yes (they ARE cliffvm); but the specs feed packs a `Request` entity, they prove a pack's logic, not that the live seat produces the Python shape | they cover rows 1-3, 5, 6, 10, 14, 39, 50-53, 58-60, 71 |
| `tests/test_server.py`, `test_ui_serve*.py`, `test_serve*.py`, `test_jwt_*.py`, `test_offbox_auth.py`, ... (27 files use `make_handler`) | 27 | **no, not unchanged**: they start the Python handler in-process with a `_StubRunner` and a fake store (`T/test_server.py:617-633`) | they can be made to run against a base URL (`MEMBRANE_TEST_BASE`) only for the assertions that do not need the stubs: boot probes, session shapes, event frames (about 30 tests of test_server.py by estimate; not counted) |
| the other 600+ Python tests | ~600 | no: they test Python modules that the swap deletes or keeps behind the door | deleted with their module (AGENTS.md rule 2) |

**Contract test against a recorded Python response** (same idea as `tests/golden/efield`, `scripts/golden_efield.py`):
1. `scripts/golden_routes.py` starts the Python handler in-process with the stub runner and a seeded fake store (as `test_server.py` does), sends the 76 rows' requests (GET and the harmless POSTs) with injected clocks/ids, and records `{request, status, content-type, normalised body}` to `tests/golden/routes/<area>.json`; volatile fields (ids, times, cwd, version) are named in a `volatile` list per fixture, exactly like `golden/efield/README.md` names its tolerances. `--check` fails on drift (so the Python side stays the oracle while it exists).
2. `native/tests/route_contract.rs` (or a spec runner extension) boots `cliffvm run ...` on an ephemeral port with the same seed (sessions, messages, models, minds facts), replays every fixture and compares status + body with the volatile fields masked + shape (keys and types) for the fields the page reads. The page-read fields are known: they are the `.data.<field>` reads in the JS (row pointers above), so the comparison list can be generated by grepping the JS, not guessed.
3. SSE fixtures: the first N frames of `/event` for a scripted turn (the stub runner's), compared frame by frame (`type`, `properties` keys).
4. A page smoke test with the real JS: headless Chrome (the driver exists, `T/js/fieldvm_link.test.mjs`) loads `/`, logs in with a minted token, creates a session, sends a message with a fake model, expects the transcript row and no console error; the same script runs against the Python seat (oracle) and cliffvm.

**Smallest first slice** (about 8 rounds, proves the architecture without a model): `cliffvm run packs + --ui-dir membrane/ui` serving the real page in headless Chrome up to the empty chat screen: (1) the `/ui/serve` prefix strip (row 9: PRESENT, w30); (2) `door_opencode.c1f` with the stub rows 32-40 (+ `GET /mcp` split by method) and `/session` list/create/get/delete/status + messages as `Chat:<sid>` entities over the existing chat, no turn yet (rows 15-22, 28); (3) `/event` SSE emitting `server.connected` + `session.*` from the existing event-stream mechanism (rows 30, 31); (4) the golden fixtures for exactly those rows (about 25 requests) and the Chrome smoke "page loads, no console error, session list renders, a created session appears". The first real turn (row 23) is slice two (the existing `chat_turn` over a `claude` fake, as `N/tests/chat_process.rs` does).

## 8. Prioritised plan, risks, decisions

Rounds per phase (1 round = 20-45 min; ranges are low = known mechanism reused, high = a new mechanism needed):

| phase | work | rounds |
|---|---|---|
| 1a prefix, probes, `/mcp` split, conf parity, 401 shape, static assets from two roots | rows 1-5, 9, 13, 14, 32-40 | 5-7 |
| 1b auth and persons | section 3 | 5-7 (+3 if seat-token mode) |
| 1c session API on chat entities: sessions, messages `{info, parts}`, per-person visibility, one chat per person | rows 15-29 | 12-18 |
| 1d `/event` + `/global/event` with opencode frames and per-person filtering | rows 8, 30, 31 | 5-8 |
| 1e state, controls, options, stats, quota, minds (a presence writer pack, settings via `fs`) | rows 41-50, 54 | 10-16 |
| 1f field map routes (8) over the Python engine through a bridge, or a native map | rows 61-68 | bridge 4-6; native 15-25 |
| 1g model backends (Ollama, cloud providers, claude API key default) behind the turn | row 23, 76, D1/D9 | 14-22 |
| 1h HA routes and bridge | row 75 | 4-6 |
| 1i item-chat say, work-graph watch nodes, empty-table safety in the sql packs | rows 55-57, 69, 70 | 3-4 |
| 1j packaging: wheels or the launcher fetching a `cliffvm` per platform, `membrane standalone` starting it, HA add-on, Termux/aarch64 | | 6-9 |
| 1k contract harness (section 7) | | 5-7 |
| **swap total** (1f as the bridge) | sum of 1a-1k | **73-110** (1g is 14-22 of it; a first swap with the claude CLI as the only model, 1g reduced to the API-key default 3: **62-91**; a native map in 1f instead of the bridge adds 11-19) |
| 2 no-history chat behind the same routes (the map banner `banner.c1f` + `recall.rs`, momentum map, `Banner:<owner>:<chat>` per person per chat, per-person turn, Continue/cap flow, the `previous_value` re-answer door) | | 8-12 |
| 3 UI rebuild | VM growth 20-30, views 61-90, harness 3-5, switch 2-3, tests 26-41 | 112-169 |
| 4 data migration and cutover | adapter 8-12, recall data 4-6, cutover (flag, rollback by journal replay, dual-run soak) 2-4 | 14-22 |

Dependencies: 1a -> 1c -> 1d -> 1k first slice; 1b before 1c's visibility; 1g before any non-claude user can swap; 2 needs 1c; 3 needs 1d (event stream) and the VM growth, and can proceed per view once 1a-1d exist; 4 can start after 1c (sessions/messages are what a user sees first) and must finish before cutover, not before the swap (the swap can run on a fresh store only for new users; users with a store need 4).

### Top risks
1. **Model backends.** The released seat serves local Ollama and cloud providers through the Python turn path (`rag.py`, `lanes /v1`, `claude_agent.py`); cliffvm's only turn is the `claude` CLI. A swap that is "unnoticed" is impossible for Ollama users until 1g (14-22 rounds). Undetected by the specs (they feed packs, not models).
2. **The session/event API is a third-party contract** (opencode SDK shapes, captured by hand: `T/test_server.py:831`, `PY:4470-4486` comments about TUI crashes). Every missing field crashed a TUI once. Only recorded fixtures from the Python oracle protect it; the TUI's real traffic was not recorded here.
3. **Reads that hang** on a store without the legacy tables (3 rows in the dry run) and routes that answer from state nothing writes (`membrane::presence`): they pass the pack specs and fail live. Needs the contract harness before any claim of parity.
4. **Browser VM is read-only and tiny** (27 tags, 7 attributes, one event, no fetch/canvas): phase 3 is the largest and least known item; WebGL and audio cannot be packs. Estimate has the widest range (112-169).
5. **Packaging and platforms.** Users install a Python wheel (`pyproject.toml:36-38`, `setup.py` builds `membrane_vm` with setuptools-rust); cliffvm is a separate binary (`native/Cargo.toml` `[[bin]]`, feature `bin`). HA's supervisor starts the seat as a child (`supervisor.py:178`); the boat's phone is Termux/aarch64 (`integrations/homeassistant/deploy/brain`, `deploy/agdroid`). A binary per platform and HA/Termux delivery are not designed.
Also: the release baseline moves (HEAD already deleted Python decision routes that `v0.7.53` serves: the "released" Python seat and HEAD's Python are not the same oracle; record fixtures from the tag).

### Decisions for the operator (each with my pick)

1. **D1 Model backends in the release scope?** Options: (a) claude CLI only at the swap, Ollama/cloud later; (b) port the provider turn path (Ollama + cloud, 14-22 rounds) before the swap; (c) keep Python as the model bridge (the cliffvm door forwards turns). Pick **(b) for Ollama, claude API-key default per the release gate; cloud providers after**, because "users do not notice" fails the first time an Ollama user's chat is dead. (c) contradicts "no forwarding to Python".
2. **D2 Is the opencode TUI attach surface part of the release?** Pick **yes, as a thin set**: the page uses the same session/event API (rows 15-31), so the cost is the 9 stubs (1-2 rounds) and the fixtures; drop only the `/tui/*` beyond a 200 `true`.
3. **D3 Keep the :11500 OpenAI/Ollama proxy port?** Pick **keep, last** (after the swap), as a door family `v1`: external clients and HA's `/api/tags` use it; but no UI calls it, so it must not block the swap.
4. **D4 Quota, router remainder and the "out of tokens" badge.** Pick **drop quota and the router remainder at the swap** (the keep-list direction, the badge is the largest UI-visible removal): rows 48 and the router scoring leave the used set; the `Model:`/`Minds:seat` registry stays. This removes `panel.js` quota popups (41 refs) and `home.js` badge (27 refs): confirm they may go before the UI is rebuilt.
5. **D5 Auth modes.** Pick **JWT only** (the default today) + the loopback owner cookie; drop the seat-token mode and the LAN guard (saves 3+ rounds); users on `MEMBRANE_JWT=0` get one release of notice. Needs your explicit yes (it changes who can reach the seat).
6. **D6 Home Assistant.** Pick **keep the HA component unchanged and port only its 5 `/membrane/ha/*` routes** as a pack (4-6 rounds); the add-on runs cliffvm as the child. The alternative (leave HA to Memo from inside) leaves the HA users with a dead integration at the swap.
7. **D7 Peers and restarts.** Pick **drop from the swap, port after**: no UI row needs them; `Peer:channel` stays.
8. **D8 Recall engine in the first swap.** The map/recall data (events, sentences, entity_states, vectors) is the field's memory. Pick **swap with the Python field engine reachable read-only as the map bridge (1f bridge) and recall native only in phase 2**; a native map is 15-25 rounds and nothing in the page's first screen needs it (the default view is the decision cockpit).
9. **D9 Claude API-key default** (the release gate): confirm it is a swap blocker, not a phase-2 item. Pick **blocker**.
10. **D10 Blurp/self/toybox.** Pick **out of the release** (off by default; 18 page parts can be deleted from `panel.js` before the rebuild, saving 6-8 rounds).
11. **D11 Page parity vs honest degrade.** The page tolerates a 404 from `/ui/zoo`, `/__boot/shape`, `/predict` but not from `/session` or `/event`. Pick **a "full or nothing" rule for rows 15-31 and 41-44, and a documented degrade for the field-map rows 61-68** (the 3D/2D windows show an "engine not ready" card instead of failing): what the page does on a 404 there is **not checked** and must be tried in the harness.
12. **D12 Baseline for the oracle.** Pick **the `v0.7.53` tag** (not HEAD) as the recorded oracle for fixtures, and freeze the Python route set at it.

