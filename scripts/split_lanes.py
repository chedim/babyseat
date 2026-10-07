"""THE SPLIT SCRIPT (run once): opencode.py's per-turn lanes -> membrane/lanes.py.

Deterministic AST cuts, verified, idempotent-ish (refuses to run twice).
The result: lanes.py = the turn-lane helpers + the OpenAI handler + the
speech lane (self-contained imports, NEVER imports .opencode);
opencode.py = the header + the explicit re-export + main().
"""
import ast
import re
import sys

SRC = 'membrane/opencode.py'
LANES = 'membrane/lanes.py'

src = open(SRC).read()
lines = src.split('\n')
tree = ast.parse(src)

# ---- the landmarks (top-level bodies)
first_def = next(n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)))
main_n = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == 'main')
speech_i = next(i for i, l in enumerate(lines)
                if l.startswith('# ---- the speech lane'))
guard_i = next(i for i, l in enumerate(lines) if l.startswith('if __name__'))
first_line = first_def.lineno - 1            # 0-idx
main_line = main_n.lineno - 1

# ---- the slices (0-idx line ranges)
header_lines = lines[:first_line]
turn_lines = lines[first_line:main_line]
main_lines = lines[main_line:speech_i]
speech_lines = lines[speech_i:guard_i]

# ---- the header's import statements (verbatim)
header_text = '\n'.join(header_lines)
head_tree = ast.parse(header_text)
import_stmts = []
last_import_end = 0
for n in head_tree.body:
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        import_stmts.append('\n'.join(header_lines[n.lineno-1:n.end_lineno]))
        last_import_end = max(last_import_end, n.end_lineno)
# ---- the constants AFTER the last import (they belong to the lanes too)
const_stmts = []
for n in head_tree.body:
    if isinstance(n, ast.Assign) and n.lineno > last_import_end:
        const_stmts.append('\n'.join(header_lines[n.lineno-1:n.end_lineno]))

# ---- lanes.py assembly
doc = '''"""THE TURN LANES (the split of opencode.py, 2026-10-07): the proxy's
whole per-turn surface in one module -- the canonical-frame intercepts,
the RAG/banner/intents/pause builders, the capture hooks, the OpenAI
handler, and the speech lane. opencode.py keeps the CLI + boots (main)
and re-exports every name (the import surface never changed). lanes.py
carries its OWN imports -- NEVER imports .opencode (the -m run's
re-import cycle is structurally impossible here)."""
from __future__ import annotations

'''
body = ('\n'.join(import_stmts) + '\n\n\n'
        + '\n\n'.join(const_stmts) + '\n\n\n'
        + '\n'.join(turn_lines).rstrip('\n') + '\n\n\n'
        + '\n'.join(speech_lines).rstrip('\n') + '\n')
body = re.sub(r'\n{4,}', '\n\n\n', body)
open(LANES, 'w').write(doc + body)

# ---- opencode.py assembly: the header (imports kept) + the re-export + main + guard
lanes_tree = ast.parse(open(LANES).read())
exported = set()
for n in lanes_tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        exported.add(n.name)
    elif isinstance(n, ast.Assign):
        exported |= {t.id for t in n.targets if isinstance(t, ast.Name)}
exported = sorted(exported)

reexport = ('# THE SPLIT (2026-10-07): the per-turn lanes (the canonical-frame\n'
            '# intercepts, the banners, the intents/pause builders, the OpenAI\n'
            '# handler, the speech lane) live in membrane/lanes.py; this module\n'
            '# keeps the CLI + the boots (main) and re-exports the lane surface\n'
            '# (the import surface never changed -- every membrane.opencode.X\n'
            '# import works).\n'
            'from .lanes import (  # noqa: E402,F401\n'
            + ',\n'.join(f'    {n}' for n in exported)
            + ',\n)\n\n\n')
# the head WITHOUT the moved constants:
keep_head = header_text.split('\n')
keep = []
const_names = {re.match(r'(\w+)', c).group(1) for c in const_stmts}
for l in keep_head:
    m = re.match(r'(\w+)\s*=', l)
    if m and m.group(1) in const_names:
        continue
    keep.append(l)
new_oc = ('\n'.join(keep).rstrip('\n') + '\n\n\n' + reexport
          + '\n'.join(main_lines).rstrip('\n') + '\n\n\n'
          + 'if __name__ == "__main__":\n    main()\n')
open(SRC, 'w').write(new_oc)

# ---- verification
ast.parse(open(LANES).read())
ast.parse(open(SRC).read())
print(f'lanes.py: {open(LANES).read().count(chr(10))} lines')
print(f'opencode.py: {new_oc.count(chr(10))} lines; re-exports: {len(exported)}')