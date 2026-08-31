---
name: membrane-forget-memory
description: Delete memories from membrane's long-term field on explicit request. Use when the user says "forget X", "wipe your memory", "delete the memory of X", or asks to remove a stored fact/memory by index (e.g. "membrane: forget...", "forget everything about...", "delete that memory").
---

# Forget memories in the field

This skill is served BY MEMBRANE, not by opencode and not by the model. When
you follow it, membrane's proxy intercepts the turn itself and removes cells
from its live store -- no model call, and the command itself is NOT learned.
Deletion is the operator's hand: it is recorded on the audit log as a
`forget` event, but the learning loop itself never deletes (this is the only
removal path).

## How to forget

Send a chat turn (no tools) phrased with the canonical frame:

    membrane: forget <what or which>

Three forms:

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