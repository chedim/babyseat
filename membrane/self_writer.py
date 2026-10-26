"""THE SINGLE WRITER FOR self/ (#440 part 1).

Blurp is moving into its own process (#440): the child will own
<data dir>/self/ -- the record (record.jsonl), its archive and manifest, the
consolidation journal, mode.json, energy.json and world.json. This module is that owner's
interface, still in-process: every write under self/ goes through it, and
nothing else in the seat opens, replaces, truncates, removes or creates a
file there (tests/test_self_writer_440.py holds both, at run time and in
the source).

Two layers:
- The writes themselves: `append` (a record line, and the in-memory record
  it keeps), `save_json` (a state file, swapped in whole), and the path
  primitives the consolidation's journal steps use (`sync_write`,
  `replace`, `unlink`, `truncate`, `mkdir`).
- The seat-side requests: the panel's switch and accept (`set_mode`,
  `accept`) and the sleep's `consolidate`. Today
  they run self_loop's code here; once the child is up (#440 part 4) they
  become requests to it, and the seat stops writing self/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _SL():
    from . import self_loop as SL
    return SL


def dir_of(srv) -> Path | None:
    """<data dir>/self, or None for a seat with no data dir."""
    mp = getattr(srv, "_models_path", None)
    return Path(mp).parent / "self" if mp else None


# ---- the writes ---------------------------------------------------------------

def append(srv, rec: dict) -> None:
    """One line on the record, and on the in-memory record when it is loaded."""
    SL = _SL()
    p = SL.record_path(srv)
    if p is None:
        return
    mkdir(p.parent)
    line = json.dumps(rec) + "\n"
    with SL._lock, open(p, "a") as fh:
        fh.write(line)
        rt = SL._rt(srv)
        cache = rt.get("recs")
        if cache is not None:
            cache.append(rec)
            rt["recs_bytes"] = rt.get("recs_bytes", 0) + len(line)


def write_json(path: Path, doc, sort_keys: bool = False) -> None:
    """A state file, written beside itself (<name>.tmp) and swapped in whole."""
    path = Path(path)
    mkdir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(json.dumps(doc, sort_keys=sort_keys))
    replace(tmp, path)


def save_json(srv, name: str, doc, sort_keys: bool = False) -> None:
    """self/<name>, swapped in whole; nothing without a data dir."""
    d = dir_of(srv)
    if d is not None:
        write_json(d / name, doc, sort_keys=sort_keys)


def sync_write(path: Path, data: bytes, mode: str = "ab") -> None:
    """Bytes to a file, flushed and synced before it returns."""
    with open(path, mode) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def unlink(path: Path) -> None:
    Path(path).unlink(missing_ok=True)


def truncate(path: Path, size: int) -> None:
    with open(path, "r+b") as fh:
        fh.truncate(int(size))


def mkdir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


# ---- the seat-side requests (the child's, once it runs: #440 part 4) ---------------

def set_mode(srv, m: str, by: str = "operator") -> bool:
    return _SL().set_mode(srv, m, by=by)


def accept(srv, i, by: str = "operator") -> dict:
    return _SL().accept(srv, i, by=by)


def consolidate(srv, now: float | None = None) -> dict | None:
    return _SL().consolidate(srv, now)
