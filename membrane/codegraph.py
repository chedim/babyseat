"""CodeGraph-lite: a stdlib-AST call/impact map of THIS repo (Tencent's
CodeGraph asset, retold in membrane's idiom).

Membrane's field remembers situations; it never knew the CODE as a structure.
This module scans every ``*.py`` under a root, extracts symbols (functions,
methods, classes with file:line) and the names each function calls, and
answers THREE questions the field cannot: who calls X, what does X call, and
what is the blast radius of touching file/symbol X (reverse callers, BFS by
name resolution).

Honesty constraint: edges are resolved by NAME (last segment + dotted suffix
match), not by types or imports -- dynamic dispatch, aliases and same-named
methods on different classes fold into one node set. It is a heuristic call
graph: cheap, zero-model, zero-dependency, right far more often than wrong;
good enough to read before an edit, not a compiler-grade analysis.

Zero-model by construction: no embeds, no LLM, pure ``ast``. The opencode
query path builds the graph lazily once per server process and reuses it;
``python -m membrane.codegraph <spec>`` is the CLI surface (``stats`` |
``impact X`` | ``callers X`` | ``callees X`` | ``impact path/to/file.py``).

This closes half of the TencentDB-Agent-Memory gap (CodeGraph + the
cold-start lane in membrane.ingest) WITHOUT inheriting its control-panel
governance: the covenant stays opt-in writes + audit trail, not ACLs.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# directories never scanned (runtime/state/vendor noise, not source)
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "dist",
              ".membrane", ".opencode", "__pycache__", "worlds", ".claude",
              ".idea", ".mypy_cache", ".pytest_cache"}

_MAX_FILES = 4000
_IMPACT_DEPTH = 4


def _py_files(root: Path) -> list[Path]:
    out: list[Path] = []
    if root.is_file():
        return [root]
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
        if len(out) >= _MAX_FILES:
            break
    return out


def _collect_calls(fn) -> list[str]:
    """Every name called inside one function body (``f()``, ``a.b.c()``),
    deduped."""
    names: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            names.append(f.id)
        elif isinstance(f, ast.Attribute):
            parts: list[str] = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            if parts:
                names.append(".".join(reversed(parts)))
    return sorted(set(names))


def _scan_tree(tree: ast.AST, rel: str) -> dict:
    """Qualname -> {file, line, kind, calls} for one module. Nested defs get
    dotted qualnames (``outer.inner``, ``Class.method``)."""
    syms: dict = {}

    def visit(node, prefix: str) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + ch.name
                kind = "method" if prefix else "function"
                syms[q] = {"file": rel, "line": ch.lineno, "kind": kind,
                           "calls": _collect_calls(ch)}
                visit(ch, q + ".")
            elif isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                syms[q] = {"file": rel, "line": ch.lineno, "kind": "class",
                           "calls": []}
                visit(ch, q + ".")
            else:
                visit(ch, prefix)

    visit(tree, "")
    return syms


def build_graph(root: str | Path = ".") -> dict:
    """Scan every .py under root -> {root, files, symbols}. Files that fail
    to parse are skipped silently (a half-broken repo must still graph)."""
    rootp = Path(root).resolve()
    files: dict = {}
    symbols: dict = {}
    for path in _py_files(rootp):
        rel = path.relative_to(rootp).as_posix()
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in src[:1024]:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        syms = _scan_tree(tree, rel)
        try:
            mt = path.stat().st_mtime
        except OSError:
            mt = 0.0
        files[rel] = {"mtime": mt, "symbols": sorted(syms)}
        symbols.update(syms)
    graph = {"root": str(rootp), "files": files, "symbols": symbols}
    graph["callers_index"] = _callers_index(symbols)
    return graph


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _callers_index(symbols: dict) -> dict:
    """callname (last segment + full dotted) -> {caller qualname}. The
    name-resolution heuristic: every symbol whose calls list mentions a name
    ending in ``short`` counts as a caller."""
    idx: dict = {}
    for sym, meta in symbols.items():
        for c in meta.get("calls", ()):
            idx.setdefault(_short(c), set()).add(sym)
            if "." in c:
                idx.setdefault(c, set()).add(sym)
    return idx


def _resolve(symbols: dict, target: str) -> list[str]:
    """Exact qualname, then suffix (``Membrane`` -> ``runtime.Membrane``),
    then dotted-tail match."""
    if target in symbols:
        return [target]
    hits = [s for s in symbols if _short(s) == target]
    if hits:
        return hits
    return [s for s in symbols if s.endswith("." + target)]


def callers_of(graph: dict, target: str, depth: int = _IMPACT_DEPTH) -> dict:
    """Reverse-BFS from target over the callers index:
    {levels: [[{caller, file, line}...]], matches: [resolved qualnames]}.
    target may be a short name or a qualname."""
    symbols = graph["symbols"]
    idx = graph.get("callers_index") or _callers_index(symbols)
    matches = _resolve(symbols, target)
    levels: list[list[dict]] = []
    seen = set(matches)
    frontier = list(matches)
    for _ in range(max(1, depth)):
        nxt: set = set()
        lvl: list[dict] = []
        for s in frontier:
            for c in sorted(idx.get(_short(s), set())):
                if c in seen:
                    continue
                meta = symbols.get(c, {})
                lvl.append({"caller": c, "file": meta.get("file", "?"),
                            "line": meta.get("line", 0)})
                seen.add(c)
                nxt.add(c)
        if not lvl:
            break
        levels.append(sorted(lvl, key=lambda d: (d["file"], d["line"])))
        frontier = list(nxt)
    return {"levels": levels, "matches": matches}


def callees_of(graph: dict, target: str) -> dict:
    """What one symbol calls, resolved to known symbols where possible."""
    symbols = graph["symbols"]
    matches = _resolve(symbols, target)
    out: list[dict] = []
    for m in matches:
        for c in symbols.get(m, {}).get("calls", ()):
            res = _resolve(symbols, _short(c))
            out.append({"call": c,
                        "resolved": res[0] if len(res) == 1 else "",
                        "file": symbols.get(m, {}).get("file", "?"),
                        "line": symbols.get(m, {}).get("line", 0)})
    return {"matches": matches, "callees": out}


def impact_of_file(graph: dict, rel: str) -> dict:
    """Blast radius of touching a FILE: its symbols + every caller of them."""
    info = graph["files"].get(rel) or graph["files"].get(rel.rstrip("/") or rel)
    if info is None:
        alt = [f for f in graph["files"] if f.endswith(rel)]
        if not alt:
            return {"symbols": [], "levels": []}
        rel = alt[0]
        info = graph["files"][rel]
    levels: list[list[dict]] = []
    seen: set = set()
    for sym in info["symbols"]:
        r = callers_of(graph, sym, depth=2)
        for lvl in r["levels"]:
            for e in lvl:
                if e["caller"] in seen:
                    continue
                seen.add(e["caller"])
                levels.append([e])
    return {"symbols": info["symbols"], "levels": levels, "file": rel}


def stats(graph: dict) -> dict:
    files = graph["files"]
    symbols = graph["symbols"]
    edges = sum(len(m.get("calls", ())) for m in symbols.values())
    top = sorted(files.items(), key=lambda kv: -len(kv[1]["symbols"]))[:10]
    return {"root": graph["root"], "n_files": len(files),
            "n_symbols": len(symbols), "n_edges": edges,
            "top": [(f, len(v["symbols"])) for f, v in top]}


def query(graph: dict, spec: str) -> str:
    """The shared text form for CLI + intercept: stats | impact X |
    callers X | callees X | impact <file.py>."""
    parts = spec.strip().split(maxsplit=1)
    verb = (parts[0].lower() if parts else "stats")
    arg = parts[1].strip() if len(parts) > 1 else ""
    if verb in ("stats", ""):
        s = stats(graph)
        lines = [f"CodeGraph: {s['n_symbols']} symbols in {s['n_files']} "
                 f"files under {s['root']} ({s['n_edges']} recorded call "
                 f"edges, name-resolved)."]
        if s["top"]:
            lines.append("top files by symbols:")
            lines += [f"  {f}  ({n})" for f, n in s["top"][:6]]
        return "\n".join(lines)
    if verb in ("impact", "callers"):
        if arg.endswith(".py"):
            r = impact_of_file(graph, arg)
            if not r["symbols"]:
                return (f"CodeGraph impact: no file matches {arg!r}. "
                        "'membrane: codegraph stats' lists the graph.")
            lines = [f"impact of {r['file']}: {len(r['symbols'])} symbols "
                     f"(direct callers of any of them:):"]
            seen = set()
            for lvl in r["levels"]:
                for e in lvl:
                    if e["caller"] in seen:
                        continue
                    seen.add(e["caller"])
                    lines.append(f"  <- {e['caller']}  "
                                 f"({e['file']}:{e['line']})")
            if not seen:
                lines.append("  (nothing calls into this file)")
            return "\n".join(lines)
        r = callers_of(graph, arg)
        if not r["matches"]:
            return (f"CodeGraph: no symbol matches {arg!r}. "
                    "'membrane: codegraph stats' shows coverage.")
        lines = [f"impact of {arg} (resolved: "
                 f"{', '.join(r['matches'][:4])}):"]
        for i, lvl in enumerate(r["levels"], 1):
            lines.append(f"  depth {i}:")
            lines += [f"    <- {e['caller']}  ({e['file']}:{e['line']})"
                      for e in lvl[:12]]
        if not r["levels"]:
            lines.append("  (no callers found -- a root or dynamically "
                         "called)")
        return "\n".join(lines)
    if verb == "callees":
        r = callees_of(graph, arg)
        if not r["matches"]:
            return f"CodeGraph: no symbol matches {arg!r}."
        lines = [f"callees of {', '.join(r['matches'][:3])}:"]
        for c in r["callees"]:
            res = f"  -> {c['resolved']}" if c["resolved"] else ""
            lines.append(f"  {c['call']}{res}")
        if not r["callees"]:
            lines.append("  (nothing)")
        return "\n".join(lines)
    return ("CodeGraph verbs: stats | impact <symbol|file.py> | "
            "callers <sym> | callees <sym>")


def summary(graph: dict) -> str:
    s = stats(graph)
    return (f"codegraph: {s['n_symbols']} symbols / {s['n_files']} files "
            f"({s['n_edges']} call edges)")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="membrane codegraph",
        description="stdlib-AST call graph over this repo (no model call)")
    ap.add_argument("spec", nargs="*", default=[],
                    help="stats | impact <symbol|file.py> | callers <sym> "
                         "| callees <sym>")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    g = build_graph(args.root)
    print(query(g, " ".join(args.spec) or "stats"))


if __name__ == "__main__":
    main()