"""WHICH STORE A SEAT USES (#971, the operator 2026-11-08: "build sqlite as parallel option and make it
default ... can membrane ask during first startup which seat type to use? can you add migration command
between the two?").

Two kinds, the same contract (cblstore.CBLiteBackend; sqlstore.SQLiteBackend over it):
- `sqlite`: SQLite, public domain, shipped with membrane. THE DEFAULT for a new seat.
- `cbl`: Couchbase Lite (Enterprise, its libraries fetched by scripts/fetch-cbl.sh). Free for development
  and evaluation only; production use needs a Couchbase licence.

The seat's choice lives in `<.membrane>/store.json` ({"kind": ...}). Which store opens (efstore.open_backend):
1. MEMBRANE_EFIELD_STORE=sqlite|cbl|file|mem, when set;
2. the seat's choice;
3. a store already there: an existing Couchbase Lite seat keeps it until it is migrated;
4. a new seat: the default, SQLite -- asked first when the seat starts on a terminal (`ensure_chosen`).

`membrane store status` says which store this seat uses and what is in it; `membrane store migrate --to
sqlite|cbl` copies every collection into the other kind (the seat stopped: the writer lock says so) and
switches the choice. The source is left as it was, a backup.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

KINDS = ("sqlite", "cbl")
DEFAULT = "sqlite"
CHOICE_FILE = "store.json"
DESCRIBE = {
    "sqlite": "SQLite -- free, ships with membrane (the default)",
    "cbl": "Couchbase Lite -- needs its libraries (scripts/fetch-cbl.sh); Enterprise is free for "
           "development and evaluation only, production use needs a Couchbase licence",
}
# every collection a seat keeps in its store: the field's, its meta, the entity store, chat sessions and
# messages, the data files, the recall labels (Couchbase Lite has no listing call in our binding)
SEAT_COLLECTIONS = ("events", "sentences", "terms", "entity_states", "entity_deltas", "relations", "claims",
                    "annotations", "origin_stats", "chunks", "meta", "entities", "sessions", "messages",
                    "files", "recall_labels")


def dirs(field_path: str = "") -> tuple[Path, Path, Path, Path]:
    """(the seat's .membrane, the old journal dir, the SQLite store dir, the Couchbase Lite store dir)."""
    from .efstore import _field_dirname_for
    base = Path(field_path).resolve().parent if field_path else Path.cwd()
    jdir = base / _field_dirname_for(field_path)
    return base, jdir, jdir.with_name(jdir.name + ".sqlite"), jdir.with_name(jdir.name + ".cblite")


def chosen(base) -> str | None:
    try:
        k = json.loads((Path(base) / CHOICE_FILE).read_text()).get("kind")
    except (OSError, ValueError, AttributeError):
        return None
    return k if k in KINDS else None


def choose(base, kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"a store kind is one of {', '.join(KINDS)}")
    Path(base).mkdir(parents=True, exist_ok=True)
    (Path(base) / CHOICE_FILE).write_text(json.dumps({"kind": kind, "ts": time.time()}) + "\n")


def resolve(field_path: str = "", mode: str | None = None) -> str:
    """The kind this seat opens: sqlite, cbl, file or mem (see the module's order)."""
    mode = mode if mode is not None else os.environ.get("MEMBRANE_EFIELD_STORE", "auto")
    if mode in ("sqlite", "cbl", "file", "mem"):
        return mode
    base, _jdir, sdir, cdir = dirs(field_path)
    k = chosen(base)
    if k:
        return k
    if cdir.exists():
        return "cbl"
    if sdir.exists():
        return "sqlite"
    return DEFAULT


def is_new(field_path: str = "") -> bool:
    base, jdir, sdir, cdir = dirs(field_path)
    try:
        has_journal = jdir.is_dir() and any(jdir.iterdir())     # an old journal seat is not new
    except OSError:
        has_journal = False
    return chosen(base) is None and not sdir.exists() and not cdir.exists() and not has_journal


def ensure_chosen(field_path: str = "", interactive: bool | None = None, ask=input, out=sys.stderr) -> str:
    """A new seat's first start: ask which store on a terminal, else take the default; the answer is kept.
    A seat that already has a store or a choice is never asked."""
    base = dirs(field_path)[0]
    if os.environ.get("MEMBRANE_EFIELD_STORE", "auto") != "auto" or not is_new(field_path):
        return resolve(field_path)
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    kind = DEFAULT
    if interactive:
        print("This seat has no store yet. Which one?", file=out)
        for i, k in enumerate(KINDS, 1):
            print(f"  {i}) {DESCRIBE[k]}", file=out)
        try:
            a = str(ask("Store [1]: ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            a = ""
        kind = {"": DEFAULT, "1": "sqlite", "2": "cbl", "sqlite": "sqlite", "cbl": "cbl",
                "couchbase": "cbl"}.get(a, DEFAULT)
    choose(base, kind)
    print(f"[store] this seat uses {kind} ({base / CHOICE_FILE}); `membrane store migrate --to ...` switches",
          file=out)
    return kind


# ---- status and migration ------------------------------------------------------------------------------

def _store_dir(field_path: str, kind: str) -> Path:
    _base, _jdir, sdir, cdir = dirs(field_path)
    return sdir if kind == "sqlite" else cdir


def _open(kind: str, d: Path):
    from . import cblite as CB
    from . import sqlitedb as SDB
    if kind == "sqlite":
        return SDB.Database("field", str(d))
    return CB.Database(CB.load(), "field", str(d))


def _lock(d: Path):
    """The store's writer lock (cblstore's, issue #26): a live seat holds it, so a migration refuses."""
    import fcntl
    from .cblstore import StoreBusy
    d.mkdir(parents=True, exist_ok=True)
    lk = d.parent / f".{d.name}.writer.lock"
    fd = os.open(str(lk), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise StoreBusy(f"the store is busy: {lk} is held (stop the seat first)") from None
    return fd


def _colls(db, kind: str) -> list[str]:
    if kind == "sqlite":
        rows = db.cx.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'c_%'").fetchall()
        have = [r[0][2:] for r in rows]
        return [c for c in SEAT_COLLECTIONS if c in have] + [c for c in have if c not in SEAT_COLLECTIONS]
    return list(SEAT_COLLECTIONS)


def _keys(db, coll: str) -> list[str]:
    try:
        return [r["k"] for r in db.query(f"SELECT META().id AS k FROM membrane.{coll}") if r.get("k")]
    except Exception as e:
        if "no such collection" in str(e).lower():     # a collection never created is empty; any other error is real
            return []
        raise


def status(field_path: str = "") -> dict:
    base, _jdir, sdir, cdir = dirs(field_path)
    out = {"uses": resolve(field_path), "choice": chosen(base), "stores": {}}
    for kind, d in (("sqlite", sdir), ("cbl", cdir)):
        if not d.exists():
            continue
        info: dict = {"path": str(d)}
        try:
            db = _open(kind, d)
            try:
                info["collections"] = {c: db.count(c) for c in _colls(db, kind) if kind == "sqlite" or _keys(db, c)}
            finally:
                db.close()
        except Exception as e:                      # noqa: BLE001 -- e.g. Couchbase Lite's libraries missing
            info["error"] = f"{type(e).__name__}: {e}"
        out["stores"][kind] = info
    return out


def migrate(field_path: str = "", to: str = DEFAULT, out=sys.stderr, batch: int = 1000) -> dict:
    """Copy every collection of this seat's store into the other kind, then switch the choice. The source
    stays as it was (a backup). Refuses while the seat runs, and when the target already exists."""
    if to not in KINDS:
        raise ValueError(f"--to is one of {', '.join(KINDS)}")
    src_kind = resolve(field_path)
    if src_kind not in KINDS:
        raise ValueError(f"this seat's store is {src_kind!r}: only sqlite <-> cbl are migrated")
    if src_kind == to:
        raise ValueError(f"this seat already uses {to}")
    src_d, dst_d = _store_dir(field_path, src_kind), _store_dir(field_path, to)
    if not src_d.exists():
        raise ValueError(f"no {src_kind} store at {src_d}")
    if dst_d.exists():
        raise ValueError(f"{dst_d} already exists: move it away first (it may be an older copy)")
    locks = [_lock(src_d)]
    t0 = time.time()
    counts: dict[str, int] = {}
    try:
        locks.append(_lock(dst_d))
        src, dst = _open(src_kind, src_d), _open(to, dst_d)
        try:
            for c in _colls(src, src_kind):
                keys = _keys(src, c)
                if not keys:
                    continue
                dst.coll(c)
                n = 0
                for i in range(0, len(keys), batch):
                    dst.begin()
                    try:
                        for k in keys[i:i + batch]:
                            js = src.get_json(c, k)
                            if js is not None:
                                dst.put_json(c, k, js.decode() if isinstance(js, bytes) else js)
                                n += 1
                    finally:
                        dst.end()
                counts[c] = n
                print(f"[store] {c}: {n} copied", file=out)
            for c, n in counts.items():                   # check against the SOURCE, not the loop's own tally
                if dst.count(c) != src.count(c):
                    raise RuntimeError(f"{c}: {dst.count(c)} in the new store, {src.count(c)} in the source ({n} copied)")
        finally:
            src.close()
            dst.close()
    except Exception:
        import shutil
        shutil.rmtree(dst_d, ignore_errors=True)          # a half-made target is no store
        raise
    finally:
        for fd in locks:
            os.close(fd)
    choose(dirs(field_path)[0], to)
    print(f"[store] migrated {src_kind} -> {to} in {time.time() - t0:.1f}s; this seat now uses {to}. "
          f"The {src_kind} store stays at {src_d} as a backup.", file=out)
    return {"from": src_kind, "to": to, "copied": counts, "backup": str(src_d)}


def cli_main(argv=None) -> int:
    """`membrane store [status | migrate --to sqlite|cbl] [--field PATH]`."""
    import argparse
    ap = argparse.ArgumentParser(prog="membrane store", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("action", nargs="?", default="status", choices=("status", "migrate"))
    ap.add_argument("--to", choices=KINDS, help="migrate: the kind to move this seat's store to")
    ap.add_argument("--field", default=os.path.join(".membrane", "field.json"), help="the seat's field path")
    a = ap.parse_args(argv)
    if a.action == "status":
        print(json.dumps(status(a.field), indent=2))
        return 0
    if not a.to:
        ap.error("migrate needs --to sqlite|cbl")
    try:
        migrate(a.field, a.to)
    except Exception as e:                                # noqa: BLE001
        print(f"[store] migration refused: {e}", file=sys.stderr)
        return 1
    return 0
