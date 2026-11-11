"""The store driver's reads through the seat's OWN SQLite (0.7.53, data safety).

The native field engine's `sql(...)` built-in used to open the seat's field database itself, with a copy of SQLite
compiled into membrane_vm (rusqlite "bundled"), while the seat's Python opened the same WAL file with CPython's
sqlite3. Two SQLite copies in one process on one WAL database is SQLite's documented corruption hazard: POSIX locks
are per process, so a close in one copy drops the other's locks, and the -shm/-wal can be truncated under the other's
mmap (SIGBUS in walFindFrame, or "database disk image is malformed"). Now the engine keeps its off-thread, never-hang
contract (native/src/store.rs) but every query runs here, on the one SQLite this process has.

The driver's rules are unchanged: read-only connections (`mode=ro` + `query_only`), a statement that is not a pure
read is refused before it runs (an authorizer that admits only reads -- SQLite's own `sqlite3_stmt_readonly` is not
exposed to Python), arguments are bound and never spliced, a locked database is waited for up to 5 s, no row cap and
no time limit; `interrupt(token)` stops a running query (a getter asked another question, or the seat stops)."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from urllib.parse import quote

BUSY_TIMEOUT_S = 5.0          # the drivers' default (Python sqlite3 timeout=5.0, rusqlite 5 s)
ONLY_READS = "sql: only reads (SELECT / WITH); the field writes through the engine"
INTERRUPTED = "sql: interrupted (the getter asked another question, or the seat is stopping)"

_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                 getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_BINDINGS = re.compile(r"uses (\d+), and there (?:are|is) (\d+) supplied")


class SqlError(Exception):
    """A query's error, worded like the native driver's (the engine shows it as is)."""


def _authorize(action, *_args):
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


def _to_sql(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)                       # a list or a dict goes in as JSON text (json_each() reads it)
    return v


def _from_sql(v):
    if v is None or isinstance(v, (bytes, memoryview)):
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class Reader:
    """One field database, read-only, through this process's sqlite3. Thread-safe: each query takes a connection of
    its own from a small pool; `interrupt` reaches a running one."""

    def __init__(self, path: str):
        p = os.path.abspath(path)
        self.path = p
        self._uri = "file:" + quote(p) + "?mode=ro"
        self._lock = threading.Lock()
        self._idle: list = []
        self._running: dict = {}
        self._cancelled: set = set()
        self._idle.append(self._open())            # fail now when the store can't be opened

    def _open(self):
        try:
            c = sqlite3.connect(self._uri, uri=True, timeout=BUSY_TIMEOUT_S, check_same_thread=False,
                                isolation_level=None)
            c.execute("PRAGMA query_only = 1")
            c.set_authorizer(_authorize)
            return c
        except sqlite3.Error as e:
            raise SqlError(f"store {self.path}: {e}") from None

    def check(self, sql: str, nargs: int) -> None:
        """Compile the statement without running it (the old driver's prepare step, on the engine's call so a bad
        query -- a write, a missing table, the wrong argument count -- is refused at once). Raises SqlError."""
        if sql.lstrip()[:7].upper() == "EXPLAIN":
            return                                 # EXPLAIN EXPLAIN is no statement: its run says what's wrong
        with self._lock:
            conn = self._idle.pop() if self._idle else None
        if conn is None:
            conn = self._open()
        try:
            conn.execute("EXPLAIN " + sql, [None] * nargs).close()
        except (sqlite3.ProgrammingError, sqlite3.Warning) as e:
            m = _BINDINGS.search(str(e))
            if m:
                raise SqlError(f"sql: the query takes {m.group(1)} argument(s), got {m.group(2)}") from None
            raise SqlError(f"sql: {e}") from None
        except sqlite3.DatabaseError as e:
            msg = str(e)
            if "not authorized" in msg or "readonly" in msg or "read-only" in msg:
                raise SqlError(ONLY_READS) from None
            raise SqlError(f"sql: {msg}") from None
        finally:
            with self._lock:
                self._idle.append(conn)

    def run(self, token: int, sql: str, args_json: str) -> str:
        """Run one read; the rows as JSON text (a list of {column: value}). Raises SqlError."""
        try:
            args = [_to_sql(a) for a in json.loads(args_json or "[]")]
        except ValueError as e:
            raise SqlError(f"sql: bad arguments ({e})") from None
        with self._lock:
            conn = self._idle.pop() if self._idle else None
        if conn is None:
            conn = self._open()
        with self._lock:
            self._running[token] = conn
            cancelled = token in self._cancelled
        # an interrupt can come before the query starts (its thread waits for the GIL): it is remembered, and seen here
        try:
            try:
                if cancelled:
                    raise sqlite3.OperationalError("interrupted")
                cur = conn.execute(sql, args)
                cols = [d[0] for d in (cur.description or [])]
                rows = [{c: _from_sql(v) for c, v in zip(cols, r)} for r in cur]
            except (sqlite3.ProgrammingError, sqlite3.Warning) as e:   # before DatabaseError: it is a subclass
                msg = str(e)
                m = _BINDINGS.search(msg)
                if m:
                    raise SqlError(f"sql: the query takes {m.group(1)} argument(s), got {m.group(2)}") from None
                raise SqlError(f"sql: {msg}") from None
            except sqlite3.DatabaseError as e:
                msg = str(e)
                if "not authorized" in msg or "readonly" in msg or "read-only" in msg:
                    raise SqlError(ONLY_READS) from None
                if "interrupted" in msg:
                    raise SqlError(INTERRUPTED) from None
                raise SqlError(f"sql: {msg}") from None
            return json.dumps(rows, default=str)
        finally:
            with self._lock:
                self._running.pop(token, None)
                self._cancelled.discard(token)
                self._idle.append(conn)

    def interrupt(self, token: int) -> None:
        with self._lock:
            self._cancelled.add(token)             # kept until its run ends: a run not started yet sees it too
            c = self._running.get(token)
        if c is not None:
            # SQLite drops an interrupt that comes while no statement runs (between the cancelled check above and
            # the statement's start): fire it again until the run ends
            threading.Thread(target=self._keep_interrupting, args=(token, c), name="membrane-sql-interrupt",
                             daemon=True).start()

    def _keep_interrupting(self, token: int, conn) -> None:
        while True:
            with self._lock:                       # under the lock: never reaches the connection's next query
                if self._running.get(token) is not conn:
                    return
                try:
                    conn.interrupt()
                except sqlite3.Error:
                    return
            time.sleep(0.02)

    def close(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for c in idle:
            try:
                c.close()
            except sqlite3.Error:
                pass
