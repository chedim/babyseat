"""LIVE CODE TREES (#715 part A, the operator 2026-10-29: "let's make our code
trees alive"): a file's source as entities, one per SYMBOL -- the module, each
class, each function or method -- read-only for now. Opt-in: only a file entity
that extends `fs:code` gets one (fstree.py, the operator's 2026-10-30 model).

IDENTITY IS STABLE: `membrane::src:<file>::<qualname>` (the module itself is
`membrane::src:<file>`; <file> is the file entity's key: repo-relative under
the seat's root, absolute elsewhere), across edits; the content hash and the
byte / line span are properties. A nested def gets a dotted qualname
(`Pump.run.inner`); a decorated definition's span starts at its first
decorator. `within` names the enclosing symbol (the module: its file entity) --
containment, never `parent` (the legacy ISA key).

THE GRAMMAR IS PICKED BY EXTENSION from lang.GRAMMARS (py, sh, js).

FILES ARE THE SOURCE OF TRUTH: the file's mtime, checked on the autonode's scan,
re-parses a file changed outside the field (an editor, git, a checkout). A
symbol gone from its file is RETIRED (status 'retired', mark-don't-delete); a
file that brings it back revives the same entity. A half-written file (a parse
error) keeps its old symbols until it parses. Reading a symbol reads its span
from disk (`source`).

Part B (the write path: entity_edit source=...) and part C (codegraph's symbols
and call edges on these trees) build on this."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity

NS = MEMBRANE_NS + "src:"
LIVE, RETIRED = "live", "retired"
EXT = {".py": "py", ".sh": "sh", ".bash": "sh", ".js": "js", ".mjs": "js", ".cjs": "js"}
_DEFS = {   # per grammar: definition node -> its kind
    "py": {"class_definition": "class", "function_definition": "function"},
    "sh": {"function_definition": "function"},
    "js": {"class_declaration": "class", "function_declaration": "function",
           "generator_function_declaration": "function", "method_definition": "method"},
}


def eid_of(key: str, qual: str = "") -> str:
    return NS + key + (f"::{qual}" if qual else "")


def _hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:16]


def lang_of(path) -> str | None:
    """The grammar a file's extension names (one lang.GRAMMARS has), else None."""
    from . import lang as LG
    g = EXT.get(Path(str(path)).suffix.lower())
    return g if g in LG.GRAMMARS else None


def symbols(src: bytes, lang: str = "py") -> list[dict] | None:
    """The module and its symbols, outermost first; None when it does not parse."""
    from . import lang as LG
    import tree_sitter as ts
    defs = _DEFS.get(lang) or {}
    root = ts.Parser(LG._language(lang)).parse(src).root_node
    if root.has_error:
        return None
    out = [{"qual": "", "kind": "module", "span": [0, len(src)],
            "lines": [1, src.count(b"\n") + 1], "hash": _hash(src), "within": None}]

    def walk(n, prefix: str, within: str, within_kind: str) -> None:
        for c in n.named_children:
            node = c.child_by_field_name("definition") if c.type == "decorated_definition" else c
            if node is not None and node.type in defs and node.child_by_field_name("name") is not None:
                q = prefix + node.child_by_field_name("name").text.decode()
                kind = defs[node.type]
                if kind == "function" and within_kind == "class":
                    kind = "method"
                a, b = c.start_byte, c.end_byte          # a decorator belongs to its def
                out.append({"qual": q, "kind": kind, "span": [a, b],
                            "lines": [c.start_point[0] + 1, c.end_point[0] + 1],
                            "hash": _hash(src[a:b]), "within": within})
                walk(node, q + ".", q, kind)
            else:
                walk(c, prefix, within, within_kind)
    walk(root, "", "", "module")
    return out


def attach(srv) -> dict:
    """The seat's symbol state: per file key its path, mtime and symbols --
    rebuilt from the store's live entities, so a restart re-reads nothing new."""
    est = srv.entity_store
    files: dict = {}
    for eid, d in list((est.docs or {}).items()):
        if not eid.startswith(NS):
            continue
        p = d.properties or {}
        if p.get("status") == RETIRED:
            continue
        f = files.setdefault(str(p.get("path") or ""), {"mtime": None, "symbols": [], "file": p.get("file")})
        if p.get("kind") == "module":
            f["mtime"] = p.get("mtime")
        else:
            f["symbols"].append(str(p.get("qualname") or ""))
    g = {"files": files}
    srv._srctree = g
    return g


def _graph(srv) -> dict:
    g = getattr(srv, "_srctree", None)
    return g if g is not None else attach(srv)


def _retire(est, eid: str) -> bool:
    d = (est.docs or {}).get(eid)
    if d is None or (d.properties or {}).get("status") == RETIRED:
        return False
    d.properties = {**(d.properties or {}), "status": RETIRED, "retired_ts": time.time()}
    est.upsert(d, embedder=None)
    return True


def _put(est, key: str, path: Path, s: dict, mtime: float, file_eid: str) -> bool:
    """Upsert one symbol's entity; False when nothing about it changed."""
    eid = eid_of(key, s["qual"])
    want = {"name": f"{key}::{s['qual']}" if s["qual"] else key, "path": key, "file": str(path),
            "qualname": s["qual"], "kind": s["kind"], "span": s["span"], "lines": s["lines"],
            "hash": s["hash"], "status": LIVE,
            "within": eid_of(key, s["within"]) if s["within"] is not None else file_eid}
    if s["kind"] == "module":
        want["mtime"] = mtime
    d = (est.docs or {}).get(eid)
    if d is not None:
        p = d.properties or {}
        if all(p.get(k) == v for k, v in want.items()):
            return False
        d.properties = {**p, **want}
        d.properties.pop("retired_ts", None)
        est.upsert(d, embedder=None)
        return True
    est.upsert(QuantumEntity(eid=eid, cls=CLASS_COMPONENT,
                             properties={**want, "silent_birth": True, "born_ts": time.time()}),
               embedder=None)
    return True


def sync_file(srv, key: str, path, within: str) -> dict:
    """Re-read one file into its symbols' entities (`within`: its file entity)."""
    g = _graph(srv)
    est = srv.entity_store
    files = g["files"]
    path = Path(path)
    old = files.get(key) or {"symbols": []}
    out = {"upserted": 0, "retired": 0}
    if not path.is_file():
        if key in files:
            for q in old["symbols"] + [""]:
                out["retired"] += _retire(est, eid_of(key, q))
            files.pop(key, None)
        return out
    lang = lang_of(path)
    try:
        src, mtime = path.read_bytes(), path.stat().st_mtime
    except OSError:
        return out
    syms = symbols(src, lang) if lang and b"\x00" not in src[:1024] else None
    if syms is None:
        files[key] = {**old, "mtime": mtime, "file": str(path)}   # half-written or no grammar: the old symbols stand
        return out
    for s in syms:
        out["upserted"] += _put(est, key, path, s, mtime, within)
    now = {s["qual"] for s in syms if s["qual"]}
    for q in old["symbols"]:
        if q not in now:
            out["retired"] += _retire(est, eid_of(key, q))
    files[key] = {"mtime": mtime, "symbols": sorted(now), "file": str(path)}
    return out


def sync_if_stale(srv, key: str, path, within: str) -> dict:
    """sync_file when the file's mtime moved (or it is gone) since the last read."""
    f = _graph(srv)["files"].get(key)
    path = Path(path)
    try:
        mt = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mt = None
    if f is not None and mt is not None and f.get("mtime") == mt:
        return {"upserted": 0, "retired": 0}
    if f is None and mt is None:
        return {"upserted": 0, "retired": 0}
    return sync_file(srv, key, path, within)


def source(srv, eid: str) -> str | None:
    """A symbol's code, read from disk (a file changed since the last read is re-read first)."""
    est = srv.entity_store
    d = (est.docs or {}).get(eid)
    if d is None:
        return None
    p = d.properties or {}
    key, path = str(p.get("path") or ""), Path(str(p.get("file") or ""))
    try:
        b = path.read_bytes()
        if (_graph(srv)["files"].get(key) or {}).get("mtime") != path.stat().st_mtime:
            mod = (est.docs.get(eid_of(key)) or d).properties or {}
            sync_file(srv, key, path, str(mod.get("within") or ""))
            p = (est.docs[eid].properties or {})
    except OSError:
        return None
    if p.get("status") == RETIRED:
        return None
    a, z = p.get("span") or [0, 0]
    return b[a:z].decode("utf-8", errors="replace")


# ---- #715 part B: the write path -----------------------------------------------------------------
# entity_edit(<symbol>, properties={source: ...}) writes the file: exactly that symbol's span is
# replaced (the rest byte-identical), the new file must parse or nothing is written, the symbols
# are re-read from disk (files stay the source of truth), and a result entity records the edit.

def _within_of(est, key: str) -> str:
    mod = (est.docs or {}).get(eid_of(key))
    return str(((mod.properties or {}) if mod is not None else {}).get("within") or "")


def edit_source(srv, eid: str, new: str) -> dict:
    """Replace one symbol's source in its file. -> {ok, symbol, result, span} or {ok: False, error}."""
    est = srv.entity_store
    d = (est.docs or {}).get(eid)
    if d is None or not str(eid).startswith(NS):
        return {"ok": False, "error": f"no code symbol {eid!r}"}
    p = d.properties or {}
    key, path = str(p.get("path") or ""), Path(str(p.get("file") or ""))
    within = _within_of(est, key)
    sync_if_stale(srv, key, path, within)              # an outside edit first: the span is the disk's
    p = est.docs[eid].properties or {}
    if p.get("status") == RETIRED:
        return {"ok": False, "error": f"{eid} is retired (gone from its file): edit the symbol that replaced it"}
    lang = lang_of(path)
    if not lang:
        return {"ok": False, "error": f"no grammar for {path.name}"}
    try:
        b, st = path.read_bytes(), path.stat()
    except OSError as e:
        return {"ok": False, "error": f"cannot read {path}: {e}"}
    a, z = p.get("span") or [0, 0]
    if _hash(b[a:z]) != p.get("hash"):                 # changed within the same mtime tick: re-read
        sync_file(srv, key, path, within)
        p = est.docs[eid].properties or {}
        a, z = p.get("span") or [0, 0]
        if p.get("status") == RETIRED or _hash(b[a:z]) != p.get("hash"):
            return {"ok": False, "error": f"{eid} moved under the edit; read it again"}
    nb = str(new).encode()
    out = b[:a] + nb + b[z:]
    if symbols(out, lang) is None:
        return {"ok": False, "error": f"the new source does not parse as {lang}; {path.name} is untouched"}
    tmp = path.with_name(f".{path.name}.membrane-edit")
    tmp.write_bytes(out)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, path)
    old_hash = p.get("hash")
    sync_file(srv, key, path, within)
    p2 = est.docs[eid].properties or {}
    n = int(p.get("edits") or 0) + 1
    res = f"{MEMBRANE_NS}result:src:{_hash(eid.encode())}:{n}"
    est.upsert(QuantumEntity(eid=res, cls=CLASS_COMPONENT, properties={
        "name": f"edit {n} of {p.get('name') or eid}"[:80], "symbol": eid, "file": str(path),
        "span": [a, z], "new_span": [a, a + len(nb)], "old_hash": old_hash,
        "new_hash": p2.get("hash") if p2.get("status") == LIVE else _hash(nb),
        "mtime": path.stat().st_mtime, "ts": time.time(), "silent_birth": True}), embedder=None)
    d = est.docs[eid]
    results = list((d.properties or {}).get("edit_results") or []) + [res]
    from . import lang as LG
    keep = LG._keep(srv)                               # code_run's: options.code_runs_keep
    for old in results[:max(0, len(results) - keep)]:
        est.remove(old)
    d.properties = {**(d.properties or {}), "edits": n, "last_edit": res, "edit_results": results[-keep:]}
    est.upsert(d, embedder=None)
    return {"ok": True, "symbol": eid, "result": res, "span": [a, a + len(nb)], "file": str(path)}


def source_write(srv, eid: str, props: dict, owner: bool) -> dict | None:
    """The seat's edit route (lanes._entity_edit): a `source` write on a code symbol is code, so
    the owner's only. None: not a source write (the edit goes on as usual). Else the source edit,
    with `properties` the rest of the patch (the ordinary edit takes it)."""
    if not isinstance(props, dict) or "source" not in props or not str(eid or "").startswith(NS):
        return None
    if not owner:
        return {"refused": True, "error": "a source edit is code: only the seat's owner writes it"}
    rest = {k: v for k, v in props.items() if k != "source"}
    return {"refused": False, "edit": edit_source(srv, eid, str(props["source"])), "properties": rest}
