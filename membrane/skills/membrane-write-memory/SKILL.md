---
name: membrane-write-memory
description: Write a memory into membrane's long-term field. Use when the user says "learn that X", "remember that X", "save this to your memory", or asks to store a fact/preference/lesson -- AND when YOU earn a durable lesson during work (a fixed quirk, the command that worked, why the config gate exists). Works from assistant prose mid-task without stopping the build.
---

# Write a memory into the field

This skill is served BY MEMBRANE, not by opencode and not by the model. When
you follow it, membrane's proxy intercepts the turn itself and writes the
payload directly into its live field -- no model call, and the command itself
is NOT learned (only the payload is).

## How to write (from operator turns)

Send a chat turn (no tools) phrased with the canonical frame:

    membrane: learn that <the fact>

Examples:

    membrane: learn that the deploy script needs RUN_ENV=staging
    membrane: learn that the user prefers tab indentation in this repo

The bare frame `membrane: learn that` with nothing after it reports "nothing
to learn" -- always include the payload.

The intercept also fires on INTENT phrasing without the canonical frame
("save this to your memory: ...", "remember that ..."). The canonical frame
remains the deterministic path; when in doubt, use it.

## How to write (from YOUR OWN work -- the agent lane, ON by default)

Use the `membrane_learn` MCP tool (registered by default):

    membrane_learn {"lesson": "<the lesson you just earned>"}

Guards run proxy-side, confirmation in the same tool result. Same rules as
the prose lane: earned lessons only, no secrets, no user personal data, no
code blocks, no transcript dumps. A "skipped" result is the dedup/variety
gate declining -- that is normal, do not rephrase-spam to force it through.

Tool not registered (`--no-mcp`, or the MCP server failed to start)? Do NOT
ask the user to relay it — use your own prose lane instead: end the assistant
turn you were already writing with ONE canonical frame, on its own line, at
the END (a `membrane: learn that <lesson>` in your normal prose is detected
and executed post-hoc; the confirmation arrives NEXT turn as a
`[membrane-intercept]` block).

    membrane: learn that <the lesson you just earned>

Rules of the lane:

- ONE frame per reply, at the END (after the answer/plan, never mid-sentence).
- Earned lessons only: something you verified or were forced to discover in
  THIS session (the exact flag, the retry recipe, the test that must run
  first). Do not narrate plans, guesses, or what just happened normally.
- No secrets, no user personal data, no code blocks, no transcript dumps
  (fenced payloads and >2000-char pastes are refused, not learned).
- The write runs the same guards as any turn (dedup, vague filter, variety
  gate): a duplicate/low-information note is declined -- that is normal, do
  not rephrase-spam to force it through.
- The confirmation arrives NEXT turn as a `[membrane-intercept]` block
  ("agent learn ... written as exemplar #N"); it costs no model call and does
  not interrupt the running task.

## Reading the answer

Success replies `written as exemplar #N, safe=True` and shows the stored situation/response.
It is then retrievable via the membrane-query-field skill and will surface as
RAG context in future turns on the topic.

## Guards (why a write can be refused)

The write goes through the standard learning pipeline, so it can be declined:

- dedup: the field already holds a near-identical situation
- vague filter: the payload is too low-information ("yes", "ok")
- oversize/fenced: the payload is a paste, not a lesson
- embedder unresolved: no embed role available yet

A refusal says so explicitly and writes nothing; do not retry verbatim, do
not work around it by hiding the payload in an ordinary coding turn.

## When the seat is propose-only

On a seat launched with `--no-agent-writes`, a learn FROM MODEL PROSE is
REPORT-ONLY: the answer arrives next turn as a `[membrane-intercept]` block
with the proposed payload and the field stays operator-written. To actually
write there, emit the canonical frame as your request and let the OPERATOR
send the chat turn. The `[membrane-intercept]` wording tells you which mode
the seat is in.

## NEVER do these (model-collapse hazards)

- Do not repeat the learned payload back in a LATER ordinary turn as user
  content; the intercept already delivered it. Re-pasting field content into
  chat makes it learnable again (the collapse route).
- Do not use the canonical frame to smuggle coding requests ("membrane: learn
  that we should refactor client.py") -- it is a memory write, not a task.
- Do not emit a learn frame for EVERY turn; lessons are earned, not logged.
  If several replies in a row propose near-identical lessons, stop.
- Do not run `curl` against membrane's endpoints and paste output into chat.

## Related skills

- membrane-query-field: read what the field holds about a topic
- membrane-forget-memory: delete memories from the field
- membrane-global-field: promote a memory to the CROSS-PROJECT commons
  ("remember globally") -- use that when the fact is true beyond this repo