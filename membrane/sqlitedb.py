"""THE SQLITE DATABASE (#971, the operator 2026-11-08: "build sqlite as parallel option and make it
default"): a drop-in for `cblite.Database` on the standard library's sqlite3.

Couchbase Lite Enterprise is free only for development and evaluation (production needs a paid
licence), and Community has no vector search; SQLite is public domain. Everything above the
database -- the field's backend (sqlstore.SQLiteBackend over cblstore.CBLiteBackend), the entity
store, chat sessions and messages, the data files -- talks to `be.db` through this one API, so they
run unchanged on either.

Same contract as cblite.Database:
- ONE connection and ONE thread that runs every operation from a priority queue: a turn's
  operation before any queued background one (cblite.TurnFirst / background()), each kind in
  arrival order; an operation on that thread itself runs at once.
- Transactions are speed batches that always commit. Another caller's write never rides inside an
  open batch: the batch commits early, the write commits on its own, the batch reopens for its
  owner (cblite.Database._outside_txn, 2026-11-02). Another caller's `begin` waits for it to end.
- Documents are JSON text by id, per collection; `get_json` returns bytes or None; `purge` removes.
- `query` / `iter_query` answer the SQL++ the callers use: `SELECT <fields> FROM membrane.<coll>
  [ORDER BY <field>]`, where a field is `META().id`, a dotted path into the document, `AS` an alias.
  A missing path is left out of the row (SQL++ MISSING), a null stays null. Anything else is refused.
- Vector indexes are not the database's here: the backend searches its vectors itself
  (sqlstore.SQLiteBackend). `vector_index` records the name; `update_lazy` has nothing to do.

Storage: one file `<directory>/<name>.sqlite3`, WAL; one table per collection, `k TEXT PRIMARY KEY,
body TEXT`; value indexes are expression indexes on json_extract(body, '$.<path>').
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import re
import sqlite3
import threading

from . import cblite as CB
from .cblite import TurnFirst, _queued, _queued_write


class SQLiteException(CB.CBLException):
    """A database error (a CBLException, so callers catching those catch it)."""


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SELECT = re.compile(r"^\s*SELECT\s+(?P<proj>.+?)\s+FROM\s+(?P<scope>\w+)\.(?P<coll>\w+)"
                     r"(?:\s+WHERE\s+(?P<valued>[\w.]+)\s+IS\s+VALUED)?"
                     r"(?:\s+ORDER\s+BY\s+(?P<order>[\w.]+)(?:\s+(?P<dir>ASC|DESC))?)?\s*$", re.I | re.S)


def _table(coll: str) -> str:
    if not _IDENT.match(coll or ""):
        raise SQLiteException(f"a collection name is a plain identifier: {coll!r}")
    return f"c_{coll}"


def _path(expr: str) -> list[str]:
    parts = [p.strip() for p in expr.split(".")]
    if not parts or not all(_IDENT.match(p) for p in parts):
        raise SQLiteException(f"not a field path: {expr!r}")
    return parts


_MISSING = object()


def _get(doc, parts):
    for p in parts:
        if not isinstance(doc, dict) or p not in doc:
            return _MISSING
        doc = doc[p]
    return doc


def _parse_select(sql: str):
    m = _SELECT.match(sql)
    if not m:
        raise SQLiteException(f"query not supported by the SQLite store: {sql[:120]!r}")
    projs = []
    for item in m.group("proj").split(","):
        item = item.strip()
        mm = re.match(r"^(?P<e>.+?)(?:\s+AS\s+(?P<a>\w+))?$", item, re.I)
        if mm is None:
            raise SQLiteException(f"not a projection: {item!r}")
        e, alias = mm.group("e").strip(), mm.group("a")
        if re.fullmatch(r"META\(\)\.id", e, re.I):
            projs.append((alias or "id", None))
        else:
            parts = _path(e)
            projs.append((alias or parts[-1], parts))
    order, valued = m.group("order"), m.group("valued")
    return (_table(m.group("coll")), projs, (_path(order) if order else None), (m.group("dir") or "ASC").upper(),
            (_path(valued) if valued else None))


class Database:
    """The field's SQLite store (#1111): the native engine's `membrane_vm.Db` (one SQLite per process, in
    Rust); the Python sqlite3 module is not used. Same API and `<dir>/<name>.sqlite3` layout as before.
    Without the native extension it falls back to `PyDatabase` (the old Python implementation)."""

    def __new__(cls, name: str, directory: str, scope: str = "membrane"):
        try:
            from . import membrane_vm as MV
            native = getattr(MV, "Db", None)
        except ImportError:
            native = None
        if native is None:
            return PyDatabase(name, directory, scope)
        self = object.__new__(cls)
        self._native = native(name, str(directory))
        self.scope = scope
        self.file = self._native.file
        self.h = self._native
        return self

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, attr):
        if attr.startswith("__") or attr == "_native":
            raise AttributeError(attr)
        return getattr(self._native, attr)


class PyDatabase:
    """One SQLite database: ONE connection, ONE thread (cblite.Database's contract)."""

    def __init__(self, name: str, directory: str, scope: str = "membrane"):
        self.lock = threading.RLock()
        self.turns = TurnFirst()
        self.scope = scope
        os.makedirs(directory, exist_ok=True)
        self.file = os.path.join(directory, f"{name}.sqlite3")
        self.cx = sqlite3.connect(self.file, isolation_level=None, check_same_thread=False)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=NORMAL")
        self.cx.execute("CREATE TABLE IF NOT EXISTS _vector_indexes (coll TEXT, name TEXT, PRIMARY KEY (coll, name))")
        self.h = self.cx
        self.colls: dict[str, str] = {}
        self._ops: queue.PriorityQueue = queue.PriorityQueue()
        self._n = itertools.count()
        self._tid = None
        self._caller = None
        self._txn_owner = None
        self._txn_depth = 0
        self._deferred: list = []
        threading.Thread(target=self._serve, name="sqlite-db", daemon=True).start()

    # ---- the database thread (cblite.Database's, unchanged) ----------------------------------
    _serve = CB.Database._serve
    _open_or_raise = CB.Database._open_or_raise
    _run = CB.Database._run

    def _outside_txn(self, fn):
        depth = self._txn_depth
        if depth:
            self.cx.execute("COMMIT")
        owner, self._txn_owner, self._txn_depth = self._txn_owner, None, 0
        try:
            return fn()
        finally:
            if depth:
                self.cx.execute("BEGIN")
            self._txn_owner, self._txn_depth = owner, depth

    def _x(self, sql: str, args=()):
        try:
            return self.cx.execute(sql, args)
        except sqlite3.Error as e:
            raise SQLiteException(f"{e} ({sql[:80]})") from None

    def coll(self, name: str):
        t = self.colls.get(name)
        if t is None:
            self._open_or_raise()
            t = _table(name)
            with self.lock:
                self._x(f"CREATE TABLE IF NOT EXISTS {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self.colls[name] = t
        return t

    # ---- transactions ---------------------------------------------------------------------
    @_queued_write
    def begin(self):
        self.lock.acquire()
        try:
            if not self._txn_depth:
                self._x("BEGIN")
        except Exception:
            self.lock.release()
            raise
        if not self._txn_depth:
            self._txn_owner = self._caller
        self._txn_depth += 1

    @_queued
    def end(self, commit=True):
        try:
            if self._txn_depth == 1:
                self._x("COMMIT" if commit else "ROLLBACK")
        finally:
            self._txn_depth = max(0, self._txn_depth - 1)
            if not self._txn_depth:
                self._txn_owner = None
            self.lock.release()

    # ---- documents --------------------------------------------------------------------------
    @_queued_write
    def put_json(self, coll: str, key: str, js: str):
        if not key:
            raise SQLiteException("a document needs an id")
        with self.lock:
            self._x(f"INSERT INTO {self.coll(coll)} (k, body) VALUES (?, ?) "
                    f"ON CONFLICT(k) DO UPDATE SET body = excluded.body", (key, js))

    @_queued
    def get_json(self, coll: str, key: str) -> bytes | None:
        if not key:
            return None
        with self.turns.lane(self.lock._is_owned()), self.lock:
            r = self._x(f"SELECT body FROM {self.coll(coll)} WHERE k = ?", (key,)).fetchone()
        return r[0].encode() if r else None

    @_queued
    def exists(self, coll: str, key: str) -> bool:
        if not key:
            return False
        with self.lock:
            return self._x(f"SELECT 1 FROM {self.coll(coll)} WHERE k = ?", (key,)).fetchone() is not None

    @_queued_write
    def purge(self, coll: str, key: str):
        with self.lock:
            self._x(f"DELETE FROM {self.coll(coll)} WHERE k = ?", (key,))

    @_queued
    def count(self, coll: str) -> int:
        with self.lock:
            return int(self._x(f"SELECT COUNT(*) FROM {self.coll(coll)}").fetchone()[0])

    # ---- indexes ------------------------------------------------------------------------------
    @_queued
    def value_index(self, coll: str, name: str, exprs: str):
        if not _IDENT.match(name or ""):
            raise SQLiteException(f"an index name is a plain identifier: {name!r}")
        cols = ", ".join(f"json_extract(body, '$.{'.'.join(_path(e.strip()))}')" for e in exprs.split(","))
        with self.lock:
            self._x(f"CREATE INDEX IF NOT EXISTS {name} ON {self.coll(coll)} ({cols})")

    @_queued
    def has_index(self, coll: str, name: str) -> bool:
        self.coll(coll)
        with self.lock:
            if self._x("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)).fetchone():
                return True
            return self._x("SELECT 1 FROM _vector_indexes WHERE coll = ? AND name = ?", (coll, name)).fetchone() is not None

    @_queued
    def vector_index(self, coll: str, name: str, expr: str, dims: int, centroids: int,
                     probes: int = 0, sq: int = 8, lazy: bool = True):
        """Recorded only: the backend searches its vectors itself (sqlstore)."""
        with self.lock:
            self._x("INSERT OR IGNORE INTO _vector_indexes (coll, name) VALUES (?, ?)", (coll, name))

    @_queued_write
    def update_lazy(self, coll: str, name: str, to_vec, limit: int = 500) -> int:
        return 0

    # ---- queries ------------------------------------------------------------------------------
    def iter_query(self, sql: str, params: dict | None = None):
        """Rows as dicts, streamed (the lock held until the iteration ends, as cblite's)."""
        table, projs, order, direction, valued = _parse_select(sql)
        q = f"SELECT k, body FROM {table}"
        if order is not None:
            q += f" ORDER BY json_extract(body, '$.{'.'.join(order)}') {direction}, rowid"
        else:
            q += " ORDER BY rowid"
        with self.lock:
            if not self.cx.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                                   (table,)).fetchone():
                # as Couchbase Lite: a query never creates its collection (a read-only snapshot of an
                # older store must not write one); the callers create it first with coll()
                raise SQLiteException(f"no collection {table[2:]!r}")
            cur = self.cx.execute(q)
            for k, body in cur:
                try:
                    doc = json.loads(body)
                except ValueError:
                    continue
                if valued is not None and _get(doc, valued) in (_MISSING, None):
                    continue                     # WHERE <path> IS VALUED: present and not null
                row = {}
                for alias, parts in projs:
                    v = k if parts is None else _get(doc, parts)
                    if v is not _MISSING:
                        row[alias] = v
                yield row

    @_queued
    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        return list(self.iter_query(sql, params))

    def close(self):
        self._run(self._close)
        self._closed = True
        ops = getattr(self, "_ops", None)
        if ops is not None:
            ops.put((2, next(self._n), None, None))

    def _close(self):
        with self.lock:
            try:
                if self._txn_depth:
                    self.cx.execute("COMMIT")
                self.cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.cx.close()
            self.h = None


# ---- finding and opening a seat's store, whichever kind (the tools that read it beside a live seat) ----

STORE_DIRS = ("field.sqlite", "field.cblite")


def store_dir(base):
    """The seat's store directory under `base` (its .membrane): the SQLite one or the Couchbase Lite one."""
    from pathlib import Path
    for n in STORE_DIRS:
        p = Path(base) / n
        if p.exists():
            return p
    return None


def is_sqlite(directory, name: str = "field") -> bool:
    from pathlib import Path
    return (Path(directory) / f"{name}.sqlite3").exists()


def open_db(directory, name: str = "field"):
    """The database in `directory`, by what is there: SQLite when it holds `<name>.sqlite3`, else Couchbase Lite."""
    if is_sqlite(directory, name):
        return Database(name, str(directory))
    return CB.Database(CB.load(), name, str(directory))

