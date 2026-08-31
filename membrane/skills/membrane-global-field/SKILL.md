---
name: membrane-global-field
description: Read or write membrane's GLOBAL commons -- memories shared across all projects. Use for "remember globally", "promote #N", "forget globally", listing scope suggestions, or when a fact applies beyond this repo (user preferences, machine setup, team conventions).
---

# The global field (the commons)

This skill is served BY MEMBRANE. The commons is a small, curated memory file
shared by every membrane on this machine (`~/.membrane/global-field.json`):
promote only what is true BEYOND this project (user preferences, machine
layout, team conventions) -- never project-local details.

## Commands (send as chat turns, no tools)

    membrane: remember globally: <the fact>     write a new note now
    membrane: promote #N                        promote local exemplar #N
    membrane: forget globally <spec>            "3-7, 9" | topic text | everything
    membrane: suggest                           list pending scope suggestions
    membrane: suggest drop <slot>               dismiss one suggestion

Scope suggestions (`membrane: suggest`) are the scope axis's picks -- local
memories that LOOK commons-worthy. Ratify one by promoting its local index;
dismiss one with the drop form. Nothing enters the commons without your hand.

## Reading the answers

- Promote replies show the global index, the owner field-id, and the vote
  count. A second project promoting the SAME text adds its vote (consensus);
  two distinct fields' notes auto-merge from the ledger shards.
- Global memories surface in ordinary recall tagged
  `[global · owned by <fid> · sim]` -- treat them as context from another
  project's lived experience.

## Guards

- Forgetting globally never touches ANY project's local field.
- Promote/forget are operator turns: the model's prose can read the commons
  but never writes it (a model-echoed promote frame is report-only).
- The layer is off entirely with `--no-global`.

## Related skills

- membrane-write-memory: local (this-project) memory
- membrane-forget-memory: local deletions