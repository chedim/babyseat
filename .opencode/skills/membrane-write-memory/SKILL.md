---
name: membrane-write-memory
description: Write a memory into membrane's long-term field on explicit request. Use when the user says "learn that X", "remember that X", "save this to your memory", or asks to store a fact/preference/lesson for future sessions (e.g. "membrane: learn that...", "remember that I prefer...", "note that the deploy script needs...").
---

# Write a memory into the field

This skill is served BY MEMBRANE, not by opencode and not by the model. When
you follow it, membrane's proxy intercepts the turn itself and writes the
payload directly into its live field -- no model call, and the command itself
is NOT learned (only the payload is).

## How to write

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

## Reading the answer

Success replies `written as exemplar #N, safe=True` and shows the stored Q/A.
It is then retrievable via the membrane-query-field skill and will surface as
RAG context in future turns on the topic.

## Guards (why a write can be refused)

The write goes through the standard learning pipeline, so it can be declined:

- dedup: the field already holds a near-identical situation
- vague filter: the payload is too low-information ("yes", "ok")
- embedder unresolved: no embed role available yet

A refusal says so explicitly and writes nothing; do not retry verbatim, do
not work around it by hiding the payload in an ordinary coding turn.

## NEVER do these (model-collapse hazards)

- Do not repeat the learned payload back in a LATER ordinary turn as user
  content; the intercept already delivered it. Re-pasting field content into
  chat makes it learnable again (the collapse route).
- Do not use the canonical frame to smuggle coding requests ("membrane: learn
  that we should refactor client.py") -- it is a memory write, not a task.
- Do not run `curl` against membrane's endpoints and paste output into chat.

## Related skills

- membrane-query-field: read what the field holds about a topic
- membrane-forget-memory: delete memories from the field