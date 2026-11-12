"""One SQLite per process (0.7.53, #1111). Two SQLite copies in one process on one WAL database corrupt it: POSIX locks
are per process, so a close in one copy drops the other's locks and the -shm/-wal can be truncated under the other's
mmap -> SIGBUS in walFindFrame (Memo, 2026-11-11, twice) or "database disk image is malformed". The field database
is owned by the native engine: its store (`membrane_vm.Db`) writes, its `sql()` reads (`native/src/sqlexec.rs`), on
the one SQLite compiled into the extension. Python's `sqlite3` is not used for the field."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")
ROOT = Path(__file__).resolve().parent.parent


class _Host:
    def resolve(self, s): return None
    def resolve_exact(self, s): return None
    def dispatch(self, e, m): return None
    def vel(self, e, n, t): return False
    def align(self, e, t): return False
    def live(self, e, p): return None


def _db(tmp_path):
    d = MV.Db("field", str(tmp_path))
    d.coll("events")
    d.put_json("events", "e1", json.dumps({"d": {"text": "hello"}}))
    return d


def test_sql_reads_run_on_the_native_store_without_pythons_sqlite(tmp_path, monkeypatch):
    import sqlite3

    def refuse(*a, **k):
        raise AssertionError("the field database was opened with Python's sqlite3")
    monkeypatch.setattr(sqlite3, "connect", refuse)
    db = _db(tmp_path)
    f = MV.Fields(_Host())
    f.set_store(db.file)
    f.set_pack("t", "PACK t 1\nChat.n getter: FOR r IN (sql(\"SELECT count(*) AS n FROM c_events\") or []): prop(r, 'n')\n")
    f.load("Chat:a", "Chat", "{}")
    f.rebuild(1.0)
    for _ in range(50):
        if not f.sql_pending():
            break
        f.sql_wait(0.2)
    f.settle(5.0)
    assert json.loads(f.get("Chat:a"))["n"] == [1]


def test_the_seat_carries_no_python_reader_of_the_field():
    assert not (ROOT / "membrane" / "sqlread.py").exists(), "the engine's sql() reads natively (sqlexec.rs)"


STRESS = textwrap.dedent("""
    import json, sys, threading, time
    from membrane import membrane_vm as MV
    class H:
        def resolve(self, s): return None
        def resolve_exact(self, s): return None
        def dispatch(self, e, m): return None
        def vel(self, e, n, t): return False
        def align(self, e, t): return False
        def live(self, e, p): return None
    db = MV.Db("field", sys.argv[1])
    db.coll("events")
    stop = threading.Event()
    def writer():
        i = 0
        while not stop.is_set():
            db.put_json("events", f"k{i % 500}", json.dumps({"d": {"text": "x" * 200}}))
            i += 1
    t = threading.Thread(target=writer); t.start()
    f = MV.Fields(H())
    f.set_store(db.file)
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
    print("ok", db.count("events"), n)
""")


def test_native_reads_with_native_writes_never_crash(tmp_path):
    out = subprocess.run([sys.executable, "-c", STRESS, str(tmp_path)], capture_output=True, text=True, timeout=120,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ok" in out.stdout, out.stdout
