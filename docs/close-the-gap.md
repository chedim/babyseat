# Closing the TencentDB-Agent-Memory gap (2026-08-31)

Comparison source: https://github.com/TencentCloud/TencentDB-Agent-Memory
(25k-star team memory hub: Chat Memory / Skill / LLM-Wiki / CodeGraph assets
behind an ACL governance panel).

## The verdict, then the two lanes we took

Tencent's memory is a WAREHOUSE (curated text assets, async LLM pipelines,
classical BM25+vector+RRF retrieval, governed by versions/ACLs). Membrane is
a DYNAMICAL SYSTEM (write-time physics: vague/dedup/variety/gate, two-
authority demotion, self-knowledge via free_energy/eff_rank, spatial
watches). Convergent design: proxy interception, layered distillation,
private-by-default sharing. Divergent core: their selection pressure is LLM
judgment + admin policy; ours is geometry.

We did NOT adopt their governance model (ACL panel, versioned assets,
per-agent loadouts): the covenant is opt-in writes + audit trail, not admin
UI. Two gaps were real and are now closed:

## 1. Cold-start ingest (`membrane.ingest`, `membrane ingest`)

Tencent loads "the save file" on day one. Membrane's first week is empty
because the field only learns from lived turns -- but the knowledge often
ALREADY exists on disk.

- `membrane ingest` (or `python -m membrane.ingest`): default sweep = docs/,
  README, top-level notes; explicit paths win. Chunks split on markdown
  headings (else paragraph-bias \u22481200-char budget), each absorbed through
  `Membrane.learn_pair` -- the SAME guards as any turn (vague/dedup/variety),
  one local embed per chunk, NO model call.
- Pair form: situation = `<rel-path> :: <heading>`, action = the chunk body,
  so a future topical query retrieves the doc text as the action.
- Re-ingest is safe (dedup merges). Cells are audited `origin: ingest`;
  a time-window or topic forget removes them wholesale without touching
  lived turns.
- Refusals: .membrane/, .jsonl state, node_modules/build/venvs, >512KB
  files, NUL-probed binaries, and (by covenant) secrets files.

## 2. CodeGraph-lite (`membrane.codegraph`, `membrane: codegraph`)

Their CodeGraph asset (symbols, call relationships, impact paths) retold
with zero dependencies: stdlib `ast` over every .py under the project root.

- Symbols with file:line (functions, methods, classes, dotted qualnames);
  per-symbol call lists; a name-resolved callers index (last-segment +
  dotted-suffix match -- aliases/dynamic dispatch fold; a heuristic, stated
  as such in every answer).
- Questions answered: `stats`, `impact <sym|file.py>` (reverse-BFS blast
  radius, depth 4), `callers <sym>`, `callees <sym>`.
- Surfaces: CLI (`membrane codegraph stats`), pre-gate intercept
  (`membrane: codegraph ...`, intent forms "who calls X?" / "impact of
  touching file.py" gated on symbol-like targets), thought-path asks from
  the agent's own prose, and a line in `field state`.
- The graph builds lazily once per proxy process; a refactor needs a proxy
  restart to re-scan (stated in the answer).

## What remains deliberately un-adopted

- ACL/versioned governance panel -- anti-covenant (see above).
- LLM-Wiki / doc-structured pages -- the ingest lane IS our doc memory;
  a link-graph wiki layer would need an edit-time model pipeline we
  deliberately don't run. Revisit only if recall quality on docs proves
  insufficient.
- Their benchmark (PersonaMem) -- valuable, but a project of its own.

## Test coverage

`tests/test_ingest_codegraph.py`: sweep rules (skip dirs/exts/size),
chunk shapes, ingest-then-dedup, --max truncation, symbol extraction,
skip-dir graph exclusion, name-resolved direct+transitive callers, file
impact, query text forms, and the matcher's strictness (coding turns like
"who calls the api" must NOT intercept).