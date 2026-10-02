---
name: membrane-query-field
description: Query membrane's long-term field memory for what it remembers about a topic. Use when the user asks what the agent remembers/learned about X, before redoing known work, or when past-session context would help (e.g. "what do you remember about...", "query the field for...", "what have we learned about...").
---

# Query the membrane field

This skill is served BY MEMBRANE, not by opencode and not by the model. When
you follow it, membrane's proxy intercepts the turn itself and answers from
its live field — no model call, and the turn is NOT learned into the field.

## How to query

Use the `query` MCP tool (registered by default):

    query {"topic": "<what to recall>"}

Structured arguments, answer in the same turn, same guarantees as the
frame: read-only, no model call, never learned.

Tool not registered (`--no-mcp`, or the MCP server failed to start)? Do NOT
ask the user to relay it — use your own prose lane instead: a bare recall
frame in your assistant-turn text ("what do you remember about X") is
detected pre-gate and the answer arrives NEXT turn as a
`[membrane-intercept]` system block. Same endpoint, same guards.

Examples:

    membrane: query the field for opencode model filtering
    membrane: query the field for RAG compactor behavior
    membrane: what do you remember about knee.py

The intercept also fires on INTENT phrasing without the canonical frame
("search your memory for ...", "what do you know about ..."). The canonical
frame remains the deterministic path; when in doubt, use it.

Inside your own assistant turns: a bare recall frame in your prose ("what do
you remember about X") is detected pre-gate and the answer arrives next turn
as a `[membrane-intercept]` system block — no dedicated chat turn needed.

## Reading the answer

The reply is a formatted list of `(<similarity> [<tag>]) Q:/A:` exemplar pairs,
newest/most-similar first, SAFE exemplars only. Treat them exactly like
membrane's recall blocks: context that may be outdated or superseded — the
latest correction in a cluster wins. If nothing is in the field, say so and
move on; do not invent memories.

## NEVER do these (model-collapse hazards)

- Do not restate the retrieved memories as a question back to membrane; the
  turn is already intercepted, but paraphrase-echoes in ordinary coding turns
  can still be learned — keep recall reads read-only.
- Do not run `curl` against membrane's endpoints and paste output into chat;
  raw-pasted field content is learnable and would feed the field back into
  itself.
- Do not summarize the field back into the chat "for the record".