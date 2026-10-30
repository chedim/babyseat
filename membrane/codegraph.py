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
import os
import sys
from pathlib import Path

# directories never scanned (runtime/state/vendor noise, not source)
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "dist",
              ".membrane", ".opencode", "__pycache__", "worlds", ".claude",
              ".idea", ".mypy_cache", ".pytest_cache"}

_MAX_FILES = 4000
_IMPACT_DEPTH = 4


_MAX_BYTES = 1_000_000      # a file bigger than this is not scanned (vendored bundles); approved by the operator 2026-10-30


def _src_files(root: Path) -> list[Path]:
    """Every source file a grammar reads (srctree.EXT: py, sh, js), sorted, skip dirs
    pruned; a file over _MAX_BYTES is left out."""
    from .srctree import lang_of
    if root.is_file():
        return [root] if lang_of(root) else []
    found: list[Path] = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        for f in files:
            p = Path(d) / f
            if lang_of(p) is None:
                continue
            try:
                if p.stat().st_size > _MAX_BYTES:
                    continue
            except OSError:
                continue
            found.append(p)
    return sorted(found)[:_MAX_FILES]


# ---- the scan: tree-sitter trees (#715 C) ----------------------------------------------------------
# Python keeps exactly what the old stdlib-ast scan gave (tests/test_codegraph_ts_715.py holds it as
# the oracle): a def's line is its `def` line (not its decorator's), a def nested under any def or
# class is a "method", its calls are every call in it (decorators, defaults and nested defs too),
# `sig` its argument spec (positional-only args left out), `doc` its cleaned docstring. Shell and JS
# get symbols and calls the same way (no sig, no doc).

_PY_DEFS = {"function_definition": "function", "class_definition": "class"}
_SH_DEFS = {"function_definition": "function"}
_JS_DEFS = {"class_declaration": "class", "function_declaration": "function",
            "generator_function_declaration": "function", "method_definition": "method"}


def _txt(src: bytes, n) -> str:
    return src[n.start_byte:n.end_byte].decode("utf-8", errors="replace")


def _unparen(n):
    while n is not None and n.type == "parenthesized_expression" and n.named_child_count == 1:
        n = n.named_children[0]
    return n


def _walk(n):
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(x.children)


def _dotted(src: bytes, f, attr: str, obj: str, member: str, name_types) -> str | None:
    """A call's callee as a dotted name: `f`, `a.b.c`; an attribute chain on something
    that is not a name keeps its attribute part (`x().b` -> `b`)."""
    f = _unparen(f)
    if f is None:
        return None
    if f.type in name_types:
        return _txt(src, f)
    parts: list[str] = []
    while f is not None and f.type == member:
        a = f.child_by_field_name(attr)
        if a is None:
            break
        parts.append(_txt(src, a))
        f = _unparen(f.child_by_field_name(obj))
    if f is not None and f.type in name_types:
        parts.append(_txt(src, f))
    return ".".join(reversed(parts)) if parts else None


def _calls(src: bytes, node, lang: str) -> list[str]:
    names: list[str] = []
    for x in _walk(node):
        if lang == "py" and x.type == "call":
            c = _dotted(src, x.child_by_field_name("function"), "attribute", "object", "attribute",
                        ("identifier",))
        elif lang == "js" and x.type == "call_expression":
            c = _dotted(src, x.child_by_field_name("function"), "property", "object",
                        "member_expression", ("identifier",))
        elif lang == "sh" and x.type == "command":
            n = x.child_by_field_name("name")
            c = _txt(src, n).strip() if n is not None else None
        else:
            continue
        if c:
            names.append(c)
    return sorted(set(names))


def _py_default(src: bytes, n) -> str:
    """A default as the old scan rendered it (ast.unparse of the expression)."""
    import ast as _ast
    t = _txt(src, n)
    try:
        return _ast.unparse(_ast.parse("(\n" + t + "\n)", mode="eval").body)   # a default may span lines
    except Exception:
        return t


def _py_sig(src: bytes, fn) -> dict:
    pos: list = []
    kw: list = []
    vararg = kwarg = None
    in_kw = False
    params = fn.child_by_field_name("parameters")
    for c in (params.children if params is not None else []):
        t = c.type
        if t == "typed_parameter":                 # `x: int`, `*a: int`, `**k: int`
            inner = c.named_children[0] if c.named_children else None
            t, c = (inner.type, inner) if inner is not None else (t, c)
        if t in ("positional_separator", "/"):
            pos = []                               # positional-only args are not in the spec
        elif t in ("keyword_separator", "*"):
            in_kw = True
        elif t == "identifier":
            (kw if in_kw else pos).append((_txt(src, c), None))
        elif t in ("default_parameter", "typed_default_parameter"):
            (kw if in_kw else pos).append((_txt(src, c.child_by_field_name("name")),
                                           _py_default(src, c.child_by_field_name("value"))))
        elif t == "list_splat_pattern":
            vararg = _txt(src, c.named_children[0]) if c.named_children else None
            in_kw = True
        elif t == "dictionary_splat_pattern":
            kwarg = _txt(src, c.named_children[0]) if c.named_children else None
    return {"args": pos, "vararg": vararg, "kwonly": kw, "kwarg": kwarg}


def _py_doc(src: bytes, d) -> str:
    import ast as _ast
    import inspect
    body = d.child_by_field_name("body")
    first = next((c for c in (body.named_children if body is not None else []) if c.type != "comment"), None)
    if first is None or first.type != "expression_statement" or first.named_child_count != 1:
        return ""
    s = _unparen(first.named_children[0])
    if s is None or s.type not in ("string", "concatenated_string") or \
            any(x.type == "interpolation" for x in _walk(s)):
        return ""
    try:
        v = _ast.literal_eval(_txt(src, s))
    except Exception:
        return ""
    return inspect.cleandoc(v)[:600] if isinstance(v, str) else ""


# ---- the fallback: the pre-#715 stdlib-ast scan, verbatim (Python only, no grammar needed) -----
def _ast_calls(fn) -> list[str]:
    import ast
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


def _ast_scan_source(src: bytes, rel: str) -> dict | None:
    """scan_source for Python where tree-sitter is not installed; None when it does not parse."""
    import ast
    try:
        tree = ast.parse(src.decode("utf-8", errors="replace"))
    except SyntaxError:
        return None
    syms: dict = {}

    def _dflt(node) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return "?"

    def _args(ch) -> dict:
        a = ch.args
        ndef = len(a.defaults)          # defaults pack onto the TRAILING positional args
        pos = []
        for i, x in enumerate(a.args):
            d = None
            if ndef and i >= len(a.args) - ndef:
                d = _dflt(a.defaults[i - (len(a.args) - ndef)])
            pos.append((x.arg, d))
        kw = [(x.arg, (_dflt(d) if d is not None else None)) for x, d in zip(a.kwonlyargs, a.kw_defaults)]
        return {"args": pos, "vararg": getattr(a.vararg, "arg", None), "kwonly": kw,
                "kwarg": getattr(a.kwarg, "arg", None)}

    def visit(node, prefix: str) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + ch.name
                syms[q] = {"file": rel, "line": ch.lineno, "kind": "method" if prefix else "function",
                           "calls": _ast_calls(ch), "sig": _args(ch),
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


def scan_source(src: bytes, rel: str, lang: str | None = None) -> dict | None:
    """Qualname -> {file, line, kind, calls[, sig, doc]} for one file; None when it does
    not parse (or no grammar reads it)."""
    from . import lang as LG
    from .srctree import lang_of
    lang = lang or lang_of(rel)
    if not lang:
        return None
    try:
        import tree_sitter as ts
        grammar = LG._language(lang)
    except (ImportError, LG.NoGrammar):
        # a seat with no tree-sitter wheel (Termux, HA on a Pi): Python keeps the stdlib-ast
        # scan it always had; sh and js are skipped there
        return _ast_scan_source(src, rel) if lang == "py" else None
    root = ts.Parser(grammar).parse(src).root_node
    if root.has_error:
        return None
    defs = {"py": _PY_DEFS, "sh": _SH_DEFS, "js": _JS_DEFS}[lang]
    syms: dict = {}

    def visit(node, prefix: str, within: str) -> None:
        for ch in node.named_children:
            d = ch.child_by_field_name("definition") if ch.type == "decorated_definition" else ch
            name = d.child_by_field_name("name") if d is not None and d.type in defs else None
            if name is None:
                visit(ch, prefix, within)
                continue
            q = prefix + _txt(src, name)
            kind = defs[d.type]
            if lang == "py" and kind == "function" and prefix:
                kind = "method"                    # the old scan: any nested def
            elif lang == "js" and kind == "function" and within == "class":
                kind = "method"
            info = {"file": rel, "line": d.start_point[0] + 1, "kind": kind,
                    "calls": [] if kind == "class" else _calls(src, ch, lang)}
            if lang == "py":
                if kind != "class":
                    info["sig"] = _py_sig(src, d)
                info["doc"] = _py_doc(src, d)
            syms[q] = info
            visit(d, q + ".", kind)
    visit(root, "", "module")
    return syms


def _scan_file(path: Path, rel: str):
    """(symbols, mtime) for one file; None when it cannot be read or does not parse."""
    try:
        src = path.read_bytes()
        mt = path.stat().st_mtime
    except OSError:
        return None
    if b"\x00" in src[:1024]:
        return None
    syms = scan_source(src, rel)
    return None if syms is None else (syms, mt)



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
    for path in _src_files(rootp):
        rel = path.relative_to(rootp).as_posix() if path != rootp else path.name
        got = _scan_file(path, rel)
        if got is None:
            continue
        syms, mt = got
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
    from .srctree import EXT
    ext = Path(rel).suffix
    mod = rel[:-len(ext)] if ext and ext.lower() in EXT else rel
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
        got = _scan_file(p, rel)
        if got is None:
            continue
        syms, mt = got
        for s in files.get(rel, {}).get("symbols", ()):
            if owner.get(s) == rel:
                symbols.pop(s, None)
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
    for p in _src_files(rootp):
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