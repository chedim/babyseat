"""#1111: the SQLite field store moves into the native engine (one SQLite per process).

Two layers:
  * PIN tests run the contract against today's Python `membrane.sqlitedb.Database` (they pass now and
    must keep passing: this is the behaviour the Rust port keeps);
  * PARITY tests run the SAME contract against `membrane_vm.Db(name, directory)` (the native store the
    port adds, same API and same <dir>/<name>.sqlite3 layout) -- red until the port lands;
  * A3: the Python SQLiteBackend must not open a Python sqlite3 connection for the field.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import sqlitedb  # noqa: E402


def make_python(name, directory):
    return sqlitedb.Database(name, directory)


def make_native(name, directory):
    from membrane import membrane_vm as MV
    if not hasattr(MV, "Db"):
        raise AssertionError("membrane_vm has no native `Db` store yet (#1111 A1: store lives in Rust)")
    return MV.Db(name, directory)


class DbContract:
    """The db behaviour the port must keep. Subclasses set `make`."""
    make = None

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = type(self).make("field", self.d)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_layout_is_field_sqlite3_wal_with_c_table(self):
        self.db.put_json("sentences", "a", '{"x": 1}')
        self.db.close()
        f = Path(self.d) / "field.sqlite3"
        self.assertTrue(f.exists())
        cx = sqlite3.connect(str(f))
        try:
            self.assertEqual(cx.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(cx.execute("SELECT k, body FROM c_sentences").fetchall(), [("a", '{"x": 1}')])
        finally:
            cx.close()

    def test_put_get_exists_purge_count(self):
        db = self.db
        self.assertIsNone(db.get_json("c", "k"))
        self.assertFalse(db.exists("c", "k"))
        db.put_json("c", "k", '{"v": 1}')
        db.put_json("c", "k", '{"v": 2}')          # upsert replaces
        self.assertEqual(json.loads(db.get_json("c", "k")), {"v": 2})
        self.assertIsInstance(db.get_json("c", "k"), bytes)
        self.assertTrue(db.exists("c", "k"))
        self.assertEqual(db.count("c"), 1)
        db.purge("c", "k")
        self.assertIsNone(db.get_json("c", "k"))
        self.assertEqual(db.count("c"), 0)
        self.assertIsNone(db.get_json("c", ""))
        self.assertFalse(db.exists("c", ""))

    def test_a_document_needs_an_id(self):
        with self.assertRaises(Exception):
            self.db.put_json("c", "", "{}")

    def test_commit_and_rollback(self):
        db = self.db
        db.put_json("c", "seed", "{}")              # collection exists before the txn
        db.begin()
        db.put_json("c", "a", '{"n": 1}')
        db.end(commit=False)
        self.assertFalse(db.exists("c", "a"))
        db.begin()
        db.put_json("c", "a", '{"n": 1}')
        db.end(commit=True)
        self.assertTrue(db.exists("c", "a"))

    def test_nested_transactions_commit_only_at_the_outermost_end(self):
        db = self.db
        db.put_json("c", "seed", "{}")
        db.begin()
        db.begin()
        db.put_json("c", "a", '{"n": 1}')
        db.end(commit=True)                         # inner: nothing committed yet
        db.end(commit=False)                        # outer decides: rollback
        self.assertFalse(db.exists("c", "a"))

    def test_value_index_and_has_index(self):
        db = self.db
        self.assertFalse(db.has_index("c", "by_name"))
        db.value_index("c", "by_name", "name")
        self.assertTrue(db.has_index("c", "by_name"))
        db.value_index("c", "by_name", "name")      # idempotent
        with self.assertRaises(Exception):
            db.value_index("c", "bad name; drop", "name")

    def test_vector_index_is_recorded_only(self):
        db = self.db
        self.assertFalse(db.has_index("c", "vec_ix"))
        db.vector_index("c", "vec_ix", "vec", 1024, 16)
        self.assertTrue(db.has_index("c", "vec_ix"))
        self.assertEqual(db.update_lazy("c", "vec_ix", lambda s: None), 0)

    def test_query_projection_order_valued_and_missing(self):
        db = self.db
        db.put_json("c", "b", '{"n": 2, "o": {"p": "x"}, "z": null}')
        db.put_json("c", "a", '{"n": 1}')
        db.put_json("c", "c", '{"n": 3, "o": {"p": "y"}}')
        rows = db.query("SELECT META().id AS id, n FROM membrane.c ORDER BY n DESC")
        self.assertEqual(rows, [{"id": "c", "n": 3}, {"id": "b", "n": 2}, {"id": "a", "n": 1}])
        rows = db.query("SELECT META().id AS id, o.p AS p FROM membrane.c WHERE o.p IS VALUED ORDER BY n ASC")
        self.assertEqual(rows, [{"id": "b", "p": "x"}, {"id": "c", "p": "y"}])
        rows = db.query("SELECT META().id AS id, z FROM membrane.c ORDER BY n ASC")
        self.assertEqual(rows[0], {"id": "a"})                  # a missing path is omitted
        self.assertEqual(rows[1], {"id": "b", "z": None})       # an explicit null is kept
        self.assertEqual(list(db.iter_query("SELECT META().id AS id FROM membrane.c"))[0], {"id": "b"})

    def test_a_query_never_creates_a_collection(self):
        with self.assertRaises(Exception):
            self.db.query("SELECT META().id AS id FROM membrane.nope")
        self.db.close()
        cx = sqlite3.connect(str(Path(self.d) / "field.sqlite3"))
        try:
            self.assertIsNone(cx.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'c_nope'").fetchone())
        finally:
            cx.close()

    def test_close_commits_open_txn_and_truncates_the_wal(self):
        db = self.db
        db.begin()
        db.put_json("c", "a", '{"n": 1}')
        db.close()
        wal = Path(self.d) / "field.sqlite3-wal"
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)
        cx = sqlite3.connect(str(Path(self.d) / "field.sqlite3"))
        try:
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM c_c").fetchone()[0], 1)
        finally:
            cx.close()

    def test_reopen_sees_the_data(self):
        self.db.put_json("c", "a", '{"n": 1}')
        self.db.close()
        again = type(self).make("field", self.d)
        try:
            self.assertEqual(json.loads(again.get_json("c", "a")), {"n": 1})
        finally:
            again.close()


class PythonDbPins(DbContract, unittest.TestCase):
    """Today's implementation: green, and must stay green through the port."""
    make = staticmethod(make_python)


class NativeDbParity(DbContract, unittest.TestCase):
    """The port: the native store must satisfy the identical contract."""
    make = staticmethod(make_native)


class OneSqlitePerProcess(unittest.TestCase):
    def test_a3_sqlitebackend_opens_no_python_sqlite_connection(self):
        """A3: with the store native, the Python sqlite3 module is not used for the field."""
        from membrane.sqlstore import SQLiteBackend
        d = tempfile.mkdtemp()
        with mock.patch.object(sqlite3, "connect",
                               side_effect=AssertionError("python sqlite3.connect used for the field")), \
                mock.patch.object(sqlitedb.sqlite3, "connect",
                                  side_effect=AssertionError("python sqlite3.connect used for the field")):
            be = SQLiteBackend(str(Path(d) / "db"))
            try:
                be.upsert("sentences", "a", {"text": "a"})
                be.flush()
            finally:
                be.close()


if __name__ == "__main__":
    unittest.main()
