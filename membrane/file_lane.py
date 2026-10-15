"""THE FILE LANE (the entity-scripts covenant, the reads/writes): the
engine's file hands routed through a script's VERDICTS -- a script
asks ({read}/{write}/{edit_file}/{mine}), the engine execs via
night.file_read_entity/file_write_entity/mine_artifact, the result rides
the ASKING entity's props (file_read_*/file_write_*/mine_*), and the
f:<path> entity
mints/updates exactly as the old file tools did. NO tool surface: the
MCP's read/write/edit are REMOVED -- everything is entity scripts.
"""
import os
import time

def _store_and_fld(srv):
    est = getattr(srv, "entity_store", None)
    fld = getattr(srv, "efield", None)
    return est, fld


def read(srv, ent, path: str, offset=None, limit=None) -> dict:
    """A script's {read: path} verdict: the engine reads the file
    THROUGH the f:<path> entity; the result head lands on the asking
    entity's file_read prop + the observation files as always."""
    from .night import file_read_entity
    est, fld = _store_and_fld(srv)
    out = file_read_entity(fld, est, srv.membrane, str(path),
                           offset=offset, limit=limit)
    props = ent.properties or {}
    props["file_read_ts"] = time.time()
    if out.get("ok"):
        props["file_read_last"] = out.get("head", "")[:200]
    else:
        props["script_last_error"] = out.get("error", "")[:300]
    ent.properties = props
    if est is not None:
        est.upsert(ent, embedder=None)
    return out


def write(srv, ent, path: str, content: str, create_dirs: bool = True
          ) -> dict:
    from .night import file_write_entity
    est, fld = _store_and_fld(srv)
    out = file_write_entity(fld, est, srv.membrane, str(path),
                            content, create_dirs=create_dirs)
    props = ent.properties or {}
    props["file_write_ts"] = time.time()
    if not out.get("ok"):
        props["script_last_error"] = out.get("error", "")[:300]
    ent.properties = props
    if est is not None:
        est.upsert(ent, embedder=None)
    return out


def edit(srv, ent, path: str, old: str, new: str) -> dict:
    """A script's {edit_file: [path, old, new]} verdict (edit rides
    write's lane with the exact-string contract)."""
    from .night import file_write_entity
    est, fld = _store_and_fld(srv)
    from pathlib import Path as _P
    p = _P(str(path)) if _P(str(path)).is_absolute() else (
        _P(os.getcwd()) / str(path))
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "error": f"no such file: {p}"}
    occurrences = content.count(old)
    if occurrences == 0:
        return {"ok": False, "error": f"old not found in {p}"}
    out = file_write_entity(fld, est, srv.membrane, str(p),
                            content.replace(
                                old, new, -1 if occurrences > 1 and
                                new == old else 1),
                            create_dirs=False)
    props = ent.properties or {}
    props["file_edit_ts"] = time.time()
    if not out.get("ok"):
        props["script_last_error"] = out.get("error", "")[:300]
    ent.properties = props
    if est is not None:
        est.upsert(ent, embedder=None)
    return out


def mine(srv, ent, spec: str) -> dict:
    """A script's {mine: path} verdict (issue #116): the lazy-ingest
    primitive -- route the re-read artifact's salient spans through the
    field extractor, one bounded pass. The prototype fetch/clean/mine
    seat scripts collapse to THIS thin wrapper over night.mine_artifact."""
    from .night import mine_artifact
    est, fld = _store_and_fld(srv)
    out = mine_artifact(fld, est, str(spec))
    props = ent.properties or {}
    props["mine_ts"] = time.time()
    if out.get("ok"):
        props["mine_mined"] = out.get("mined")
        props["mine_spans"] = out.get("spans")
    else:
        props["script_last_error"] = (out.get("reason") or "mine failed")[:300]
    ent.properties = props
    if est is not None:
        est.upsert(ent, embedder=None)
    return out


