"""LIVE CODE TREES (#715 part A, the operator 2026-10-29: "let's make our code
trees alive"): the project's Python source as entities, one per SYMBOL -- the
module, each class, each function or method -- read-only for now.

IDENTITY IS STABLE: `membrane::src:<path>::<qualname>` (the module itself is
`membrane::src:<path>`), across edits; the content hash and the byte / line
span are properties. A nested def gets a dotted qualname (`Pump.run.inner`); a
decorated definition's span starts at its first decorator. `parent` names the
enclosing symbol's entity.

FILES ARE THE SOURCE OF TRUTH: codegraph's mtime sweep (`stale_paths`) names
the files changed outside the field (an editor, git, a checkout); each is
re-parsed with the tree-sitter grammar (lang.GRAMMARS["py"]) and its symbols'
entities updated. A symbol gone from its file is RETIRED (status 'retired',
mark-don't-delete); a file that brings it back revives the same entity. A
half-written file (a parse error) keeps its old symbols until it parses. Reading
a symbol reads its span from disk (`source`).

Part B (the write path: entity_edit source=...) and part C (codegraph's symbols
and call edges on these trees) build on this."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity

NS = MEMBRANE_NS + "src:"
LIVE, RETIRED = "live", "retired"
_DEFS = {"class_definition": "class", "function_definition": "function"}


def eid_of(rel: str, qual: str = "") -> str:
    return NS + rel + (f"::{qual}" if qual else "")


def _hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:16]


def symbols(src: bytes) -> list[dict] | None:
    """The module and its symbols, outermost first; None when it does not parse."""
    from . import lang as LG
    import tree_sitter as ts
    root = ts.Parser(LG._language("py")).parse(src).root_node
    if root.has_error:
        return None
    out = [{"qual": "", "kind": "module", "span": [0, len(src)],
            "lines": [1, src.count(b"\n") + 1], "hash": _hash(src), "parent": None}]

    def walk(n, prefix: str, parent: str, parent_kind: str) -> None:
        for c in n.named_children:
            node = c.child_by_field_name("definition") if c.type == "decorated_definition" else c
            if node is not None and node.type in _DEFS and node.child_by_field_name("name") is not None:
                q = prefix + node.child_by_field_name("name").text.decode()
                kind = _DEFS[node.type]
                if kind == "function" and parent_kind == "class":
                    kind = "method"
                a, b = c.start_byte, c.end_byte          # a decorator belongs to its def
                out.append({"qual": q, "kind": kind, "span": [a, b],
                            "lines": [c.start_point[0] + 1, c.end_point[0] + 1],
                            "hash": _hash(src[a:b]), "parent": parent})
                walk(node, q + ".", q, kind)
            else:
                walk(c, prefix, parent, parent_kind)
    walk(root, "", "", "module")
    return out


def default_root(srv) -> Path:
    """The project root codegraph reads (the field's parent's parent)."""
    return Path(srv.membrane.field_path).parent.parent


def attach(srv, root) -> dict:
    """The seat's sweep state: the root, and per file its mtime and symbols --
    rebuilt from the store's live entities, so a restart re-reads nothing new."""
    est = srv.entity_store
    files: dict = {}
    for eid, d in list((est.docs or {}).items()):
        if not eid.startswith(NS):
            continue
        p = d.properties or {}
        if p.get("status") == RETIRED:
            continue
        f = files.setdefault(str(p.get("path") or ""), {"mtime": None, "symbols": []})
        if p.get("kind") == "module":
            f["mtime"] = p.get("mtime")
        else:
            f["symbols"].append(str(p.get("qualname") or ""))
    g = {"root": str(Path(root)), "files": files}
    srv._srctree = g
    return g


def _graph(srv) -> dict:
    g = getattr(srv, "_srctree", None)
    return g if g is not None else attach(srv, default_root(srv))


def _retire(est, eid: str) -> bool:
    d = (est.docs or {}).get(eid)
    if d is None or (d.properties or {}).get("status") == RETIRED:
        return False
    d.properties = {**(d.properties or {}), "status": RETIRED, "retired_ts": time.time()}
    est.upsert(d, embedder=None)
    return True


def _put(est, rel: str, s: dict, mtime: float) -> bool:
    """Upsert one symbol's entity; False when nothing about it changed."""
    eid = eid_of(rel, s["qual"])
    want = {"name": f"{rel}::{s['qual']}" if s["qual"] else rel, "path": rel,
            "qualname": s["qual"], "kind": s["kind"], "span": s["span"], "lines": s["lines"],
            "hash": s["hash"], "status": LIVE,
            "parent": eid_of(rel, s["parent"]) if s["parent"] is not None else None}
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


def sync_file(srv, rel: str) -> dict:
    """Re-read one repo-relative file into its symbols' entities."""
    g = _graph(srv)
    est = srv.entity_store
    files = g["files"]
    old = files.get(rel) or {"symbols": []}
    out = {"upserted": 0, "retired": 0}
    p = Path(g["root"]) / rel
    if not p.is_file():
        for q in old["symbols"] + [""]:
            out["retired"] += _retire(est, eid_of(rel, q))
        files.pop(rel, None)
        return out
    try:
        src, mtime = p.read_bytes(), p.stat().st_mtime
    except OSError:
        return out
    syms = symbols(src) if b"\x00" not in src[:1024] else None
    if syms is None:
        return out                      # half-written or not Python: the old symbols stand
    for s in syms:
        out["upserted"] += _put(est, rel, s, mtime)
    now = {s["qual"] for s in syms if s["qual"]}
    for q in old["symbols"]:
        if q not in now:
            out["retired"] += _retire(est, eid_of(rel, q))
    files[rel] = {"mtime": mtime, "symbols": sorted(now)}
    return out


def _packages(root: Path) -> set:
    """THE SCOPE (the operator's card): the project's own packages -- the ones
    its pyproject.toml ships ([tool.setuptools] packages; this repo: membrane/),
    else the top-level dirs with an __init__.py."""
    try:
        import tomllib
        pk = ((tomllib.loads((root / "pyproject.toml").read_text()).get("tool") or {})
              .get("setuptools") or {}).get("packages")
        if isinstance(pk, list) and pk:
            return {str(p).split(".")[0] for p in pk}
    except (OSError, ValueError, ImportError):
        pass
    try:
        return {d.name for d in root.iterdir() if d.is_dir() and (d / "__init__.py").is_file()}
    except OSError:
        return set()


def sweep(srv) -> dict:
    """codegraph's mtime sweep over the root's packages: every file changed,
    added or deleted since the last sweep is re-read. -> counts."""
    from .codegraph import stale_paths
    g = _graph(srv)
    pk = _packages(Path(g["root"]))
    out = {"files": 0, "upserted": 0, "retired": 0}
    for rel in stale_paths(g):
        if Path(rel).parts[0] not in pk and rel not in g["files"]:
            continue
        r = sync_file(srv, rel)
        out["files"] += 1
        out["upserted"] += r["upserted"]
        out["retired"] += r["retired"]
    return out


_BUSY: set = set()


def sweep_soon(srv) -> None:
    """The presence pulse's hook: one sweep at a time, off the thinker's beat
    (the first one reads every package file)."""
    if getattr(srv, "entity_store", None) is None or getattr(srv, "membrane", None) is None \
            or id(srv) in _BUSY:
        return
    import threading
    _BUSY.add(id(srv))

    def go():
        try:
            r = sweep(srv)
            if r["files"]:
                import sys
                print(f"[srctree] swept {r['files']} file(s): {r['upserted']} symbol(s) "
                      f"updated, {r['retired']} retired", file=sys.stderr)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            _BUSY.discard(id(srv))
    threading.Thread(target=go, daemon=True, name="srctree-sweep").start()


def source(srv, eid: str) -> str | None:
    """A symbol's code, read from disk (a file changed since the sweep is re-read first)."""
    est = srv.entity_store
    d = (est.docs or {}).get(eid)
    if d is None:
        return None
    p = d.properties or {}
    rel = str(p.get("path") or "")
    path = Path(_graph(srv)["root"]) / rel
    try:
        b = path.read_bytes()
        if (_graph(srv)["files"].get(rel) or {}).get("mtime") != path.stat().st_mtime:
            sync_file(srv, rel)
            p = (est.docs[eid].properties or {})
    except OSError:
        return None
    if p.get("status") == RETIRED:
        return None
    a, z = p.get("span") or [0, 0]
    return b[a:z].decode("utf-8", errors="replace")
