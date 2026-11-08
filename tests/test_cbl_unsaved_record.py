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
        b._pending = {k: () for k in pending}
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


if __name__ == "__main__":
    unittest.main()
