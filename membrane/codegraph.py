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
    """Qualname -> {file, line, kind, calls, sig} for one module. Nested
    defs get dotted qualnames (``outer.inner``, ``Class.method``). The
    ``sig`` entry (functions/methods only): the def site's argument
    spec -- {args: [(name, default_or_None)...], vararg, kwonly:
    [(name, default)...], kwarg, returns} -- the SIGNATURE VERB's data
    (the call-edge graph answers who-calls-X; this answers what-X-TAKES)."""
    syms: dict = {}

    def _dflt(node) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return "?"

    def _args(ch) -> dict:
        a = ch.args
        # ast packs `defaults` onto the TRAILING positional args: zip
        # must offset by n_args - n_defaults (else 'messages, n=3'
        # pairs as 'messages=3')
        ndef = len(a.defaults)
        pos = []
        for i, x in enumerate(a.args):
            d = None
            if ndef and i >= len(a.args) - ndef:
                d = _dflt(a.defaults[i - (len(a.args) - ndef)])
            pos.append((x.arg, d))
        kw = [(x.arg, (_dflt(d) if d is not None else None))
              for x, d in zip(a.kwonlyargs, a.kw_defaults)]
        return {"args": pos, "vararg": getattr(a.vararg, "arg", None),
                "kwonly": kw,
                "kwarg": getattr(a.kwarg, "arg", None)}

    def visit(node, prefix: str) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + ch.name
                kind = "method" if prefix else "function"
                syms[q] = {"file": rel, "line": ch.lineno, "kind": kind,
                           "calls": _collect_calls(ch), "sig": _args(ch),
                           "doc": (ast.get_docstring(ch) or "")[:600]}
                visit(ch, q + ".")
            elif isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                syms[q] = {"file": rel, "line": ch.lineno, "kind": "class",
                           "calls": [],
                           "doc": (ast.get_docstring(ch) or "")[:600]}
                visit(ch, q + ".")
            else:
                visit(ch, prefix)

    visit(tree, "")
    return syms


def _sig_parts(info: dict, sig: dict) -> list[str]:
    """The def site's parts: positional args (their DEFAULTS correctly
    paired -- ast packs `defaults` onto the TRAILING positional args,
    so the zip offsets by n_args - n_defaults), then *vararg, then
    keyword-only args with their per-arg defaults, then **kwarg. The
    receiver (self/cls on a method) is not a signature fact."""
    parts: list[str] = []
    args = sig.get("args") or []
    kwonly = sig.get("kwonly") or []
    ndef = sum(1 for _n, d in kwonly if d is not None) \
        + sum(1 for _n, d in args if d is not None)
    # positional defaults: count only the positional ones
    ndef_pos = sum(1 for _n, d in args if d is not None)
    for i, (n, _d) in enumerate(args):
        if info.get("kind") == "method" and i == 0 and n in (
                "self", "cls"):
            continue
        d = None
        if ndef_pos and i >= len(args) - ndef_pos:
            d = args[i][1]
        parts.append(n if d is None else f"{n}={d}")
    if kwonly and not sig.get("vararg"):
        parts.append("*")
    for n, d in kwonly:
        parts.append(f"{n}={d}" if d is not None else f"*{n}")
    if sig.get("vararg"):
        parts.append(f"*{sig['vararg']}")
    if sig.get("kwarg"):
        parts.append(f"**{sig['kwarg']}")
    return parts


def build_graph(root: str | Path = ".") -> dict:
    """Scan every .py under root -> {root, files, symbols}. Files that fail
    to parse are skipped silently (a half-broken repo must still graph)."""
    rootp = Path(root).resolve()
    files: dict = {}
    symbols: dict = {}
    defs: dict = {}
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
        defs.update(_qualify(syms, rel))
    graph = {"root": str(rootp), "files": files, "symbols": symbols,
             "defs": defs}
    graph["callers_index"] = _callers_index(symbols)
    graph["short_index"] = _short_index(defs)
    return graph


def module_of(rel: str) -> str:
    """'membrane/opencode.py' -> 'membrane.opencode' (a package's
    __init__.py is the package itself)."""
    mod = rel[:-3] if rel.endswith(".py") else rel
    mod = mod.replace("/", ".")
    return mod[:-len(".__init__")] if mod.endswith(".__init__") else mod


def _qualify(syms: dict, rel: str) -> dict:
    """The COLLISION-FREE view: '<module>.<qualname>' -> info (+ qual,
    mod). `symbols` keys on bare qualnames and a later file overwrites an
    earlier one (58 collisions in this repo -- main, Class.__init__ ...);
    `defs` keeps every definition. Additive: nothing that reads
    `symbols` changes."""
    mod = module_of(rel)
    return {f"{mod}.{q}": {**info, "qual": q, "mod": mod}
            for q, info in syms.items()}


def _short_index(defs: dict) -> dict:
    """short name (last segment) -> [full defs names] -- how a bare name
    in prose resolves (unique -> that def; several -> ambiguous)."""
    idx: dict = {}
    for full in defs:
        idx.setdefault(_short(full), []).append(full)
    return idx


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


def rebuild_paths(graph: dict, rels) -> dict:
    """Incremental update: re-scan ONLY the named repo-relative files (the
    incremental bridge). Edits re-parse and swap that file's symbols; missing
    files drop out (deletes/renames). Then the callers index is rebuilt.
    Symbol-owner semantics match build_graph (a duplicated qualname: last
    file scanned wins) -- a symbol another file also claims is never
    dropped. Half-written files (parse error mid-save) keep their old
    symbols; the next mtime sweep re-picks them."""
    rootp = Path(graph["root"])
    symbols = graph["symbols"]
    files = graph["files"]
    defs = graph.setdefault("defs", {})
    owner: dict = {}
    for rel2, meta2 in files.items():
        for s in meta2["symbols"]:
            owner[s] = rel2
    touched: list[str] = []
    dropped: list[str] = []
    for rel in rels:
        rel = Path(rel).as_posix()
        p = rootp / rel
        if not p.is_file():
            for s in files.get(rel, {}).get("symbols", ()):
                if owner.get(s) == rel:
                    symbols.pop(s, None)
            for full in [d for d in defs if defs[d].get("file") == rel]:
                defs.pop(full, None)
            files.pop(rel, None)
            dropped.append(rel)
            touched.append(rel)
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            if "\x00" in src[:1024]:
                continue
            tree = ast.parse(src)
            mt = p.stat().st_mtime
        except (OSError, SyntaxError):
            continue
        for s in files.get(rel, {}).get("symbols", ()):
            if owner.get(s) == rel:
                symbols.pop(s, None)
        syms = _scan_tree(tree, rel)
        files[rel] = {"mtime": mt, "symbols": sorted(syms)}
        symbols.update(syms)
        for full in [d for d in defs if defs[d].get("file") == rel]:
            defs.pop(full, None)
        defs.update(_qualify(syms, rel))
        touched.append(rel)
    graph["callers_index"] = _callers_index(symbols)
    graph["short_index"] = _short_index(defs)
    return {"touched": touched, "dropped": dropped}


def stale_paths(graph: dict) -> list[str]:
    """Repo-relative files whose stored mtime no longer matches disk (edited
    outside the tool lane -- bash, git, an external editor) plus deleted
    files and files added since the last scan. A full walk, so callers must
    throttle it (the opencode bridge sweeps at most once per interval)."""
    rootp = Path(graph["root"])
    files = graph["files"]
    stale: list[str] = []
    seen: set[str] = set()
    for p in _py_files(rootp):
        rel = p.relative_to(rootp).as_posix()
        seen.add(rel)
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        info = files.get(rel)
        if info is None or info.get("mtime") != mt:
            stale.append(rel)
    stale.extend(rel for rel in files if rel not in seen)
    return stale


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
    if verb == "signature":
        # THE SIGNATURE VERB: what-X-TAKES, from the def site (a
        # caller's override is NOT the signature -- the AST reads the
        # def, so defaults are the source of truth, file:line for
        # navigation). Name-resolved like every verb here.
        hits = _resolve(graph.get("symbols") or {}, arg)
        if not hits:
            return (f"CodeGraph: no symbol matches {arg!r}. "
                    "'membrane: codegraph stats' shows coverage.")
        lines = [f"signature of {', '.join(hits[:3])}:"]
        for q in hits[:3]:
            info = graph["symbols"][q]
            sig = info.get("sig")
            if not sig:
                lines.append(f"  {q}  ({info['file']}:{info['line']})"
                             "  [class -- methods below]")
                continue
            parts = _sig_parts(info, sig)
            lines.append(f"  {q}({', '.join(parts)})")
            lines.append(f"    ({info['kind']}, {info['file']}:"
                         f"{info['line']}; entity id: symbols:{q.lower()})")
        return "\n".join(lines)
    return ("CodeGraph verbs: stats | impact <symbol|file.py> | "
            "callers <sym> | callees <sym> | signature <sym>")


def summary(graph: dict) -> str:
    s = stats(graph)
    return (f"codegraph: {s['n_symbols']} symbols / {s['n_files']} files "
            f"({s['n_edges']} call edges)")


def refine_entities(tags: dict, graph: dict) -> dict:
    """Validate/refine entity tags against the AST graph (the entities.py
    capture-time harvest, grounded in the real symbol table):

    - ``symbols``: drop anything the graph cannot resolve (regex harvests
      prose noise like ``http.server`` in a URL string); promote resolvable
      bare names to their full qualname (``route`` -> ``router.Registry.route``)
      -- the qualname is the precise retrieval key.
    - ``files``: keep only files the graph knows (a mentioned path that is
      not in the repo is a fiction, e.g. a path in a copied log line).
    Everything else passes through untouched; any error degrades to the
    unrefined tags (a capture must never fail because refinement did)."""
    try:
        symbols: dict = graph.get("symbols") or {}
        files: dict = graph.get("files") or {}
        out: dict = {}
        for kind, values in (tags or {}).items():
            if kind == "symbols":
                good = []
                for s in values or []:
                    if s in symbols:
                        good.append(s)
                        continue
                    hits = [q for q in symbols if _short(q) == s]
                    if not hits:
                        hits = [q for q in symbols
                                if q.endswith("." + s)]
                    if len(hits) == 1:
                        good.append(hits[0])   # promote to the qualname
                    elif hits:
                        good.append(s)         # ambiguous: keep the mention
                    # no hits at all -> drop (prose noise)
                if good:
                    out[kind] = good[:8]
            elif kind == "files":
                repo_tails: dict = {}
                for k in files:
                    repo_tails.setdefault(k.rsplit("/", 1)[-1], k)
                good = []
                for f in values or []:
                    if f in files:
                        good.append(f)                       # exact repo path
                    elif f in repo_tails:
                        good.append(repo_tails[f])           # tail match
                    # else: not a repo file -> drop (foreign log-line path)
                if good:
                    out[kind] = sorted(set(good))[:8]
            else:
                out[kind] = values
        return out
    except Exception:
        return tags or {}


def repo_tails(files: dict, name: str) -> dict:
    """files whose tail == name -> {name: repo_rel_path}."""
    return {k.rsplit("/", 1)[-1]: k for k in files
            if k.rsplit("/", 1)[-1] == name}


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