"""THE SEAT'S SMALL DATA FILES IN ITS DATABASE (#799, the operator 2026-11-01: data in
the database; secrets, hand-edited config and append-only logs stay files).

A whole-JSON state file (the absorb watermark, the router's state, the watch board,
the background-usage tally) is one document in the `files` collection of the seat's
Couchbase Lite store, keyed by its file name, once the seat registered its database
and data folder (`use`, at boot). A file in that folder migrates the first time it is
read and is kept renamed `<name>.migrated-<time>`. Any other path, and every seat
without the database (lab fields, tests), reads and writes the file as before."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

COLL = "files"
# the seat's state files: migrated as soon as the seat registers its database (the
# watch board loads before the field's store opens, so a first read would come late)
SEAT_FILES = ("serve-absorb.json", "router.json", "watches.json", "bg-usage.json")
_SEAT: dict = {"db": None, "dir": None}


def use(db, data_dir) -> None:
    """The seat's database and its data folder (None, None: files only)."""
    _SEAT["db"] = db
    _SEAT["dir"] = Path(data_dir).resolve() if data_dir is not None else None
    if db is not None and _SEAT["dir"] is not None:
        for name in SEAT_FILES:
            if (_SEAT["dir"] / name).exists():
                read_json(_SEAT["dir"] / name)


def _db_for(path: Path):
    db, d = _SEAT["db"], _SEAT["dir"]
    if db is None or d is None:
        return None
    try:
        return db if path.resolve().parent == d else None
    except OSError:
        return None


def _key(path: Path) -> str:
    from .cblite import doc_key
    return doc_key("file:" + path.name)


def _read_file(path: Path):
    return json.loads(path.read_text())


def read_json(path, default=None):
    """The document (or file) at `path`, or `default` when there is none or it
    can't be read."""
    path = Path(path)
    db = _db_for(path)
    if db is None:
        try:
            return _read_file(path)
        except Exception:
            return default
    try:
        raw = db.get_json(COLL, _key(path))
    except Exception as e:
        print(f"[datafiles] {path.name}: database read failed ({e})", file=sys.stderr)
        raw = None
    if raw is not None:
        try:
            return json.loads(raw).get("d", default)
        except Exception:
            return default
    if not path.exists():
        return default
    try:
        doc = _read_file(path)
    except Exception:
        return default
    try:                                          # the first read with the database
        db.put_json(COLL, _key(path), json.dumps({"name": path.name, "d": doc}))
        os.replace(path, path.with_name(f"{path.name}.migrated-{time.strftime('%Y%m%d-%H%M%S')}"))
        print(f"[datafiles] migrated {path.name} into couchbase lite; the file is kept as a backup",
              file=sys.stderr)
    except Exception as e:
        print(f"[datafiles] {path.name}: migration failed ({e})", file=sys.stderr)
    return doc


def write_json(path, doc) -> None:
    """`doc` at `path`: the seat's document, or the file written beside itself
    (<name>.tmp) and swapped in whole. Raises like a file write would."""
    path = Path(path)
    db = _db_for(path)
    if db is not None:
        db.put_json(COLL, _key(path), json.dumps({"name": path.name, "d": doc}))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, path)
