---
name: membrane-codegraph
description: Answer who-calls-X / what-does-X-call / blast-radius questions about THIS repo from membrane's AST call graph. Use before editing a function or file ("what breaks if I change X?", "who calls this?", "impact of touching file.py"), with the membrane: codegraph frames.
---

# Query the codegraph

This skill is served BY MEMBRANE's proxy: the turn is intercepted pre-gate
and answered from a stdlib-AST call graph built at server start. No model
call, nothing learned, read-only by construction.

## How to ask

Canonical frames (send as a chat turn, no tools):

    membrane: codegraph stats
    membrane: codegraph impact <symbol>
    membrane: codegraph impact <path/to/file.py>
    membrane: codegraph callers <symbol>
    membrane: codegraph callees <symbol>

Intent phrasing also intercepts ("who calls learn_pair?", "impact of
touching membrane/watch.py", "blast radius of ..."). The canonical frame is
the deterministic path; when in doubt, use it.

## Reading the answer

- `impact` walks CALLERS outward (depth-capped BFS): depth 1 = direct
  callers, deeper = transitive. It is NAME-resolved: aliases, dynamic
  dispatch and same-named methods on different classes fold together.
  Treat it as a reading aid BEFORE an edit, not a compiler-grade analysis.
- The graph is built once per proxy process: after a big refactor, restart
  the proxy (`membrane opencode`) to re-scan.
- For doc/knowledge questions ("what does the project say about X"), use
  membrane-query-field instead -- the FIELD, not the graph.