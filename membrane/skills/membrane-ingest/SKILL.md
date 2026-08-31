---
name: membrane-ingest
description: Cold-start a project's memory field by absorbing existing docs/README/notes into it. Use when the field is empty or thin and the knowledge already exists on disk ("ingest the docs", "cold-start the field", before relying on recall in a fresh project).
---

# Cold-start the field (ingest)

The field learns from lived turns -- which makes the first week empty.
When the knowledge ALREADY exists (README, docs/, design notes), ingest it:
every chunk goes through the SAME learn_pair guards as any turn (vague,
dedup, variety), one embed per chunk, NO model call.

## The operator command

Run it in the terminal (it is a CLI surface, not a chat frame):

    membrane ingest                 # docs/ + README + top-level notes
    membrane ingest docs/ DESIGN.md # explicit paths
    membrane ingest --dry-run       # count chunks, write nothing

The pair form: situation = `<rel-path> :: <heading>`, action = the chunk.
A second run dedups (same guard as any learn), so re-ingesting is safe.

## The agent path (from the window)

The agent does NOT run bash incantations for this: ask the USER to run
`membrane ingest`, or (when the user asks you to do it) run
`python -m membrane.ingest --dry-run` first, show the counts, then run it
for real on approval. Ingested cells are audited `origin: ingest` and
forensically separable from lived turns -- a time-window forget can remove
them wholesale.

## What never to ingest

- .membrane/ runtime state, audit logs (`.jsonl`), node_modules, build/
- secrets-bearing files (.env, keys) -- the default sweep never touches
  them, and you must not pass them as explicit paths either
- transcript dumps or raw field content (model-collapse hazard, same as
  the query-field skill's rules)