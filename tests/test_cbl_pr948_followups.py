"""PR 948 review findings (issue #927). No Couchbase Lite libraries needed: a fake db."""
import io, json, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import cblite as CB  # noqa: E402
from membrane import restarts  # noqa: E402
from membrane.cblstore import CBLiteBackend  # noqa: E402


class FakeDb:
    def __init__(self, rows=None, put_exc=None, begin_exc=None):
        self.rows = rows or {}
        self.put_exc, self.begin_exc = put_exc, begin_exc
        self.puts, self.purges = [], []

    def begin(self):
        if self.begin_exc:
            raise self.begin_exc

    def end(self):
        pass

    def put_json(self, c, k, js):
        if self.put_exc:
            raise self.put_exc
        self.puts.append((c, k, js))
        self.rows[(c, k)] = js

    def purge(self, c, k):
        self.purges.append((c, k))

    def get_json(self, c, k):
        return self.rows.get((c, k))


def _b(tmp, db, pending=None):
    b = object.__new__(CBLiteBackend)
    b.path = Path(tmp) / "db"
    b._pend_lock = threading.Lock()
    b._pending = dict(pending or {})
    b._inflight = {}
    b.db = db
    b.unsaved_at_close = []
    return b


class TestOnlyBusyRetries(unittest.TestCase):
    def test_busy_code_16_is_requeued(self):
        tmp = tempfile.mkdtemp()
        b = _b(tmp, FakeDb(put_exc=CB.CBLException("busy", code=16)), {("entity_states", "e1"): (1, {"m": 1})})
        with mock.patch("sys.stderr", io.StringIO()):
            b.drain()
        self.assertIn(("entity_states", "e1"), b._pending)

    def test_a_permanent_failure_stops_retrying_and_is_reported(self):
        tmp = tempfile.mkdtemp()
        b = _b(tmp, FakeDb(put_exc=CB.CBLException("corrupt", code=5)), {("entity_states", "e1"): (1, {"m": 1})})
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            b.drain()
        self.assertEqual(b._pending, {})
        rec = json.loads((Path(tmp) / "db.unsaved.json").read_text())
        self.assertEqual(rec["keys"], ["entity_states/e1"])
        self.assertIn("e1", buf.getvalue())

    def test_a_permanent_begin_failure_is_reported_not_retried(self):
        tmp = tempfile.mkdtemp()
        b = _b(tmp, FakeDb(begin_exc=CB.CBLException("nope", code=5)), {("entity_states", "e1"): (1, {"m": 1})})
        with mock.patch("sys.stderr", io.StringIO()):
            b.drain()
        self.assertEqual(b._pending, {})
        self.assertTrue((Path(tmp) / "db.unsaved.json").exists())


class TestReapplyKeepsNewerDiskValue(unittest.TestCase):
    def test_a_row_saved_after_the_record_is_not_overwritten(self):
        tmp = tempfile.mkdtemp()
        ts = time.time() - 100
        # same insertion ordinal (o is not a version); the disk row was saved after the cut
        disk = json.dumps({"o": 3, "t": ts + 50, "d": {"m": 99}})
        db = FakeDb({("entity_states", "e1"): disk})
        b = _b(tmp, db)
        (Path(tmp) / "db.unsaved.json").write_text(json.dumps(
            {"ts": ts, "count": 1, "keys": ["entity_states/e1"],
             "items": [{"c": "entity_states", "k": "e1", "o": 3, "d": {"m": 2}}]}))
        b._reapply_unsaved()
        self.assertEqual(db.puts, [])
        self.assertEqual(b.recovered_at_boot["stale"], 1)

    def test_a_row_saved_before_the_record_is_replaced(self):
        tmp = tempfile.mkdtemp()
        ts = time.time() - 100
        disk = json.dumps({"o": 3, "t": ts - 50, "d": {"m": 1}})
        db = FakeDb({("entity_states", "e1"): disk})
        b = _b(tmp, db)
        (Path(tmp) / "db.unsaved.json").write_text(json.dumps(
            {"ts": ts, "count": 1, "keys": ["entity_states/e1"],
             "items": [{"c": "entity_states", "k": "e1", "o": 3, "d": {"m": 2}}]}))
        b._reapply_unsaved()
        self.assertEqual(len(db.puts), 1)


class TestFailedReapplyIsRetried(unittest.TestCase):
    def test_an_unapplied_record_is_kept_for_the_next_boot(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "db.unsaved.json"
        p.write_text(json.dumps({"ts": 1, "count": 1, "keys": ["entity_states/e1"],
                                 "items": [{"c": "entity_states", "k": "e1", "o": 3, "d": {"m": 2}}]}))
        text = restarts._unsaved_text(d)
        self.assertTrue(p.exists())
        self.assertFalse((Path(d) / "db.unsaved.reported.json").exists())
        self.assertIn("unsaved", text)

    def test_an_applied_record_is_consumed(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "db.unsaved.json"
        p.write_text(json.dumps({"ts": 1, "count": 1, "keys": ["a/b"], "reapplied": True,
                                 "applied": 1, "stale": 0, "items": []}))
        restarts._unsaved_text(d)
        self.assertFalse(p.exists())
        self.assertTrue((Path(d) / "db.unsaved.reported.json").exists())


if __name__ == "__main__":
    unittest.main()
