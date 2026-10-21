# The claude-agent rung: Claude through the Agent SDK

The seat can route a turn to Claude through the
[Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) (issue #384).
The SDK drives your local `claude` CLI with the CLI's own login. membrane
does not read that login's token and does not call an Anthropic API itself.

**The rule:** Claude's replies are shown and never learned.

## Turning it on

1. Install the optional extra on the seat's Python:
   `pip install 'membrane[claude-agent]'` (or `pip install claude-agent-sdk`).
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

**It is text-only.** Claude Code's own tools are switched off: no built-in
tools, nothing auto-allowed, no MCP servers, no settings files or
`CLAUDE.md`, and one turn. The SDK runs its own agent loop over the tools
it is given, and it cannot hand a tool call back to membrane unexecuted. So
membrane's tool definitions are not passed on, and the reply never carries
tool calls. membrane's tool loop and MCP surface remain the only tools; on
this rung Claude answers in text. The bullet-salvage that turns a written
tool call into a real one is also off for Claude replies.

The turn's system messages become the SDK's system prompt. These are the
doctrine, membrane's context block and the speech footer. The rest of the
conversation goes in as the prompt, ending on the latest message. The reply
comes back as an ordinary OpenAI response, streamed or buffered, with its
usage.

## What is learned, what is shown

| | Learned (field capture, claims, the night, the voice, the predictor, absorb) | Shown |
|---|---|---|
| The person's message | yes | yes |
| Tool results (file contents, command output: world data) | yes | yes |
| Claude's reply text and reasoning | **never** | yes, in the transcript |
| Claude's tool-call arguments | **never** (the rung makes none) | — |

- **Capture.** The proxy gives the field the person's side only
  (`on_turn(query, "", "")`). The A/B judge, the frame lint and the call
  capture skip the reply. Claims and the night read the field's events, so
  no Claude text reaches them.
- **The transcript.** The reply stays in the session transcript, so the chat
  shows it and the conversation can continue. Replaying it as history to the
  next turn is conversation, not learning. The transcript row is marked
  `madeBy: "claude-agent"`. The voice's history, its recall pool and the
  predictor skip marked rows. Absorb's fail-closed rule covers them too: a
  session that contains one is never absorbed.
- **The rule keys on the provider, not the model's name.** The rung's
  router record has `backend: "claude-agent"`. The proxy stamps every
  response and chunk with `membrane_provider: "claude-agent"`, and the row
  mark comes from that stamp. Renaming a model cannot get its replies past
  the rule.

## Policy

This rung relies on Anthropic allowing Agent SDK use with a Claude plan.
Anthropic's help article
[Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540)
describes the SDK as usable with Pro and Max plans. Its usage counts
against the plan's limits; as of the article's 2026-10-07 update, Max and
Team plans also include monthly API credits that cover the Agent SDK. Check
the article and Anthropic's current terms before turning the rung on; they
can change.

Claude's outputs never becoming learned material is the reason for the rule
above. It is enforced in code (`membrane/claude_agent.py`) and tested
(`tests/test_claude_agent_384.py`).
