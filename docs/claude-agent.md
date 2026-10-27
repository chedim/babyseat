# The claude-agent rung: Claude through the Agent SDK

The seat can route a turn to Claude through the
[Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) (issue #384).
The rung runs on your **Anthropic API key** (issue #413). The SDK drives
the `claude` CLI, and membrane hands that CLI your key. membrane does not
call an Anthropic API itself.

**The rules (issue #389):** Claude's replies are learned like any other
model's. Claude gets membrane's own tools, and only those, inside the SDK's
agent loop.

## Turning it on

1. Install the optional extra on the seat's Python:
   `pip install 'babyseat[claude-agent]'` (or `pip install claude-agent-sdk`).
   The SDK ships its own copy of the `claude` CLI. membrane uses the
   `claude` on your PATH first, and that bundled copy if there is none.
2. Give membrane your Anthropic API key: run `membrane auth anthropic`
   (it asks for the key and stores it in `~/.membrane/auth.json`, mode
   600), or set `ANTHROPIC_API_KEY` in the seat's environment. The
   environment variable wins over the stored key.
3. Run `membrane auth claude-agent`. It checks that the SDK is installed,
   that the CLI is found and that a key is set. Then it stores a non-secret
   mode mark (`"claude-agent": "api-key"`) in `~/.membrane/auth.json`. The
   key is not copied or printed. You can set `MEMBRANE_CLAUDE_AGENT=1`
   instead.
4. Restart the seat when it is idle. At boot it registers three rungs:
   `claude-agent:sonnet`, `claude-agent:opus` and `claude-agent:haiku`. They
   use the CLI's model aliases.

**How the key reaches Claude.** For each turn, membrane passes the key to
the CLI subprocess as `ANTHROPIC_API_KEY` in the SDK's
`ClaudeAgentOptions.env`. The SDK sets that env over the seat's own
environment. The same env blanks every credential the CLI would use
instead of the key: `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` and
the Bedrock, Vertex and Foundry switches. So the CLI cannot fall back to a
`claude` login stored on the machine. With the key in its environment,
`claude auth status` reports `authMethod: "api_key"` even where a
claude.ai login is stored. The key is never printed or logged, and an
error that echoes it is masked before it is shown.

A missing SDK, CLI or key gives a clear error. The boot line says
`NOT usable yet: …`. A turn sent to the rung gets HTTP 503 with what to
do (for a missing key: run `membrane auth anthropic` or set
`ANTHROPIC_API_KEY`), and no model is called. If Claude fails during a
turn (for example, the key is wrong or hit a limit), the reply says
`[claude-agent] the turn failed: …`.

### Development opt-in: the CLI login

For development and personal use only, `membrane auth claude-agent --login`
runs the rung on the `claude` CLI's own login instead of a key. It checks
that `claude auth status` reports a login, then stores the mark
`"claude-agent": "login"` (or set `MEMBRANE_CLAUDE_AGENT=login`). In this
mode `ANTHROPIC_API_KEY` is blanked for the CLI, so the login answers.
membrane never reads the login's token. It is never chosen on its own: a
mark stored before #413 (`"cli-login"`) means the API key, and
`membrane auth claude-agent` without `--login` goes back to the key.

## When it answers

The rung is **opt-in**. The router never escalates to it on its own:
autoselect, a price ceiling and the failover ladder all skip it. A
claude-agent rung answers only when:

- you **pin** it: pick it as the session's model in the picker or the TUI;
  or
- you **add it to the ladder** with `--ladder-include claude-agent:haiku`
  (repeatable). Only then can autoselect and the ladder reach it.

**Background lanes use it only when you pick it for them.** This covers the
thinker, the dreamer, the night, the judges, the watch loops and the field's
own calls. Without a pick, a background chat call to the rung returns nothing
and makes no call, and a loop session pinned to it falls back to the seat's
model. A person's lanes can always take it: the chat, plan, the phone and a
speaker. The exception is an explicit pick in the ⚙ options window, under
Background work (or `POST /membrane/models`, or an active-model entity). There
you can set the **Goal worker** (the watch and goal loops) or the **Idle
thinker** (the background chat calls of the thinker, dreamer and night) to a
claude-agent rung. That mind's background turns then call Claude, billed to
the credentials picked on its row:

- **Same as the chat** (the default) uses whatever the chat's rung uses: the
  API key, or the CLI login when you opted in with `--login`.
- **API key** always uses the Anthropic key for that mind.
- **CLI login** always uses the `claude` CLI login for that mind.

The credentials row only appears when the mind's model is a claude-agent
rung. The choice is saved in `models.json` next to the minds' models
(`loops_auth`, `cortex_auth`). A turn carries the override as
`membrane_claude_auth`, which reaches `claude_agent.credential_env(mode=)`.
The chat's own path is unchanged. The claim judges still call Ollama
directly, so a Claude rung does not answer them.

**It gets membrane's tools, and only those.** The SDK runs its own agent
loop, and it cannot hand a tool call back to membrane unexecuted. So Claude
gets membrane's MCP tool surface (the same tools the stdio MCP server serves to
opencode) as an in-process SDK MCP server named `membrane`:

- `allowed_tools` is exactly the `mcp__membrane__*` set, one per tool in
  `membrane/mcp_server.py`'s `TOOLS`. Each tool runs the same function the
  stdio server runs (`TOOL_IMPLS`). It relays to this seat's proxy, so the
  proxy's guards apply: dedup, the variety gate, caps and the audit trail.
- Claude Code's own tools stay off: `tools=[]` (no Bash, Read, Edit, Write,
  WebFetch or any other built-in). No MCP servers from your config
  (`strict_mcp_config`) and no settings files or `CLAUDE.md`.
- An answer stops at 2048 tool calls, where you can press Continue. No
  tool call is refused for silence, and no wall clock cuts a turn that is
  still working (see "One run, a ceiling, an idle guard" below).
- The turn's own tool definitions (the chat lane's surface) are not passed
  on. The reply never carries `tool_calls` for the caller to run, and the
  bullet-salvage that turns written calls into real ones stays off for
  Claude rounds, so nothing runs twice.

The system prompt ends with a fixed **tools note** (`TOOLS_NOTE`): Claude Code's own tools are off, and membrane's tools are its hands. It frames the work as **programming the field**, pointing to the doctrine's rules (a library, not one-shots) rather than restating them: there are no one-shot commands (#576 part F): find what already exists (`entity_query`, `graph`, `method_inspect`) and call it; to do something new, define a method on the entity it belongs to or its class (`method_define`, `{{name}}` params), then `method_call` it on one object or `method_each` over the class, or write a property whose write is the effect; a call straight at the executor (`tool:executor`, `remote_executor`) is refused; keep each method small (a sensor reads one thing, an actuator does one) and compose them through steps or parents; a thing in the world is an entity that keeps itself current (`entity_edit` with a `getter_script` or a `tick_script` paced by `tick_every`), read back after planting; code work happens in a git worktree in a temp folder, never the main checkout. (Before 2026-10-25 the note sold `entity_link` as a raw shell, and Memo ran commands instead of building anything the field could reuse; on 2026-10-26 `entity_link` went entirely.) It also says to ask the person for a decision as a `Decision` card on their board (one variable and a pick), not a list in chat, made in one `entity_add` call (it applies `properties` itself), with a concrete option-card example and the rule that a name holds no quotes or apostrophes (the name lint refuses them). The answer wakes the watcher loops as a `decision` event ([decision-map.md](decision-map.md#an-answer-wakes-the-watcher-loops)). It maps the doctrine's tool families onto the rung's MCP tools (`exec(action='call')` -> `method_call`, `exec(action='define')` -> `method_define`, `goal(action='plant')` -> `intend`, and so on); a test checks every pair against `toolset.FAMILIES` and `mcp_server.TOOLS`, so the map can't go stale. And it says how to work, as Claude Code does: carry a task through to the end within the turn, without asking permission for its steps or ending on "next I'll..."; end the turn only when done or when blocked, and say which; status updates ride the next tool call's message; no "Open decisions" footers. And since a turn ends after 5 minutes of silence and a running tool call is silent, a command that may run longer than about 4 minutes is started detached (a method whose command is `nohup … > log 2>&1 &`) or as an entity script, and checked afterwards. Without it, Claude told the operator it had no tools, and ended most turns on a permission question or an announced next step. Its text never changes, so the prompt cache still holds.

`method_call` answers like Claude Code's Bash tool: the method's reply carries its run's whole output (stdout, then stderr) up to `mcp_server.LINK_OUTPUT_CAP`, 30000 characters, Claude Code's default. A longer output keeps its first 20000 and last 10000 characters around a `… N chars cut …` marker; the reply gives the total size and says to read the rest by re-running narrower (`sed -n 'A,Bp'`, `head -c`, `tail -c`, `grep`), since the full output is not kept. The cut happens where the command runs, so the route's JSON never carries more than the cap. What is stored stays small: the method entity keeps its `run_head`/`run_tail` residue (6000 and 2000 characters), the field event carries the same, and the object's call ring keeps only the fed line. The local models' toolset path (`server.mcp_executor`, `mcp_server.LOCAL_TOOL_IMPLS`) keeps the 600-character head it always had, because their context is small. A command slower than 3 s on that path still becomes a job, and its delivered result keeps its 1500-character cap (`jobs.RESULT_MAX`); the Claude rung runs its tools without the job path.

The tools Claude gets:

| Tool | Writes? |
|---|---|
| `query`, `state`, `intents`, `entity_tracks`, `entities_related`, `entity_query`, `graph` | read-only |
| `entity_read` | reads, but a dynamic entity's getter script fires (and `run: true` runs it) |
| `learn`, `forget` | writes field notes / deletes cells |
| `intend`, `fulfill` | writes goals |
| `entity_add`, `entity_edit`, `entity_remove`, `entity_inherit` | writes entities (`entity_edit` can set scripts) |
| `method_define` | defines a Cliffhanger method on a class or object (a command template, or composite `steps`, optionally behind a `guard`; `abstract` declares an interface's method); owner only, like every method route |
| `method_call` | runs a Cliffhanger method on an object, e.g. `presence.restart`; a call straight at the executor (`tool:executor`, `remote_executor`, an implementation's `exec`) is refused |
| `method_each` | calls one method on every member of a class or a name pattern (`max`, `dry_run`, `on_fail`) |
| `method_inspect` | read-only: where a call would resolve, and its template |
| `entity_watch`, `entity_unwatch`, `field_watch`, `attention`, `junk_cut` | writes tracks, watches and dials |
| `peer_post` | posts on the peer channel |

The turn's stable system messages become the SDK's system prompt: the
doctrine and the client's own instructions. The rest of the conversation goes
in as the prompt, ending on the latest message. membrane's per-turn blocks
(the context block with recall and where-we-are, banners, watch reports) go
at the end of the prompt, inside `<membrane-context>` ...
`</membrane-context>`, right before the latest message; the speech footer
stays after it. The reply comes back as an ordinary OpenAI response, streamed
or buffered, with its usage. Each tool Claude ran rides the reply: a
`membrane_tool` chunk when streamed, a `membrane_tools` list when buffered.
When streamed, each call's start rides ahead of its result: a
`membrane_tool_start` chunk as soon as the SDK reports the tool_use, before
the tool runs (again with the full input; `{"id", "dropped"}` when the
ceiling's hook denied it). A start is not a ran tool: it never joins
`membrane_tools`.

## The prompt cache

Anthropic caches a request's prefix in order: the tool schemas, then the
system prompt, then the messages. A system prompt that changed on every call
(it used to carry the per-turn blocks) broke the cache right after the tools.
Now the system prompt is byte-identical from call to call, so the tools and
the system prompt can be read from the cache. The per-turn blocks cost only
their own tokens at the end of the prompt.

A block counts as per-turn when membrane placed it for this turn (the
proxy's `rag._inject` / `rag._inject_after`) or when it is a system message
that comes after the conversation has started. When the doctrine rides in
front of an injected block (a client that did not get it as an instructions
file), the doctrine part stays in the system prompt.

The usage keeps Anthropic's split. The OpenAI-shaped `usage` totals are
unchanged (`prompt_tokens` is uncached input + cache reads + cache writes);
`usage.prompt_tokens_details` adds `cached_tokens` (reads) and
`cache_write_tokens` (writes). The stored message tokens carry them as
`tokens.cache.read` and `tokens.cache.write`; the uncached input is
`tokens.input` minus both. `GET /membrane/diag/claude-agent` adds
`usage: {calls, input, cache_read, cache_write, output, per_answer, per_call,
steps}`: sums since the seat's boot (`input` is the uncached input). `calls`
counts answers, and `per_answer` (still also `per_call`, its old name) is one
answer's summed usage. One answer is many API round trips, so `steps` (#450)
measures per step from the usage the SDK reports on each response:
`{steps, first, later, per_step, per_first_step, per_later_step}`. An answer's
**first** step writes the fresh prompt to the cache (the transcript window,
the banner, the recall). Its **later** steps append only the last reply and
tool result, and read the rest from the cache. `prompt` (#450) measures
what each answer's first step sends, in chars per answer (`system_chars`,
`head_chars`, `tail_chars`), and `system_changes` counts the answers whose
system prompt differed from the answer before: each one rewrites the cached
system prompt.

**The stable cached head (#450), on with an API key.** When the rung runs on an Anthropic API key, the conversation goes to the SDK as content
blocks, one per earlier message, with a cache breakpoint on the last of them.
The per-turn blocks (the banner) and the latest message follow it. The next
answer starts with the same blocks, so the prompt cache reads the head the
answer before wrote, instead of writing it again. The serve window advances in
steps of `HISTORY_ANCHOR_STEP = 4` messages (`membrane/server.py`): its first
row is a multiple of 4, so the window holds 9-12 earlier messages instead of
exactly 12, and its start (the head's first block) stays put for a few
answers. The bundled CLI keeps our `cache_control` and adds its own on its
trailing environment message. With the system prompt's 2 that is 4, the API's
maximum, checked by capturing the CLI's requests. It is on by default on an API
key and off on the CLI login (the development opt-in below): on the live seat
(the CLI login) every head answer failed as `claude-agent: unknown`.
If a head answer fails for any reason before its first word, it is sent again as
one plain text prompt, and the head stays off until the seat restarts
(`head_refused` in the diag). Switches: `MEMBRANE_CLAUDE_CACHE_HEAD=1` forces the head on (the login
included), `MEMBRANE_CLAUDE_CACHE_HEAD=0` forces it off (an API key included),
`MEMBRANE_HISTORY_ANCHOR=1` restores the plain last-12 window, and
`MEMBRANE_HISTORY_MAX` sets the window's size (default 12).

**Aborted answers keep their usage (#472).** After every API step the reply
streams the answer's usage so far (cumulative; its last chunk still carries
the whole answer's). The proxy keeps the last usage it saw. A serve round
whose stream breaks (the turn watchdog closing its socket) records the usage
it had, instead of the ~2K estimate.

## One run, a ceiling, an idle guard

One answer is **one continuous SDK run**. There is no fixed batch of turns.
Claude works the way Claude Code does (the operator, 2026-10-23: "let's do
it as Claude. If the model loops, that's not our problem but the model's"):

- **No check-ins.** No tool call is ever refused for silence. Claude may
  make any number of tool calls in a row without writing to the person.
  (Until 2026-10-23 the 8th call in a row without words was denied with a
  check-in request, #425.) The tools note asks Claude to put its status
  updates in the same message as its next tool call.
- **The ceiling:** a `PreToolUse` hook (the SDK's `hooks` option,
  `membrane/claude_agent.py`) counts the calls: at most
  `MAX_TOOL_CALLS_PER_ANSWER = 2048` tool calls per answer. The call past
  it is denied and the hook stops the run.
- `max_turns` is only a backstop: `MAX_TURNS = 2 * MAX_TOOL_CALLS_PER_ANSWER`,
  so it never binds before the ceiling. If it ever does, the answer is
  marked the same way as at the ceiling.
- Every membrane tool result ends with a footer, `· N calls left`, the live
  count within the answer (#467). The footer is for Claude only: it is
  stripped from the stored tool output. The stored reply keeps its parts in
  the order they happened: text up to a call, that call, the next text, and
  so on.
- **The idle guard instead of the wall clock.** Other models' turns are cut
  by the turn watchdog after `TURN_MAX_S = 300` s without a new round
  (`membrane/server.py`). A Claude answer is one round, so that clock cut
  Claude turns that were still working. A claude-agent turn has no wall
  clock and no lifetime cap: every event its stream publishes (a token, a
  tool call and its result, a usage step) resets an idle clock, and the
  turn is aborted only after `TURN_IDLE_S = 300` s (approved 2026-10-23)
  with no event at all. The abort says `no event for 300.0s -- aborted by
  the turn watchdog's idle guard`.
- **A silent tool call is silence.** No keep-alive is streamed while a
  membrane tool runs (the operator, 2026-10-24, took out #502's), so a
  tool call that returns nothing for `TURN_IDLE_S` ends the turn like any
  other silence. The tools note tells Claude so: a command that may run
  longer than about 4 minutes must be started detached (a method whose
  command is `nohup … > log 2>&1 &`) or as an entity script, and its
  result checked afterwards. The ceiling and the idle guard are the only
  stops; nothing continues by itself.

## The ceiling and Continue

An answer that stops at the ceiling (or the backstop) is not an error, and
it is not cut off without a word:

- The reply carries `membrane_capped: {session, calls}` (an SSE chunk when
  streamed, a top-level key when buffered). `session` is the SDK's session
  id.
- The transcript row keeps it as `claudeCapped: {session, calls, continued}`.
  The chat shows "Claude stopped at the tool-call ceiling (50 calls)" under
  the answer, with a **Continue** button. Rows from before #425's ceiling
  (`turns`) still show the old turn-cap line.
- Continue posts the person's message `Continue` with
  `claudeContinue: <message id>`. The seat claims that capped answer once
  and starts a turn that resumes the **same** SDK session (the SDK's
  `resume` option), with a short continue prompt. The turn gets the same
  tools and options and a fresh ceiling. If it reaches the ceiling again, the new answer offers its own
  Continue.
- Nothing runs twice. Each capped answer can be continued only once: a
  second press gets HTTP 409 and makes no call. The resumed session already
  holds the earlier tool calls and their results, and none of them is run
  again.
- Continue is a person's action only. Nothing continues by itself, and a
  session on a background lane (a watch loop, a mind) refuses it. Typing
  "Continue" as an ordinary message does not resume a session.
- Diagnostics: `GET /membrane/diag/claude-agent` (localhost only) returns
  `{ceiling_hits, continues, head_refused, max_tool_calls, max_turns}`,
  plus the cache `usage` above. The counters live in RAM since the seat's
  boot, so you can see how often an answer reaches the ceiling.

## What is learned, what is shown

Claude's turn is learned like any other model's turn:

- **Capture.** The proxy gives the field the person's message, Claude's
  reply and its reasoning (`on_turn(query, reply, thought)`). Claude's tool
  calls and their results go in as calls and results (`on_calls`,
  `on_tool_result`). The A/B judge and the frame lint read the reply. Claims
  and the night read the field's events, so they see it too. Only a failed
  SDK turn's error line (`[claude-agent] the turn failed: …`) is not
  learned as the model's words.
- **The voice and the predictor** hear Claude's rows like any model's.
- **The transcript.** The reply and Claude's tool calls show in the chat.
  Each tool call shows the way Claude Code shows it: its part appears,
  state `running`, when the call starts, and the words before it go out and
  close then, not when the tool finishes; the result completes the same
  part with its input and output. A call that gets no result (denied, or
  the reply ended) ends as an error, never left running. Streamed words go
  out in batches of at most `DELTA_COALESCE_S`, by a timer when no next
  event comes. The stored row keeps text, call, text in order. The
  assistant row and each of its tool parts are marked
  `madeBy: "claude-agent"`. The mark is provenance only now: nothing skips
  a marked row.
- **The mark keys on the provider, not the model's name.** The rung's
  router record has `backend: "claude-agent"`. The proxy stamps every
  response and chunk with `membrane_provider: "claude-agent"`, and the row
  mark comes from that stamp.
- **Absorb** (importing external Claude Code and OpenCode sessions)
  learns sessions answered by Anthropic models like any other, and that
  includes a `claude-agent:*` model id or a marked row. It learns
  OpenCode sessions from every provider too (#404), and keeps who
  answered, per row, as provenance (#396). The one provider it skips is
  the seat's own (OpenCode provider `membrane`), so the seat's turns do
  not come back to it.

## Out of tokens

When Claude reports that the quota is spent (an `AssistantMessage` with
`error` `rate_limit` or `billing_error`, or a failed result whose words say so),
that rung alone pauses (`membrane/quota.py`). The pause lasts until the reset
time: the CLI's rejected rate-limit event (`resets_at`), else the
"resets 10:50pm (America/New_York)" line, else a probe 10 minutes later (the
next real call). A paused rung is not called: `open_reply` refuses it before
the SDK starts, and the proxy answers `429` with error type `quota_paused` (or
that error as an SSE event once the stream has begun). The serve turn holds the
person's message, and the page shows "out of tokens · it resumes at HH:MM" with
Resume now. The other rungs and models keep answering. See
[standalone.md](standalone.md#out-of-tokens-) for the chat, the background minds
and the ⚠.

## Billing

The rung's usage is billed to the Anthropic API key, like any API call on
that key.

The rules above are enforced in code (`membrane/claude_agent.py`,
`membrane/lanes.py`, `membrane/server.py`, `membrane/cli.py`) and tested
(`tests/test_claude_agent_384.py`, `tests/test_claude_agent_tools_389.py`,
`tests/test_claude_agent_apikey_413.py`).
