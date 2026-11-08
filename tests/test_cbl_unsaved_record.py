"""Issue #927: a shutdown that leaves writes unsaved records them and tells
the operator. Needs no Couchbase Lite libraries (only the record method)."""
import io, json, sys, tempfile, threading, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane.cblstore import CBLiteBackend  # noqa: E402


class TestUnsavedRecord(unittest.TestCase):
    def _b(self, tmp, pending):
        b = object.__new__(CBLiteBackend)
        b.path = Path(tmp) / "db"
        b._pend_lock = threading.Lock()
        b._pending = {k: (1, {"v": 1}) for k in pending}
        return b

    def test_left_writes_are_recorded_and_announced(self):
        with tempfile.TemporaryDirectory() as tmp:
            b, buf = self._b(tmp, [("entity_states", "e9"), ("sessions", "s1")]), io.StringIO()
            with mock.patch("sys.stderr", buf):
                b._record_unsaved()
            self.assertEqual(b.unsaved_at_close, ["entity_states/e9", "sessions/s1"])
            self.assertIn("UNSAVED", buf.getvalue())
            rec = json.loads((Path(tmp) / "db.unsaved.json").read_text())
            self.assertEqual(rec["count"], 2)
            self.assertEqual(rec["keys"], b.unsaved_at_close)

    def test_a_clean_shutdown_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            b, buf = self._b(tmp, []), io.StringIO()
            with mock.patch("sys.stderr", buf):
                b._record_unsaved()
            self.assertEqual(b.unsaved_at_close, [])
            self.assertEqual(buf.getvalue(), "")
            self.assertFalse((Path(tmp) / "db.unsaved.json").exists())

    def test_lease_cut_record_survives_a_held_lock(self):
        """PR 948: the lease cut records unsaved writes even when a hung drain
        holds the pending lock, in the same (o, d) shape."""
        with tempfile.TemporaryDirectory() as tmp:
            b, buf = self._b(tmp, [("sessions", "s1")]), io.StringIO()
            b._pend_lock.acquire()
            try:
                with mock.patch("sys.stderr", buf):
                    b._record_unsaved(lock_timeout=0.1)
            finally:
                b._pend_lock.release()
            rec = json.loads((Path(tmp) / "db.unsaved.json").read_text())
            self.assertEqual(rec["keys"], ["sessions/s1"])
            self.assertEqual(rec["items"], [{"c": "sessions", "k": "s1", "o": 1, "d": {"v": 1}}])

    def test_batch_in_flight_is_recorded_and_newer_pending_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            b, buf = self._b(tmp, [("sessions", "s1")]), io.StringIO()
            b._inflight = {("sessions", "s1"): (1, {"v": 0}), ("sessions", "s2"): (2, {"v": 2})}
            with mock.patch("sys.stderr", buf):
                b._record_unsaved()
            rec = json.loads((Path(tmp) / "db.unsaved.json").read_text())
            self.assertEqual(rec["keys"], ["sessions/s1", "sessions/s2"])
            self.assertEqual([i["d"] for i in rec["items"]], [{"v": 1}, {"v": 2}])


if __name__ == "__main__":
    unittest.main()
