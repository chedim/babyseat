"""The store driver through the Python binding (the operator 2026-11-10: thread-safe and non-blocking): `sql(...)`
reads the seat's SQLite store from a pack, and the engine never holds the GIL while it works or waits."""
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")


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
    c.executemany("INSERT INTO c_events VALUES (?, ?)", [
        ("e1", json.dumps({"o": 1, "d": {"session": "s1", "text": "hello"}})),
        ("e2", json.dumps({"o": 2, "d": {"session": "s1", "text": "world"}}))])
    c.commit()
    return str(p)


PACK = """PACK t 1
Chat.turns getter: FOR r IN (sql("SELECT json_extract(body, '$.d.text') AS t FROM c_events WHERE json_extract(body, '$.d.session') = ? ORDER BY k", this.session) or []): prop(r, 't')
Chat.spin getter: FOR r IN (sql("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000000) SELECT count(*) AS c FROM n") if this.spin_now else []): prop(r, 'c')
"""


def _field(tmp_path):
    f = MV.Fields(_Host())
    f.set_store(_db(tmp_path))
    f.set_pack("t", PACK)
    f.load("Chat:a", "Chat", "{}")
    f.rebuild(1.0)
    return f


def _arrive(f, now=5.0):
    t = time.time()
    while f.sql_pending() and time.time() - t < 10:
        f.sql_wait(0.2)
    f.settle(now)


def test_a_pack_reads_the_store_through_sql(tmp_path):
    f = _field(tmp_path)
    f.write("Chat:a", "session", '"s1"', 2.0)
    _arrive(f)
    assert json.loads(f.get("Chat:a"))["turns"] == ["hello", "world"]


def test_the_engine_never_holds_the_gil_while_it_works(tmp_path):
    f = _field(tmp_path)
    ticks, stop = [0], threading.Event()

    def spin():                                    # a Python thread: it runs only while nobody holds the GIL
        while not stop.is_set():
            ticks[0] += 1
    t = threading.Thread(target=spin)
    t.start()
    time.sleep(0.05)
    before = ticks[0]
    t0 = time.time()
    f.write("Chat:a", "spin_now", "true", 3.0)    # a long query (3 million rows counted in SQLite): asked, not waited
    assert time.time() - t0 < 0.05                 # the write came back at once
    _arrive(f)                                     # waiting for the rows: no engine lock, no GIL
    took = time.time() - t0
    during = ticks[0] - before
    stop.set()
    t.join()
    assert json.loads(f.get("Chat:a"))["spin"] == [3000000]
    assert took > 0.05 and during > 1000, (took, during)   # the other thread kept running the whole time


def test_threads_waiting_for_the_engine_never_deadlock(tmp_path):
    f = _field(tmp_path)
    done = []

    def writer():
        f.write("Chat:a", "spin_now", "true", 3.0)
        done.append("w")

    def reader():
        for _ in range(50):
            f.get("Chat:a")
        done.append("r")
    ts = [threading.Thread(target=writer), threading.Thread(target=reader), threading.Thread(target=reader)]
    for x in ts:
        x.start()
    for x in ts:
        x.join(timeout=10)
    assert sorted(done) == ["r", "r", "w"], done


def test_a_runaway_query_never_hangs_the_field(tmp_path):
    f = _field(tmp_path)
    f.set_pack("r", 'PACK r 1\nIMPORT t 1\nChat.forever getter: sql(this.q) if this.q else None\n')
    f.write("Chat:a", "q", json.dumps("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"), 3.0)
    t = time.time()
    f.write("Chat:a", "session", '"s1"', 4.0)       # the field goes on
    for _ in range(50):
        f.get("Chat:a")
    assert time.time() - t < 1.0
    assert f.sql_pending() >= 1
    f.sql_interrupt()                               # shutdown stops it
    for _ in range(50):
        f.settle(5.0)                               # its outcome (interrupted) lands on the next round
        if not f.sql_pending():
            break
        time.sleep(0.02)
    assert f.sql_pending() == 0


def test_the_seat_seeds_the_store_entity_and_its_getters_read_the_store(tmp_path, monkeypatch):
    import types
    from membrane import fields_live as FL
    from membrane import watch_agents as WA
    from membrane.entities import EntityStore
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    db = _db(tmp_path)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                efield=types.SimpleNamespace(b=types.SimpleNamespace(db=types.SimpleNamespace(file=db))))
    est._srv = srv
    live = FL.start(srv)
    try:
        assert FL.STORE_EID in est.docs
        t = time.time()
        while time.time() - t < 10 and (est.docs[FL.STORE_EID].properties or {}).get("events") is None:
            time.sleep(0.05)
        p = est.docs[FL.STORE_EID].properties
        assert p["events"] == 2 and "c_events" in p["tables"], p
    finally:
        FL.stop(srv)
