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

**Background lanes never use it.** This covers the thinker, the dreamer, the
night, the judges, the watch loops and the field's own calls. A background
chat call to the rung returns nothing and makes no call. A loop session
pinned to it falls back to the seat's model. Only a person's lanes can take
it: the chat, plan, the phone and a speaker.

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
  answer stops at 50 tool calls, where you can press Continue (see
  "Check-ins instead of a turn cap" below).
- The turn's own tool definitions (the chat lane's surface) are not passed
  on. The reply never carries `tool_calls` for the caller to run, and the
  bullet-salvage that turns written calls into real ones stays off for
  Claude rounds, so nothing runs twice.

The tools Claude gets:

| Tool | Writes? |
|---|---|
| `query`, `state`, `intents`, `entity_tracks`, `entities_related`, `entity_query`, `graph` | read-only |
| `entity_read` | reads, but a dynamic entity's getter script fires (and `run: true` runs it) |
| `learn`, `forget` | writes field notes / deletes cells |
| `intend`, `fulfill` | writes goals |
| `entity_add`, `entity_edit`, `entity_remove`, `entity_inherit` | writes entities (`entity_edit` can set scripts) |
| `entity_link`, `entity_unlink` | runs command entities / retires the links |
| `entity_watch`, `entity_unwatch`, `field_watch`, `attention`, `junk_cut` | writes tracks, watches and dials |
| `peer_post` | posts on the peer channel |

The turn's stable system messages become the SDK's system prompt: the
doctrine and the client's own instructions. The rest of the conversation goes
in as the prompt, ending on the latest message (or, when the conversation's
SDK session already holds the earlier turns, only the latest message: see
[The conversation cache](#the-conversation-cache)). membrane's per-turn blocks
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
`usage: {calls, input, cache_read, cache_write, output, per_call}`: sums
since the seat's boot (`input` is the uncached input) and the per-call
averages.

## The conversation cache

The system prompt alone is not enough: a call that renders the whole
conversation as one new text pays for all of it again (or writes it to the
cache again, at 1.25x) on every turn. So each conversation keeps one SDK
session, the way Claude Code keeps its own:

- The first turn (or any turn membrane cannot match, below) is rendered in
  full, as described above, in a fresh SDK session.
- A turn that continues that conversation resumes the session (the SDK's
  `resume`) and sends only the tail: membrane's per-turn blocks in
  `<membrane-context>`, then the latest message, then the speech footer if
  any. The CLI replays the session's earlier turns as real messages, its own
  tool calls and results included, and puts its cache breakpoint at the end
  of every request. The next call's request starts with the previous one
  byte for byte, so the earlier turns are read from the cache and only the
  tail and the last answer are new.

membrane stays the source of truth. It keeps, in RAM, the conversation each
session carries (roles and texts). A turn resumes a session only when the
conversation it sends, minus the latest message, ends on an answer and is a
window of that session's conversation: the same roles and texts in order,
where a text may be the capped head of the held one (the chat replays the
last `HISTORY_MAX_MESSAGES` rows, each capped). A session is taken by one
turn at a time, so a regenerate (the same conversation again, with a new
last message) finds nothing to resume. Everything else is rendered in full in
a fresh session: an edited answer, an answer another rung gave, an
unknown conversation, a seat restart, a session that stopped at the ceiling.
A resume that fails before Claude says anything (the CLI lost the session,
or refused it) is retried once, rendered in full, in the same answer.

Two consequences. The session holds every turn since it began, not the
chat's sliding window (the CLI compacts it when it nears the model's
context), and each turn's per-turn blocks stay in the session as they were
sent: rewriting them would break the cache from that point on. Up to
`CONVO_SESSIONS_MAX` (64) conversations are remembered, each by its last
`CONVO_HELD_MAX` (24) messages; the oldest goes first, and its next turn is
rendered in full.

`GET /membrane/diag/claude-agent` adds `conversation: {resumed, full, lost,
sessions, sessions_max}`: turns that resumed a session, turns rendered in
full, resumes that failed and fell back, and the sessions held now. The
per-call `usage` there shows the effect: on a resumed turn `cache_read`
carries the earlier turns.

## Check-ins instead of a turn cap

One answer is **one continuous SDK run**. There is no fixed batch of turns.
Instead, a `PreToolUse` hook (the SDK's `hooks` option, `membrane/claude_agent.py`)
keeps Claude talking to the person while it works:

- **Check-ins:** Claude may make at most `CHECKIN_TOOL_CALLS = 7` tool calls
  in a row without writing text to the person. Any assistant text, such as
  a progress or status line in the reply, resets the count to 0. The 8th
  call in a row is denied with "Tell the person where you are first (one
  or two lines), then continue." Claude gets that as the tool's result,
  says where it is, and goes on in the same run. The denied call never
  runs. The chat shows no error for it: a denied call is Claude's
  feedback, not a tool part.
- **The ceiling:** at most `MAX_TOOL_CALLS_PER_ANSWER = 50` tool calls per
  answer (about seven check-ins). The call past it is denied and the hook
  stops the run.
- `max_turns` is only a backstop: `MAX_TURNS = 2 * MAX_TOOL_CALLS_PER_ANSWER`.
  Every allowed call may take a turn and be denied once first, so it never
  binds before the ceiling. If it ever does, the answer is marked the same
  way as at the ceiling.
- Both values were approved by the operator on 2026-10-21.
- Calls in one assistant message count one by one. The hook waits until
  membrane has read the message that carries the call, so words in the
  same message count first.

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
