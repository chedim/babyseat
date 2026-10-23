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
- Claude checks in with the person at least every 7 tool calls, and an
  answer stops at 2048 tool calls, where you can press Continue (see
  "Check-ins instead of a turn cap" below).
- The turn's own tool definitions (the chat lane's surface) are not passed
  on. The reply never carries `tool_calls` for the caller to run, and the
  bullet-salvage that turns written calls into real ones stays off for
  Claude rounds, so nothing runs twice.

The system prompt ends with a fixed **tools note** (`TOOLS_NOTE`): Claude Code's own tools are off, and membrane's tools are its hands. `entity_link` runs shell commands (read, edit, test, `git`, `gh`, in a git worktree for code work), and `method_call` runs an object's method. It also says to ask the person for a decision as a `Decision` card on their board (one variable and a pick), not a list in chat, made in one `entity_add` call (it applies `properties` itself), with a concrete option-card example and the rule that a name holds no quotes or apostrophes (the name lint refuses them). It maps the doctrine's tool families onto the rung's MCP tools (`exec(action='link')` -> `entity_link`, `goal(action='plant')` -> `intend`, and so on); a test checks every pair against `toolset.FAMILIES` and `mcp_server.TOOLS`, so the map can't go stale. And it says how to work, as Claude Code does: carry a task through to the end within the turn, without asking permission for its steps or ending on "next I'll..."; end the turn only when done, when the person's decision is needed (a Decision card, then stop), or when blocked, and say which; status updates ride the next tool call's message; no "Open decisions" footers. Without it, Claude told the operator it had no tools, and ended most turns on a permission question or an announced next step. Its text never changes, so the prompt cache still holds.

The tools Claude gets:

| Tool | Writes? |
|---|---|
| `query`, `state`, `intents`, `entity_tracks`, `entities_related`, `entity_query`, `graph` | read-only |
| `entity_read` | reads, but a dynamic entity's getter script fires (and `run: true` runs it) |
| `learn`, `forget` | writes field notes / deletes cells |
| `intend`, `fulfill` | writes goals |
| `entity_add`, `entity_edit`, `entity_remove`, `entity_inherit` | writes entities (`entity_edit` can set scripts) |
| `entity_link`, `entity_unlink` | runs command entities / retires the links |
| `method_call` | runs a Cliffhanger method on an object, e.g. `presence.exec` (JS in the person's opted-in page) or `presence.restart`; defining methods (`method_define`, `method_each`) stays off the MCP surface |
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

## Check-ins instead of a turn cap

One answer is **one continuous SDK run**. There is no fixed batch of turns.
Instead, a `PreToolUse` hook (the SDK's `hooks` option, `membrane/claude_agent.py`)
keeps Claude talking to the person while it works:

- **Check-ins:** Claude may make at most `CHECKIN_TOOL_CALLS = 7` tool calls
  in a row without writing text to the person. Any assistant text, such as
  a progress or status line in the reply, resets the count to 0. The 8th
  call in a row is denied with a message telling Claude to write its one-line
  status in the same message as its next tool call (words first, then the
  call), not as a separate text-only reply. Claude gets that as the tool's
  result and goes on in the same run. The denied call never
  runs. The chat shows no error for it: a denied call is Claude's
  feedback, not a tool part.
- **The ceiling:** at most `MAX_TOOL_CALLS_PER_ANSWER = 2048` tool calls per
  answer (about 290 check-ins). The call past it is denied and the hook
  stops the run.
- `max_turns` is only a backstop: `MAX_TURNS = 2 * MAX_TOOL_CALLS_PER_ANSWER`.
  Every allowed call may take a turn and be denied once first, so it never
  binds before the ceiling. If it ever does, the answer is marked the same
  way as at the ceiling.
- Both values were approved by the operator on 2026-10-21.
- Calls in one assistant message count one by one. The hook waits until
  membrane has read the message that carries the call, so words in the
  same message count first.

## Check-ins ride with the next call (#467)

When a check-in is due, the denial tells Claude to write its one-line status
in the same message as its next tool call, not as a separate text-only reply.
Every membrane tool result ends with a footer, `· N calls left · check-in in
M`, the live count within the answer. The stored reply keeps its parts in the
order they happened: text up to a call, that call, the next text, and so on.

## The ceiling and Continue

An answer that stops at the ceiling (or the backstop) is not an error, and
it is not cut off without a word:

- The reply carries `membrane_capped: {session, calls}` (an SSE chunk when
  streamed, a top-level key when buffered). `session` is the SDK's session
  id.
- The transcript row keeps it as `claudeCapped: {session, calls, continued}`.
  The chat shows "Claude stopped at the tool-call ceiling (50 calls)" under
  the answer, with a **Continue** button. Rows from before the check-ins
  (`turns`) still show the old turn-cap line.
- Continue posts the person's message `Continue` with
  `claudeContinue: <message id>`. The seat claims that capped answer once
  and starts a turn that resumes the **same** SDK session (the SDK's
  `resume` option), with a short continue prompt. The turn gets the same
  tools and options, a fresh ceiling of 50 calls, and a fresh check-in
  count. If it reaches the ceiling again, the new answer offers its own
  Continue.
- Nothing runs twice. Each capped answer can be continued only once: a
  second press gets HTTP 409 and makes no call. The resumed session already
  holds the earlier tool calls and their results, and none of them is run
  again.
- Continue is a person's action only. Nothing continues by itself, and a
  session on a background lane (a watch loop, a mind) refuses it. Typing
  "Continue" as an ordinary message does not resume a session.
- Diagnostics: `GET /membrane/diag/claude-agent` (localhost only) returns
  `{checkin_denials, ceiling_hits, continues, checkin_tool_calls,
  max_tool_calls, max_turns}`, plus the cache `usage` above. The counters
  live in RAM since the seat's boot, so you can see how often Claude is
  asked to check in and whether 50 calls is too tight.

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
  Each tool call is a finished tool part with its input and output. The
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

## Billing

The rung's usage is billed to the Anthropic API key, like any API call on
that key.

The rules above are enforced in code (`membrane/claude_agent.py`,
`membrane/lanes.py`, `membrane/server.py`, `membrane/cli.py`) and tested
(`tests/test_claude_agent_384.py`, `tests/test_claude_agent_tools_389.py`,
`tests/test_claude_agent_apikey_413.py`).
