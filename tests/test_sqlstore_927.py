"""#927 on SQLite: a failed write is re-queued with backoff; close records what is left (stderr + <db>.unsaved.json)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import sqlitedb as SDB  # noqa: E402
from membrane.sqlstore import SQLiteBackend as B  # noqa: E402


class SqlWrites927(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = str(Path(self.d) / "db")

    def _fail_puts(self, be):
        def boom(*a, **k):
            raise SDB.SQLiteException("database is locked")
        return mock.patch.object(be.db, "put_json", side_effect=boom)

    def test_failed_write_is_requeued_and_backs_off(self):
        be = B(self.p)
        with self._fail_puts(be), contextlib.redirect_stderr(io.StringIO()):
            be.upsert("events", "k", {"text": "x"})
            be.drain()
            self.assertIn(("events", "k"), be._pending)
            self.assertTrue(be._retry_at > 0 and be._fails >= 1)
        be._retry_at = 0
        self.assertEqual(be.drain(), 1)
        self.assertEqual(be._fails, 0)
        be.close()

    def test_close_records_unsaved_to_stderr_and_file(self):
        be = B(self.p)
        err = io.StringIO()
        with self._fail_puts(be), contextlib.redirect_stderr(err):
            be.upsert("events", "k", {"text": "x"})
            be.close()
        self.assertIn("UNSAVED", err.getvalue())
        self.assertIn("events/k", err.getvalue())
        rec = Path(self.p).parent / (Path(self.p).name + ".unsaved.json")
        self.assertTrue(rec.exists())
        self.assertEqual(json.loads(rec.read_text())["keys"], ["events/k"])


if __name__ == "__main__":
    unittest.main()
