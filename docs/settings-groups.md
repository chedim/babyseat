# Settings window: the groups, the inventory and the write path (#1143)

Design only, no code. Tree `memo-hotfix-0753` (`af7e506c`). Every number or default I would add is listed as a decision at the end; none is picked here.

**Important first.** Of the runtime settings the old window had, only ONE still does anything on this seat: the chat's model (`Minds:seat.loops`). The rest of `/membrane/options`
(`self_cpu_share`, `code_runs_keep`, `cortex`, `workers`, the `*_auth` picks, `think`) is stored and read by nothing (`door_models.c1f:98-111`, `router.c1f:73-96`; grep of the packs: only
`chat.c1f:30` and `ha.c1f:115` read `Minds`). A window that shows them lies. They stay out until something reads them (decision 1). Everything the launcher tells (`--fact`) is launch-only:
`Boot` is VIRTUAL (never stored, never journalled; `docs/cliffvm.md` "Storage modes"), so it comes back from the launch line on every start.

## Rules (operator, 2026-11-14)

- Everyone who has access is the owner. The window is the same for everyone: no owner-only variant, no per-person or per-device setting (no personal theme, no per-person voice).
- The server's own owner check stays as it is (`door.c1f:71-73` `owner` / `forbidden`; `door_tools.c1f:42-44` `mc_deny`). A write the seat refuses is shown as text, with its reason, in place; nothing is hidden.
- Quota is OUT of the first release (`/membrane/quota`, `quota_probe_s`, `door_state.c1f:19-24, 79`): no row.
- No secret is ever shown. A key or token appears only by the NAME of its environment variable (`ANTHROPIC_API_KEY`, `api_key_env` names), or as yes/no (`anthropic_key_set`).

## What is NOT a setting (and why it is not in the window)

| thing | where | reason |
|---|---|---|
| `cortex`, `workers`, `loops_auth`, `cortex_auth`, `workers_auth`, `think` | `Minds:seat` (`router.c1f:76`), written by `POST /membrane/models` and `/membrane/options` | stored, read by nothing on this seat (no loops, no cortex, the turn is the claude CLI) |
| `self_cpu_share` (default 0.5), `code_runs_keep` (default 30) | `Settings:seat` (`door_models.c1f:112, 123-124`) | "NOTHING in this seat reads the two writable values yet" (`door_models.c1f:107`) |
| `current_tau_s`, `energy` | `/membrane/options` | read-only in the old window too; `energy` is null here |
| pause, `loops_max`, `self`, `accept`, `self_name` | `POST /membrane/seat/control`, `/seat/pause` (`door_state.c1f:57-62`) | the door only records the ask on `membrane::presence.control` and answers 202; nothing consumes it ("THE GAP", `door_state.c1f:11-14`) |
| `ui_views` | fact; `GET /ui/fieldvm` `views` (`door_fieldvm.c1f:60`) | the switch between the old and the pack renderer; the UI is rewritten completely, so it has no meaning after the swap |
| per-person voice (`Config:voice:in|out:<slug>`: on, mode, device) | `POST /membrane/voice/state` (`door_voice.c1f`) | per-person: excluded by the operator's rule |
| the page's name prompt, `membrane.chat.sid`, `membrane.home.left`, `dockedSidebar`, `sidebarPanelOrder`, `sidebarHiddenPanels` | browser `localStorage` (old `host.js:20`, `panel.js:2674`, `home.js:3105`) | per-device; no storage effect in the browser VM yet (W6, `ui-rebuild-plan.md`) |
| a chat's own model pick | `Chat:session:<sid>.smodel`, `door_session.c1f:149-164` | an act in the chat, not a setting |
| the banner `text`, the doctrine/system prompt | `Banner:owner:main.text` is the model's (`default_banner.c1f`, `Help:banner`); the prompt is pack code (`chat_turn.c1f:90`) | replaced by writing a pack, not by a value |
| session titles | `PATCH /session/<sid>` | a chat action |

## Groups and order

Order is by how often a person reaches for it. Names are one plain word.

1. **Model** - which model answers. The one live setting that matters.
2. **Chat** - how a turn runs and how the chat looks.
3. **Map** - what the field view shows.
4. **Home** - what the model may do in the house (allow-list).
5. **Import** - old history and second databases.
6. **Access** - who the owner is, how tokens work (read-only).
7. **Limits** - machine limits set at launch (read-only, "restart needed").

Reasons: Model/Chat first, they are the daily questions. Map/Home are the other things the operator tunes at runtime. Import is rare. Access and Limits are information plus the launch line that changes them, so they go last and carry no edit control.
Help text below is domain-neutral (no product names; `help_1143`).

## The inventory

Columns: **key**; **lives**; **type / values**; **default**; **change today**; **live?**; **journalled**; **secret**. Who may change it is the same for all rows: everyone with access; the server's owner check answers 403 with the reason, shown as text.

### Model

| key (label) | lives | type / values | default | change today | live | journalled | secret |
|---|---|---|---|---|---|---|---|
| `model.chat` ("Model": "The model that answers your messages") | `Minds:seat.loops` (RESIDENT, `router.c1f:6, 76-96`); read by `Chat.seat_model` (`chat.c1f:30`) and the HA select (`ha.c1f:115`) | one name of `pickable` (the local tags that pass `chat_ok`, plus listed claude-agent rungs, `door_models.c1f:41`), a model name of 1..120 chars from `[a-z0-9_.:/@~+-]` (`door_models.c1f:48-55`); `''` = not picked | `''`, then the launcher's `seat_default`, then `claude` (`chat.c1f:27-30`) | `POST /membrane/options {"loops": ..}` or `/membrane/models` (owner, `door_models.c1f:64-68, 181`); or `entity_write Minds:seat.loops` (owner) | yes: the next message reads it | yes (outside write; the journal does not say who) | no |
| `model.default` ("Fallback model") | `Minds:seat.seat_default` | model name | `claude` | only the launcher's `minds` fact seeds it (`router.c1f:77-96`, once, while no `Minds` entity exists); no route writes it | no route | - | no |
| `model.list` (read-only list "Available models") | `Model:*` (`router.c1f:60-70`), `GET /v1/models` | rows | - | the backend's tag list and `cloud_models` | live read | - | no |
| `model.cloud` ("Other providers") | fact `cloud_models`: list of `{name, backend, base_url, api_key_env, chat_path?}` (`chat_oai.c1f:10, 35, 44`) | list | none | launch only (`--fact cloud_models=..`) | LAUNCH-ONLY, restart needed | no | the key is read from the env var NAMED by `api_key_env`: show only that name |
| `model.signin` ("Claude sign-in": `api_key` or `login`) | fact `chat_auth` (`chat_turn.c1f:37`), shown as `options.background.chat_auth` (`door_models.c1f:121`) | `api_key` / `login` | `api_key` (the release gate) | launch only (`--fact chat_auth=login`, or `CLAUDE_AUTH=login` of the scripts) | LAUNCH-ONLY | no | the key itself is `ANTHROPIC_API_KEY` in the seat's environment; `anthropic_key_set` says yes/no |
| `model.cli` ("Claude program path") | fact `chat_claude_bin` (`chat_turn.c1f:34`) | path | none (no path = the turn answers with an error that says so) | launch only | LAUNCH-ONLY | no | no |

### Chat

| key | lives | type / values | default | change today | live | journalled | secret |
|---|---|---|---|---|---|---|---|
| `chat.max_steps` ("Most steps per answer": "How many tool steps one answer may take") | fact `chat_max_turns` (`chat_turn.c1f:46`; read by a getter on `Boot`) | whole number >= 1 | 2048 (a pack default, "a threshold for the operator") | launch only | LAUNCH-ONLY today; a getter, so an override entity makes it live (item A) | - | no |
| `chat.timeout` ("Answer time limit", seconds) | fact `chat_turn_timeout_s` (`chat_turn.c1f:51`) | number > 0 | 21600 (6 h) | launch only | as above | - | no |
| `chat.page_size` ("Turns loaded at a time") | fact `chat_page_size` -> `Boot:seat` in the `/ui/fieldvm` snapshot (`door_fieldvm.c1f:60`) | whole number >= 1 | 20 | launch only | read when the page loads | - | no |
| `chat.poll_size` ("Turns re-read while waiting") | `chat_poll_size` | whole number >= 1 | 10 | launch only | page load | - | no |
| `chat.poll_idle_ms` ("Check for news every", ms, when idle) | `chat_list_poll_ms` | number | 3000 | launch only | page load | - | no |
| `chat.poll_busy_ms` ("Check for news every", ms, while answering) | `chat_active_poll_ms` | number | 700 | launch only | page load | - | no |
| `chat.stick_px` ("Stay at the bottom within", px) | `chat_stick_px` | number | 80 | launch only | page load | - | no |
| `chat.tool_chars` ("Tool input shown up to", characters) | `chat_tool_chars` | whole number | 90 | launch only | page load | - | no |
| `chat.header_label` ("Header word of the context note") | `Banner:owner:main.header_dial` (`default_banner.c1f:14`) | text | `CONVERSATION` | `entity_write` | yes (banner getter recomputes) | yes | no |
| `chat.error_label` ("Words before a failed turn's note") | `Banner:owner:main.error_dial` (`default_banner.c1f:15`) | text | `THE PREVIOUS TURN ENDED WITH AN ERROR` | `entity_write` | yes | yes | no |

The six `chat_*` rows are the old page's constants (`ui-rebuild-plan.md` decision 6). In a c1f UI they are values the view pack reads; I recommend they become real settings (item B) or are dropped as constants (decision 3). The two labels are only worth a row if the operator wants them; they are the banner's, and the model may replace the whole banner (decision 4).

### Map

| key | lives | type / values | default | change today | live | journalled | secret |
|---|---|---|---|---|---|---|---|
| `map.hidden` ("Hidden kinds": "Kinds of things the map leaves out") | fact `map_hidden` (`door_fieldvm.c1f:73-74`) | list of class names, or comma text | `Message, SemText, Span, Request, DomNode, View, Fetch` | launch only | read on every request of `/ui/fieldvm/active`; fact is launch-only (item A) | - | no |
| `map.hidden_prefix` ("Hidden name starts") | `map_hidden_prefix` (`door_fieldvm.c1f:77-78`) | list / comma text | `Chat:message:` | launch only | same | - | no |
| `map.window` ("Things shown", how many of the most recently active) | `field_window` (`door_fieldvm.c1f:50`; `m` is required by the route, no default there) | whole number >= 1 | none: "the pack's tried value stands" | launch only | page load | - | no |
| `map.poll_ms` ("Refresh every", ms) | `field_poll_ms` | number | none (pack's value) | launch only | page load | - | no |
| `map.tile` ("Tile size") | `map_tile_k` (`door_map.c1f:36`) | number | 150 | launch only | - | - | no |
| `map.fade_s` ("Recent activity fades over", seconds) | `field_tau_s` (`door_state.c1f` op_tau; `door_map.c1f`) | number | 180.0 | launch only; the old window showed it read-only: "changing it needs the operator's approval" | - | - | no |

### Home

| key | lives | type / values | default | change today | live | journalled | secret |
|---|---|---|---|---|---|---|---|
| `home.allow:<name>` ("Allowed actions"): one entry per name, `services` (list: `light.turn_on`, `light.*`, `states.read`) and `entities` (list: `light.kitchen`, `light.*`) | `HaAllow:<name>` (`ha.c1f:103`, RESIDENT) | two lists of text | none: DEFAULT DENY (no entry = every call is an error) | `entity_write HaAllow:<name>.services / .entities` (owner), delete = `retire = true` | yes, read at the moment a call is claimed (`ha.c1f:21-23`) | yes | no |

The Home Assistant token is the component's own; the seat holds no Home Assistant secret (`docs/home-assistant.md`). No row for it.

### Import

| key | lives | type / values | default | change today | live | journalled | secret |
|---|---|---|---|---|---|---|---|
| `import.sources` ("Other databases": rows `name`, `path`, `access`) | `SqlSource:<name>` {path, mode} (`sql.c1f:25-27`, RESIDENT) | `mode` `ro` or `rw`; the seat's own store is refused with a reason | none; mode `ro` | `entity_write SqlSource:<name>.path / .mode` (owner); seeded by the fact `sql_sources` (a source with a path is left alone) | yes; the next run opens it again | yes | no |
| `import.page` ("Old turns read per query") | fact `legacy_page` (`door_session.c1f`, `legacy_import.c1f`) | whole number >= 1 | none: unset = an open imports nothing | launch only | LAUNCH-ONLY | - | no |
| "Import old history" (a button, not a value) | `Legacy:seat.list = {id}` / `.import = {id, sessions, page}` (`legacy_import.c1f:47, 69`; ledger entity) | action | - | `entity_write` | yes | yes | no |

`rw` on a source lets the model change that file: the row shows `rw` as text, not hidden (decision 5 asks whether the window may set it).

### Access (read-only)

| key | lives | shows | change |
|---|---|---|---|
| `access.owner` ("Owner") | fact `operator`, default `owner` (`door.c1f:70`) | the name; the owner is the person whose token `sub` equals it | launch only |
| `access.token_life` ("Owner key lasts", seconds) | `owner_token_ttl_s`, default 2592000 (30 days; `native/src/owner.rs:55`) | number | launch only; a new key is minted at every start |
| `access.token_file` ("Owner key file") | `owner_token_file` | the PATH only, never the token | - |
| `access.mode` | `door_secret_file` present = tokens required; none = open (the barest seat) | text | launch only |
| `access.people` ("People") | `Person:<sub>` {sub, name, first_seen, last_seen} (`door_auth.c1f`) | list, read-only | made by the first request of a token |
| `access.port` | `door_port` | number | launch only |

Minting a token for another person is the command `cliffvm token mint` (`docs/auth.md`); there is no route, so no control in the window (a new route would be new plumbing, item F).

### Limits (read-only, "restart needed")

`command_timeout_s` (default 120), `effect_workers` (4), `warm_rows` (1000), `memory_budget_mb` (none), `unload_soft` (none), `unload_hard` (none; 1.0 = the budget), `unload_batch` (none: no unloading), `eval_stats` (on), `eval_stats_window_s` (10): all set once in `native/src/cli.rs:134-148` through `Host::set_*`, all launch-only.
Seat identity shown with them (also launch): `seat_name`, `seat_aliases`, `seat_language`, `seat_stt` (url, else the browser listens), `seat_voice_out`, `seat_blurp`, `seat_backend` (url).
Never in the window: `request_keep_s` (accepted and ignored), `app_dirs`, `ledger`, `door_secret_file` content, `ui_dir`, `build_id`.

## How the window reads and writes (c1f only)

### Recommendation: one `Setting:<group>.<key>` entity per setting, owners stay the source of truth

Shape (a pack `settings.c1f`, class `Setting`, RESIDENT so it is in the page's snapshot and listed whole):

- Data, written once by the pack at `Boot.started` (the descriptor): `group`, `key`, `label`, `help`, `kind` (`choice` / `number` / `text` / `list` / `readonly`), `allowed` or `min`/`max`, `default`, `order`, `live` (`true` / `restart`), and `owner` + `owner_prop` (where the value lives, e.g. `Minds:seat`.`loops`).
- Getters: `current` reads the owner (`prop(this.owner, this.owner_prop)`, or the Boot fact, or the default), `err` validates a candidate, `shown` = label + current + help. No copy of the value is kept, so nothing drifts from the owner.
- ONE write door: `Setting:<g>.<k>.set = <value>`. The on-setter checks `err`; when `None` it writes the owner and records `applied = clock`; otherwise it records `refused = <reason>` and writes nothing. The window reads `refused` and shows it as text next to the row.
- The page writes it with ONE `Fetch` to `POST /mcp` `entity_write` (`door_tools.c1f:17, 58-64`): that route is already owner-checked (`mc_deny`, 403 shown as text), journalled (an outside write), and the reply carries the reactions (`refused` in them).

Why this and not "read the owners directly": (1) the window becomes ONE generic list (`FOR s IN instances('Setting')`, grouped by `group`, sorted by `order`): the labels, help, limits and defaults are data, so a new setting is a new row, not new view code; (2) validation lives next to the value, once, with the reason as text (today it is two copies: `op_err` and `errs`, `door_models.c1f:48-61, 131-141`); (3) launch-only and live rows are one shape (`live = restart`: shown read-only with the launch line, no edit); (4) the old routes keep working (`/membrane/options`, `/membrane/models` for the Home Assistant select) because they write the same owners. Direct reading would make the view know every owner's prop name and every validation.

Cost of the alternative the other way (a separate settings route): a new Request family of about 200 lines like `door_models.c1f`, one more owner check to keep in step with `door.c1f:71`; not recommended.

Things to prove before building (engine, not assumed): whether a setter can write a computed property name (`(this.owner).[this.owner_prop] = v`; `door_tools.c1f:63` does it for the MCP write, so I expect yes); if not, one explicit on-setter per setting, as `door_models.c1f:84-111` does.

### What the write path guarantees

| need | how |
|---|---|
| one owner-checked route | `POST /mcp` `entity_write` (`door_tools.c1f:44`); the 403 text `for the owner` (`door.c1f:73`) is shown as it is |
| live where possible | the owner's value is read by getters on the next use (`Chat.seat_model`, `HaCall.allow_by`, `SqlSource`); rows marked `restart` have no edit control |
| journalled | the write of the owner and of `applied`/`refused` are outside writes of RESIDENT entities |
| the reason when refused | `Setting.refused` (validation) or the 403 body (access); both text in the row |
| who wrote it | NOT recorded today: the MCP write does not stamp the caller (item E) |

## New plumbing, as separate items (nothing is built here)

| item | what | why | cost (rounds of 20-45 min, my estimate) |
|---|---|---|---|
| **S** the `Setting` pack | class, descriptors for the rows above, `current` / `err` / `set` | the whole design | 3-4 |
| **A** live override of a launch fact read by a pack | `Setting` value wins over the Boot fact in the getters that read the facts: `chat_max_turns`, `chat_turn_timeout_s` (`chat_turn.c1f:46, 51`), `map_hidden*` (`door_fieldvm.c1f:73-78`), the `chat_*` constants. A value written is journalled, so it survives a restart (the launch fact is then only the default) | these are the settings a person would want to change without a restart | 1-2 for the chat and map getters |
| **B** the page reads view constants from `Setting` | the view packs take `chat_*` / `field_*` from `Setting` entities instead of the snapshot's `Boot:seat` props | removes the "page load" lag | 1; needs `Setting` in the page's snapshot allow-set (`ui-rebuild-plan.md` decision 5) |
| **C** live host limits | a verb for `Host::set_command_timeout`, `set_effect_workers`, `set_memory` (`cli.rs:134-139`) so `command_timeout_s`, `effect_workers` and the memory lines can change at runtime | only if the operator wants them in the window; otherwise they stay launch-only | 2-3 (Rust, host verb + test) |
| **D** minds that something reads | `cortex`, `workers`, `think` and the sign-in picks have no reader | they return to the window only when a loop reads them | not planned |
| **E** who wrote | stamp the verified `sub` on a `Setting` write (the MCP write drops it) so the journal says who | audit of a seat several people can change | 1 |
| **F** token minting from the window | a route and a driver verb (`Request.mint` exists, `door_auth.c1f:12-18`) | today only the command line mints | 2-3; secret handling needs its own review |
| **G** `Boot` facts readable by the page | read-only `Setting` rows whose `current` reads `instances('Boot')` (a getter, no new route) | the Access / Limits groups | included in S |

Stays launch-only (no edit control, "restart needed", shown with the launch line): `model.cloud`, `model.signin`, `model.cli`, `access.*`, every Limits row, `import.page`, seat identity facts, and anything holding a secret.

## Decisions for the operator (none chosen)

1. Show `cortex`, `workers`, `*_auth`, `think`, `self_cpu_share`, `code_runs_keep` at all? They do nothing on this seat. Recommendation: no, until something reads them (item D).
2. Which launch-only rows become live (items A, C)? Recommendation: A for `chat.max_steps`, `chat.timeout` and the map rows; C only on request.
3. Keep the six `chat_*` view constants (20 / 10 / 3000 ms / 700 ms / 80 px / 90 chars) as settings, or fixed constants in the view pack? Each of those defaults is carried from the old page, not approved for the new one.
4. The two banner labels: a setting row, or only the model's banner? (and is `CONVERSATION` the default header word)
5. May the window set a SQL source's `mode` to `rw` (the model could then change that file), or only `ro`?
6. Defaults I would otherwise have to choose: `map.window` and `map.poll_ms` (no default exists; the pack's tried value stands), the allowed ranges of every number row (min/max for `chat.max_steps`, `chat.timeout`, `chat.*_ms`, `map.tile`, `map.fade_s`), and whether a number out of range is refused (my rule: refused with the reason, never clamped, as `options.py` did).
7. The group names and order (Model, Chat, Map, Home, Import, Access, Limits) and the label/help wording above.
8. Does the window show Access and Limits (read-only information) at all, or only editable settings?
9. Item E: record who changed a setting (yes/no).
10. Item F: mint tokens from the window, or keep the command line only.
11. `legacy_page` has no default (unset imports nothing on open): the window cannot show an editable number until the operator names one.

## Built: the server side (`membrane/packs/settings.c1f`, tests `native/tests/settings_1143.rs`, model page `Help:settings`)

Operator answers of 2026-11-14 applied: the dead settings and the six `chat_*` view constants are not rows; no author is recorded and no token is minted by the window; Access and Limits are shown read-only; the window cannot set a SQL source to `rw`; a number outside its range is refused with the reason.

**Contract for the view** (the c1f view is built separately): the window lists `instances('Setting')`, groups by `group`, sorts by `order`. A row holds `label`, `help`, `kind` (`text` | `number` | `list` | `allowlist` | `readonly`), `live` (`live` | `restart`), `default`, `min`, `max`, `whole`, `unit`, `how`, and the getter `current`. A change is ONE write, `Setting:<group>.<key>.set = <value>`, as `POST /mcp` `entity_write` (owner-checked: a person who is not the owner gets the door's 403 text `for the owner`, shown as it is). Read back `refused` (the reason as text, empty after a good write) and `applied` (when). A refused write writes nothing else. `readonly` rows have no edit control and say in `how` what changes them.

**Owners.** `model.chat` writes `Minds:seat.loops` (what the chat already reads); `home.allow` writes `HaAllow:<name>` (an entry the new list leaves out is retired); `chat.max_steps`, `chat.timeout`, `map.hidden`, `map.hidden_prefix`, `map.window` and `map.poll_ms` are owned by the row itself (`value`, journalled, so they survive a restart): the readers (`chat_turn.c1f`, `door_fieldvm.c1f`) read `Setting:*.current` first, then the launch fact, then the pack's own default (which a seat without this pack still uses). `GET /ui/fieldvm/active` with no `m` uses `map.window`, whose default is 150 (operator, 2026-11-14), so it answers 200; an `m` that is named but is not a number of at least 1 is still a 400; `map.poll_ms` defaults to 2000 ms; the snapshot's `field_window` / `field_poll_ms` read the same rows.

**Hidden by default.** `Setting` joins the default hidden classes (`map.hidden` and the `door_fieldvm.c1f` fallback): Message, SemText, Span, Request, DomNode, View, Fetch, Setting. Operator answers kept: `home.allow` stays one row; the banner labels and the seat-identity facts are not rows; number limits as listed (chat.max_steps 1..100000, chat.timeout 1..604800 s, map.window 1..10000, map.poll_ms 200..600000).

**Privilege.** The pack is granted privilege in `boot.c1f` (as `door_tools` and `unload` are) because it writes `Minds` and `HaAllow`, classes of packs the barest seat does not load; the surface snapshot shows the grant.

## Built: the view (`membrane/apps/barest.c1f/settings_view.c1f`, route `GET /ui/settings` in `settings.c1f`)

**Operator 2026-11-14: "settings panel is empty? build it as c1f".** The Settings tab of the page is a c1f view pack (no JavaScript logic) that fills the shell's Settings slot through the view-pack contract (`docs/ui-rebuild-plan.md`): it registers `Nav:settings`, draws when the tab is first shown and reads the rows again each time the tab is shown again.

- **Read feed.** The browser VM cannot read entities, so `settings.c1f` answers `GET /ui/settings` (a new route family `settings` in `door.c1f`, getters only, no builtin): `{settings: [row, ...]}`, every row in the window's order (`order` runs through the groups), each row `id, group, key, label, help, kind, live, order, default, min, max, whole, unit, how, hidden` and what is true now `current, refused` (text, empty after a good write), `applied` (a time); the chat model row also carries `choices` (the seat's model list: empty on the barest seat, which loads no router). Hidden rows are sent flagged; the view leaves them out. Nothing in it is a secret (tested with a key, a url password, the door secret and the owner key on the seat). Everyone who is authenticated reads it; the write door stays the owner's. Tests: `native/tests/settings_1143.rs` (`the_view_feed_...`).
- **View.** One section per group (Model, Chat, Home, Import, Access, Limits: the order the feed carries), one row per setting: label, a badge (`Applies now` / `Needs a restart`), the help line, what is in force, the control, the result of the last Save. text and number: a text input (a select when the feed gives a model list) and Save; list: one input per line with Remove, Add a line, Save; allowlist: one card per entry (name, services, entities as comma-separated text, Remove), Add an entry, Save; readonly: the value, and the launch line from `how` in plain text, no control of any kind. Enter in a box is the form's submit.
- **Write.** ONE `POST /mcp` `tools/call` `entity_write` of `Setting:<id>.set` per Save (one at a time per row). The call is answered 2xx when the seat took it: the rows are then read back, and the row says `Applied.` or `Refused: <the seat's own reason>` next to it (the value in force unchanged, what was typed stays in the box). A refusal of the call itself (403 `for the owner`, a failure, a timeout) is `Not saved: the seat answered: <reason>`. Nothing is clamped by the page; a text that is no number goes as typed, so the seat's reason names it.
- **Not in the view:** the six Map rows (`hidden`), the dead settings, any secret (only environment-variable NAMES and file PATHS), who changed a value, token minting, `rw` for a SQL source.
- **Tests:** `tests/js/settings_view.test.mjs` (wasm engine: rows per group in order, hidden absent, live write, refusal with reason, 403 text, restart rows without a control, both editors round trip, select, failed read and Try again, a stale answer dropped, no secret, idle), `tests/js/settings_page.test.mjs` (a real private seat and headless Chrome: change the chat step limit and see it applied, 0 refused with the seat's reason, the model row, the allow-list editor, no secret on the page, dark/light/phone screenshots).
