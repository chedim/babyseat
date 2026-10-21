# The claude-agent rung: Claude through the Agent SDK

The seat can route a turn to Claude through the
[Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) (issue #384).
The SDK drives your local `claude` CLI with the CLI's own login. membrane
does not read that login's token and does not call an Anthropic API itself.

**The rules (issue #389):** Claude's replies are learned like any other
model's. Claude gets membrane's own tools, and only those, inside the SDK's
agent loop.

## Turning it on

1. Install the optional extra on the seat's Python:
   `pip install 'babyseat[claude-agent]'` (or `pip install claude-agent-sdk`).
2. Install Claude Code and log in once: run `claude`, or `claude auth login`.
   The SDK also ships its own copy of the CLI. membrane uses the `claude`
   on your PATH first, and that bundled copy if there is none.
3. Run `membrane auth claude-agent`. It checks that the SDK is installed and
   that `claude auth status` reports a login. Then it stores a non-secret
   mark (`"claude-agent": "cli-login"`) in `~/.membrane/auth.json`. No key or
   token is stored. You can set `MEMBRANE_CLAUDE_AGENT=1` instead.
4. Restart the seat when it is idle. At boot it registers three rungs:
   `claude-agent:sonnet`, `claude-agent:opus` and `claude-agent:haiku`. They
   use the CLI's model aliases.

A missing SDK or CLI gives a clear error. The boot line says
`NOT usable yet: …`. A turn sent to the rung gets HTTP 503 with what to
install or log in to, and no model is called. If Claude fails during a turn
(for example, you are logged out or hit a limit), the reply says
`[claude-agent] the turn failed: …`.

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
- `MAX_TURNS = 4` caps the agent turns for one answer
  (`membrane/claude_agent.py`). When an answer hits it, you can press
  Continue (see "The turn cap and Continue" below).
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

The turn's system messages become the SDK's system prompt. These are the
doctrine, membrane's context block and the speech footer. The rest of the
conversation goes in as the prompt, ending on the latest message. The reply
comes back as an ordinary OpenAI response, streamed or buffered, with its
usage. Each tool Claude ran rides the reply: a `membrane_tool` chunk when
streamed, a `membrane_tools` list when buffered.

## The turn cap and Continue

An answer that reaches `MAX_TURNS` stops there. The SDK reports this as a
result of subtype `error_max_turns`. membrane does not treat that as an
error, and the answer is not cut off without a word:

- The reply carries `membrane_capped: {session, turns}` (an SSE chunk when
  streamed, a top-level key when buffered). `session` is the SDK's session
  id.
- The transcript row keeps it as `claudeCapped: {session, turns, continued}`.
  The chat shows "Claude stopped at the turn cap (4 turns)" under the answer,
  with a **Continue** button.
- Continue posts the person's message `Continue` with
  `claudeContinue: <message id>`. The seat claims that capped answer once
  and starts a turn that resumes the **same** SDK session (the SDK's
  `resume` option), with a short continue prompt. The turn gets the same
  tools and options and another `MAX_TURNS` turns. If it hits the cap again,
  the new answer offers its own Continue.
- Nothing runs twice. Each capped answer can be continued only once: a
  second press gets HTTP 409 and makes no call. The resumed session already
  holds the earlier tool calls and their results, and none of them is run
  again.
- Continue is a person's action only. Nothing continues by itself, and a
  session on a background lane (a watch loop, a mind) refuses it. Typing
  "Continue" as an ordinary message does not resume a session.
- Diagnostics: `GET /membrane/diag/claude-agent` (localhost only) returns
  `{cap_hits, continues, max_turns}`. The counters live in RAM since the
  seat's boot, so you can see whether 4 turns is too tight.

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
- **Absorb** (importing external Claude Code and OpenCode sessions) keeps
  its older, separate rule: a session answered by an Anthropic model is
  skipped whole, and that includes a `claude-agent:*` model id or a marked
  row.

## Policy

This rung relies on Anthropic allowing Agent SDK use with a Claude plan.
Anthropic's help article
[Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540)
describes the SDK as usable with Pro and Max plans. Its usage counts
against the plan's limits; as of the article's 2026-10-07 update, Max and
Team plans also include monthly API credits that cover the Agent SDK. Check
the article and Anthropic's current terms before turning the rung on; they
can change.

The rules above are enforced in code (`membrane/claude_agent.py`,
`membrane/lanes.py`, `membrane/server.py`) and tested
(`tests/test_claude_agent_384.py`, `tests/test_claude_agent_tools_389.py`).
