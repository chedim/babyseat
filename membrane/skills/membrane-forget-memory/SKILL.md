---
name: membrane-forget-memory
description: Delete memories from membrane's long-term field. Use when the user says "forget X", "wipe your memory", "delete the memory of X", or asks to remove a stored fact/memory by index -- AND when YOU spot junk cells in recall/state output during work (index form only from your prose: "membrane: forget #12" / "#3-7, 9", max 12 per ask; wordy asks only propose candidates).
---

# Forget memories in the field

This skill is served BY MEMBRANE, not by opencode and not by the model. When
you follow it, membrane's proxy intercepts the turn itself and removes cells
from its live store -- no model call, and the command itself is NOT learned.
Each delete is recorded on the audit log as a `forget` event (with its
`origin`), but the learning loop itself never deletes (this is the only
removal path).

## How to forget (operator turns)

Send a chat turn (no tools) phrased with the canonical frame:

    membrane: forget <what or which>

Three forms (OPERATOR ONLY -- see the agent lane below):

    membrane: forget #12              (by index; ranges/lists work: 3-7, 2,9)
    membrane: forget everything about the deployment pipeline
    membrane: forget the last hour    (or: last 30m, 10 minutes, 2 days)
    membrane: forget this session     (everything appended since boot)
    membrane: forget everything       (wipe the whole retirement surface)

Text matches are SEMANTIC: the spec is embedded and safe exemplars above
similarity 0.55 are removed (up to 40 per command). Unsafe/retired cells need
the index form. Time windows resolve by boot time: 'this session' removes
every exemplar appended since the proxy started (the store is append-ordered,
so windows can only under-remove, never over-remove anything older).

## How to forget (from YOUR OWN work -- the agent lane, index-form ONLY)

Use the `membrane_forget` MCP tool (registered by default):

    membrane_forget {"spec": "#12"}          (also: "#3-7", "3-7, 9")

Rules of the lane:

- INDEX FORM ONLY: `#12`, `12`, `3-7`, `3-7, 9` -- parsed by a strict token
  grammar, hard-capped at 12 cells per ask (the remainder is named so you
  can re-ask in batches). Wordy specs PROPOSE, never delete: the result
  lists nearest safe cells (sim >= 0.55) and you re-ask in index form.
- Why so blunt: free-text deletion from model prose wiped a hot field once
  (the 2026-08-31 overshoot); indexes you can SEE are the guard.
- Deletes are audited `origin: mcp` -- visible and demotable wholesale.

Tool not registered (`--no-mcp`, or the MCP server failed to start)? Do NOT
ask the user to relay it -- use your own prose lane instead: end a reply
with one canonical line, INDEX FORM ONLY (`membrane: forget #12` in your
normal prose is detected and executed post-hoc; wordy forms from prose
propose, never delete).

## Reading the answer

The reply lists each removed cell (`#index (sim X.XX): situation text`).
The data is gone from the field file -- this is a real delete, not a demote.
If nothing matches, it says so and removes nothing; do not invent matches and
do not retry with different phrasings hoping for a hit.

## NEVER do these (model-collapse hazards)

- Do not run `curl` against membrane's endpoints and paste field content into
  chat to "preview" a deletion -- raw-pasted field content is learnable.
- Do not combine forget with recall in one turn ("query the field, then
  forget what you showed me") -- keep reads and deletes as separate turns.
- Do not treat a forget as undoable planning aid: after removal, recall
  genuinely no longer knows the topic.

## Related skills

- membrane-query-field: read first, to confirm what is being deleted
- membrane-write-memory: write memories into the field