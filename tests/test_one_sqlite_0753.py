"""One SQLite per process (0.7.53, data safety). The native field engine used to open the seat's own field database
with its own bundled copy of SQLite (rusqlite "bundled") while the seat's Python opened the same WAL file with
CPython's sqlite3. Two SQLite copies in one process on one WAL database: POSIX locks are per process, so a close in
one copy drops the other's locks and the -shm/-wal can be truncated under the other's mmap -> SIGBUS in walFindFrame
(Memo, 2026-11-11, twice) or "database disk image is malformed". Now the engine's sql() runs through the seat's own
sqlite3 (membrane/sqlread.py); the extension carries no SQLite at all."""
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")


def _bundles_sqlite() -> bool:
    return b"SQLite format 3" in Path(MV.__file__).read_bytes()   # a literal of SQLite's source: present iff compiled in


def test_the_extension_carries_no_sqlite_of_its_own():
    assert not _bundles_sqlite(), "membrane_vm compiles in its own SQLite: two copies in one process (see this file)"


class _Host:
    def resolve(self, s): return None
    def resolve_exact(self, s): return None
    def dispatch(self, e, m): return None
    def vel(self, e, n, t): return False
    def align(self, e, t): return False
    def live(self, e, p): return None


def _db(tmp_path):
    p = tmp_path / "field.sqlite3"
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE c_events (k TEXT PRIMARY KEY, body TEXT NOT NULL)")
    c.execute("INSERT INTO c_events VALUES ('e1', ?)", (json.dumps({"d": {"text": "hello"}}),))
    c.commit()
    c.close()
    return str(p)


def test_sql_runs_through_the_seats_own_sqlite(tmp_path, monkeypatch):
    from membrane import sqlread
    seen = []
    real = sqlread.Reader.run

    def spy(self, token, sql, args_json):
        seen.append(sql)
        return real(self, token, sql, args_json)
    monkeypatch.setattr(sqlread.Reader, "run", spy)
    f = MV.Fields(_Host())
    f.set_store(_db(tmp_path))
    f.set_pack("t", "PACK t 1\nChat.n getter: FOR r IN (sql(\"SELECT count(*) AS n FROM c_events\") or []): prop(r, 'n')\n")
    f.load("Chat:a", "Chat", "{}")
    f.rebuild(1.0)
    for _ in range(50):
        if not f.sql_pending():
            break
        f.sql_wait(0.2)
    f.settle(5.0)
    assert json.loads(f.get("Chat:a"))["n"] == [1]
    assert seen and "count(*)" in seen[0]


def test_reader_matches_the_old_driver_rules(tmp_path):
    from membrane import sqlread
    r = sqlread.Reader(_db(tmp_path))
    with pytest.raises(sqlread.SqlError, match="only reads"):
        r.run(1, "DELETE FROM c_events", "[]")
    with pytest.raises(sqlread.SqlError, match="only reads"):
        r.run(2, "ATTACH DATABASE ':memory:' AS x", "[]")
    with pytest.raises(sqlread.SqlError, match="takes 1 argument"):
        r.run(3, "SELECT * FROM c_events WHERE k = ?", "[]")
    rows = json.loads(r.run(4, "SELECT k, 1.5 AS x, NULL AS z, ? AS b, ? AS j FROM c_events", '[true, {"a": 1}]'))
    assert rows == [{"k": "e1", "x": 1.5, "z": None, "b": 1, "j": '{"a": 1}'}]
    with pytest.raises(sqlread.SqlError):
        sqlread.Reader(str(tmp_path / "missing" / "nope.sqlite3"))


def test_the_compile_check_refuses_a_bad_query_before_it_runs(tmp_path):
    """The synchronous path (tools, tests): a write, a missing table or the wrong argument count, refused unrun."""
    from membrane import sqlread
    r = sqlread.Reader(_db(tmp_path))
    with pytest.raises(sqlread.SqlError, match="no such table"):
        r.check("SELECT * FROM nope", 0)
    with pytest.raises(sqlread.SqlError, match="only reads"):
        r.check("DELETE FROM c_events", 0)
    with pytest.raises(sqlread.SqlError, match="takes 1 argument"):
        r.check("SELECT * FROM c_events WHERE k = ?", 0)
    r.check("SELECT * FROM c_events WHERE k = ?", 1)


def test_an_interrupt_always_stops_its_query_whenever_it_lands(tmp_path):
    """Before the run, at its start (SQLite drops an interrupt while no statement runs) or mid-query: it stops."""
    import threading
    import time
    from membrane import sqlread
    r = sqlread.Reader(_db(tmp_path))
    forever = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    r.interrupt(1)                                 # before it runs
    with pytest.raises(sqlread.SqlError, match="interrupted"):
        r.run(1, forever, "[]")
    for i, delay in enumerate((0.0, 0.0, 0.001, 0.01, 0.1)):   # at once (the start window) and mid-query
        token, err = 10 + i, []
        t = threading.Thread(target=lambda: err.append(pytest.raises(sqlread.SqlError, r.run, token, forever, "[]")))
        t.start()
        time.sleep(delay)
        while token not in r._running and t.is_alive():
            time.sleep(0)
        r.interrupt(token)
        t.join(timeout=10)
        assert not t.is_alive(), f"a query interrupted after {delay}s kept running"
        assert "interrupted" in str(err[0].value)
    assert r.run(99, "SELECT 1 AS one", "[]") == '[{"one": 1}]'   # the pooled connection is not left interrupted


STRESS = textwrap.dedent("""
    import json, sqlite3, sys, threading, time
    from membrane import membrane_vm as MV
    db = sys.argv[1]
    class H:
        def resolve(self, s): return None
        def resolve_exact(self, s): return None
        def dispatch(self, e, m): return None
        def vel(self, e, n, t): return False
        def align(self, e, t): return False
        def live(self, e, p): return None
    w = sqlite3.connect(db, check_same_thread=False, isolation_level=None)
    w.execute("PRAGMA journal_mode=WAL")
    stop = threading.Event()
    def writer():
        i = 0
        while not stop.is_set():
            w.execute("INSERT OR REPLACE INTO c_events VALUES (?, ?)", (f"k{i % 500}", json.dumps({"d": {"text": "x" * 200}})))
            i += 1
            if i % 50 == 0:
                w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c = sqlite3.connect(db); c.execute("SELECT count(*) FROM c_events").fetchone(); c.close()
    t = threading.Thread(target=writer); t.start()
    f = MV.Fields(H())
    f.set_store(db)
    f.set_pack("t", "PACK t 1\\nChat.n getter: FOR r IN (sql(\\"SELECT count(*) AS n, sum(length(body)) AS s FROM c_events WHERE k > ?\\", this.q) or []): prop(r, 'n')\\n")
    f.load("Chat:a", "Chat", "{}")
    f.rebuild(1.0)
    t0 = time.time(); n = 0
    while time.time() - t0 < 6:
        n += 1
        f.write("Chat:a", "q", json.dumps(f"k{n % 300}"), 2.0 + n)
        f.sql_wait(0.05)
        f.settle(2.0 + n)
    stop.set(); t.join()
    w.close()
    print(sqlite3.connect(db).execute("PRAGMA quick_check").fetchone()[0], n)
""")


def test_native_reads_with_python_writes_and_checkpoints_never_crash(tmp_path):
    if _bundles_sqlite():                          # the old binary would SIGBUS here (and pop a crash dialog); the
        pytest.skip("the extension still bundles SQLite (the test above fails red)")
    db = _db(tmp_path)
    out = subprocess.run([sys.executable, "-c", STRESS, db], capture_output=True, text=True, timeout=120,
                         cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split()[0] == "ok", out.stdout
