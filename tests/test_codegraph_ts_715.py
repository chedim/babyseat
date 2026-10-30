'''#715 part C: codegraph's symbols and call edges come from the tree-sitter
trees, so every language with a grammar (py, sh, js) gets callers, callees and
impact. Python keeps exactly what the stdlib-ast scan gave (the parity oracle
below is the old scanner, kept verbatim for this test).'''
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import codegraph as CG  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# ---- the oracle: the pre-#715 stdlib-ast scan, verbatim ---------------------------------------
def _collect_calls(fn):
    names = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            names.append(f.id)
        elif isinstance(f, ast.Attribute):
            parts = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            if parts:
                names.append(".".join(reversed(parts)))
    return sorted(set(names))


def _ast_scan(tree, rel):
    syms = {}

    def _dflt(node):
        try:
            return ast.unparse(node)
        except Exception:
            return "?"

    def _args(ch):
        a = ch.args
        ndef = len(a.defaults)
        pos = []
        for i, x in enumerate(a.args):
            d = None
            if ndef and i >= len(a.args) - ndef:
                d = _dflt(a.defaults[i - (len(a.args) - ndef)])
            pos.append((x.arg, d))
        kw = [(x.arg, (_dflt(d) if d is not None else None)) for x, d in zip(a.kwonlyargs, a.kw_defaults)]
        return {"args": pos, "vararg": getattr(a.vararg, "arg", None), "kwonly": kw,
                "kwarg": getattr(a.kwarg, "arg", None)}

    def visit(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + ch.name
                syms[q] = {"file": rel, "line": ch.lineno, "kind": "method" if prefix else "function",
                           "calls": _collect_calls(ch), "sig": _args(ch),
                           "doc": (ast.get_docstring(ch) or "")[:600]}
                visit(ch, q + ".")
            elif isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                syms[q] = {"file": rel, "line": ch.lineno, "kind": "class", "calls": [],
                           "doc": (ast.get_docstring(ch) or "")[:600]}
                visit(ch, q + ".")
            else:
                visit(ch, prefix)
    visit(tree, "")
    return syms


def _repo_py():
    for d, dirs, files in os.walk(REPO):
        dirs[:] = [x for x in dirs if x not in CG._SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield Path(d) / f


def test_python_symbols_match_the_ast_scan_on_every_repo_file():
    bad = []
    n = 0
    for p in _repo_py():
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            want = _ast_scan(ast.parse(src), "x.py")
        except SyntaxError:
            continue
        got = CG.scan_source(src.encode(), "x.py")
        n += 1
        if got != want:
            diff = sorted(k for k in set(got or {}) | set(want) if (got or {}).get(k) != want.get(k))
            bad.append((p.relative_to(REPO).as_posix(), diff[:3]))
    assert n > 100 and not bad, bad[:5]


def test_the_scan_is_not_stdlib_ast():
    src = (REPO / "membrane" / "codegraph.py").read_text()
    assert "ast.parse(src)" not in src and "ast.walk" not in src


SH = 'helper() {\n  echo hi\n}\ngreet() {\n  helper\n  ls -la\n}\n'
JS = ('class Boat {\n  sail(w) { return dock(w); }\n}\n'
      'function dock(b) { return b.tie(); }\nfunction main() { new Boat().sail(1); dock(2); }\n')


def _proj():
    d = Path(tempfile.mkdtemp())
    (d / "run.sh").write_text(SH)
    (d / "web").mkdir()
    (d / "web" / "boat.js").write_text(JS)
    (d / "lib.py").write_text("def py_f():\n    return 1\n")
    return d


def test_shell_and_js_get_symbols_callers_and_impact():
    g = CG.build_graph(_proj())
    assert g["symbols"]["greet"]["calls"] == ["helper", "ls"] and g["symbols"]["greet"]["file"] == "run.sh"
    assert {c["caller"] for c in CG.callers_of(g, "helper")["levels"][0]} == {"greet"}
    assert g["symbols"]["Boat"]["kind"] == "class" and g["symbols"]["Boat.sail"]["kind"] == "method"
    assert {c["caller"] for c in CG.callers_of(g, "dock")["levels"][0]} == {"Boat.sail", "main"}
    assert "b.tie" in g["symbols"]["dock"]["calls"]
    assert "sail" in g["symbols"]["main"]["calls"]
    assert "web/boat.js" in g["files"] and "lib.py" in g["files"]
    assert CG.impact_of_file(g, "run.sh")


def test_a_changed_js_file_is_stale_and_rebuilds():
    d = _proj()
    g = CG.build_graph(d)
    p = d / "web" / "boat.js"
    p.write_text(JS + "function moor() { dock(3); }\n")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    assert "web/boat.js" in CG.stale_paths(g)
    CG.rebuild_paths(g, ["web/boat.js"])
    assert "moor" in {c["caller"] for c in CG.callers_of(g, "dock")["levels"][0]}


def test_a_file_that_does_not_parse_is_skipped_and_keeps_its_old_symbols():
    d = _proj()
    g = CG.build_graph(d)
    (d / "web" / "boat.js").write_text("function (((")
    CG.rebuild_paths(g, ["web/boat.js"])
    assert "dock" in g["symbols"]


def test_a_file_over_the_size_bound_is_not_scanned(monkeypatch):
    d = _proj()
    monkeypatch.setattr(CG, "_MAX_BYTES", 60)
    g = CG.build_graph(d)
    assert "web/boat.js" not in g["files"] and "run.sh" in g["files"]
