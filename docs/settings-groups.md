# Settings: the rows, the write door and the window (BUILT, #1143)

Status: built and tested. The server side is `membrane/packs/settings.c1f` (tests `native/tests/settings_1143.rs`, the model's page `Help:settings`); the window is the c1f view pack
`membrane/apps/barest.c1f/settings_view.c1f` (tests `tests/js/settings_view.test.mjs`, `tests/js/settings_page.test.mjs`: a real private seat and headless Chrome). The design questions this page once carried were answered
by the operator on 2026-11-14 and are recorded as the rules below.

## Rules

- Everyone who has access is the admin (no users in this release: docs/auth.md). The window is the same for everyone: no admin-only variant, no per-person or per-device setting.
- A write the seat refuses for its VALUE (a number out of its range, a bad name) is shown as text, with its reason, in place; nothing is hidden or clamped. A write with no verified token is a `401` and changes nothing.
- No secret is ever shown. A key or token appears only by the NAME of its environment variable or as a path or a yes/no (`anthropic_key_set`).
- A setting that nothing reads is not shown: `cortex`, `workers`, the sign-in picks of the minds, `think`, `self_cpu_share`, `code_runs_keep` (stored by `POST /membrane/options` and read by nothing), pause/loops controls, per-person voice,
  quota (out of the release), and the page's own constants (`chat_page_size` ... `chat_tool_chars`). Everything the launcher tells (`--fact`) is launch-only: `Boot` is VIRTUAL (docs/cliffvm.md "Storage modes"), so it comes back from the launch line on every start.

## The rows (what `settings.c1f` declares)

`Setting:<group>.<key>` is one RESIDENT entity per setting. A row is data (`group`, `key`, `label`, `help`, `kind`, `live`, `order`, `default`, `min`, `max`, `whole`, `unit`, `how`, `fact`, `owner` + `owner_prop`) and a getter `current`
over the one place the value lives, so nothing drifts. `kind` is `text | number | list | allowlist` (editable) or `readonly` (shown, never written, `how` says what changes it); `live` is `live` (in force at once) or `restart`.

| group | rows | where the value lives | change |
|---|---|---|---|
| Model | `model.chat` (the model that answers) | `Minds:seat.loops` | live |
| | `model.default`, `model.list`, `model.cloud` (fact `cloud_models`), `model.signin` (fact `chat_auth`: `api_key` or `login`), `model.cli` (fact `chat_claude_bin`) | the launch | read-only, restart |
| Chat | `chat.name` (fact `seat_name`, default Bob: the doctrine's "Your name is ...", docs/doctrine-barest.md; one line of at most 120 characters), `chat.max_steps` (fact `chat_max_turns`, default 2048, 1 to 100000), `chat.timeout` (fact `chat_turn_timeout_s`, default 21600 s, 1 to 604800) | the row's own `value` (journalled, survives a restart) | live |
| Map | `map.hidden`, `map.hidden_prefix` (lists), `map.window` (default 150, 1 to 10000), `map.poll_ms` (default 2000, 200 to 600000); `map.fade_s` (read-only) | the row's own `value` | live; the Map rows are hidden in the window while the field view is disabled |
| Home | `home.allow` (one `HaAllow:<name>` entry per name: `services`, `entities`; default deny) | `HaAllow:<name>` | live |
| Import | `import.history` (fact `legacy_path`), `import.sources` (`SqlSource:<name>`: `ro` or `rw`), `import.embeddings` ("Sentence vectors": `Embed:queue.line`, how many texts have their vectors or why the queue waits; fact `embed_model` names the model, default `mxbai-embed-large`; `packs/embed.c1f`) | the launch, `SqlSource`, `Embed:queue` | read-only, restart |
| Access | `access.token_life` (fact `admin_token_ttl_s`, 30 days), `access.token_file` (path only), `access.mode` (tokens required / open), `access.port` | the launch | read-only, restart |
| Limits | `limits.command_timeout_s` (120), `effect_workers` (4), `warm_rows` (1000), `memory_budget_mb`, `unload_soft`, `unload_hard`, `unload_batch`, `eval_stats` (on), `eval_stats_window_s` (10) | the launch | read-only, restart |

`Setting` is in the default hidden classes of the map (Message, SemText, Span, Request, DomNode, View, Fetch, Setting). The settings pack is granted privilege in `boot.c1f` (as `door_tools` and `unload` are) because it writes
`Minds` and `HaAllow`, classes of packs the barest seat does not always load; the surface snapshot shows the grant.

## The one write door

`Setting:<group>.<key>.set = <value>`, written by one `POST /mcp` `tools/call` `entity_write`. The setter checks the value (each arm refuses with its reason); a good value is written to its owner and `applied` says when
(`refused` is cleared); a bad one writes `refused = <reason>` and NOTHING else. The reply of the write carries the reactions, so the page sees `refused` at once. The readers (`chat_turn.c1f`, `door_fieldvm.c1f`) read `Setting:*.current` first, then the launch fact,
then the pack's own default. `GET /ui/fieldvm/active` with no `m` uses `map.window` (default 150); an `m` that is named but is not a number of at least 1 is a 400. Who wrote a value is NOT recorded (no users).

## The window (`settings_view.c1f`)

- **Feed.** The browser VM cannot read entities, so `settings.c1f` answers `GET /ui/settings`: `{settings: [row, ...]}` in the window's order, each row `id, group, key, label, help, kind, live, order, default, min, max, whole, unit, how, hidden`
  and what is true now (`current`, `refused`, `applied`); the model row also carries `choices` (the model list: empty on the barest seat, which has no model to pick between). Nothing in it is a secret (tested with a key, a url password, the door secret and the admin key on the seat).
- **View.** One section per group, one row per setting: label, a badge (`Applies now` / `Needs a restart`), the help line, what is in force, the control, the result of the last Save. text and number: a text input (a select when the feed gives a list) and Save;
  list: one input per line with Remove, Add a line, Save; allowlist: one card per entry, Remove, Add an entry, Save; readonly: the value and the launch line from `how`, no control.
- **Write.** One `entity_write` of `Setting:<id>.set` per Save. A 2xx means the seat took it: the rows are read back and the row says `Applied.` or `Refused: <the seat's reason>` (the value in force unchanged, what was typed stays in the box).
  A refusal of the call itself (a failure, a timeout, a `401`) is `Not saved: the seat answered: <reason>`. The page clamps nothing; a text that is no number goes as typed, so the seat's reason names it.
- **Not in the window:** the dead settings above, any secret, who changed a value, token minting (`cliffvm token mint` is the command line), `rw` for a SQL source.

## Not built

Live host limits (a verb for the command timeout, the worker count, the memory lines), minting tokens from the window, recording who changed a value, and the minds' settings that nothing reads. They return only when something reads them.
