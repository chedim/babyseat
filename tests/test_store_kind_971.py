"""#971 (the operator 2026-11-08): SQLite as a parallel store and the default for a new seat; a new seat's
first start asks which store on a terminal; `membrane store migrate --to ...` moves a seat between them.

The store's own contract is the Couchbase Lite suites, run against SQLite with MEMBRANE_TEST_STORE=sqlite
(tests/conftest.py). These tests cover choosing and moving."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane import storekind as SK  # noqa: E402
from membrane.sqlstore import SQLiteBackend  # noqa: E402

HAVE_CBL = cblite.lib_paths() is not None


def _seat():
    d = Path(tempfile.mkdtemp()) / "seat" / ".membrane"
    d.mkdir(parents=True)
    return str(d / "field.json")


class _env:
    """The environment without a store override, and the test's temp seat treated as a real seat (a temp
    path is a lab field to open_backend: RAM)."""

    def __init__(self, **kw):
        e = {k: v for k, v in os.environ.items() if k != "MEMBRANE_EFIELD_STORE"}
        e.update(kw)
        self.ps = [mock.patch.dict(os.environ, e, clear=True),
                   mock.patch("membrane.substrate._is_lab_field_path", return_value=False)]

    def __enter__(self):
        for p in self.ps:
            p.__enter__()
        return self

    def __exit__(self, *a):
        for p in reversed(self.ps):
            p.__exit__(*a)
        return False


class Choosing(unittest.TestCase):
    def test_a_new_seat_opens_sqlite_by_default(self):
        f = _seat()
        with _env():
            be = efstore.open_backend(f)
        try:
            self.assertIsInstance(be, SQLiteBackend)
            self.assertTrue((Path(f).parent / "field.sqlite" / "field.sqlite3").exists())
        finally:
            be.close()

    def test_the_first_start_asks_on_a_terminal_and_keeps_the_answer(self):
        f = _seat()
        out = io.StringIO()
        with _env():
            k = SK.ensure_chosen(f, interactive=True, ask=lambda prompt: "2", out=out)
            self.assertEqual(k, "cbl")
            self.assertIn("Which one?", out.getvalue())
            self.assertEqual(SK.chosen(Path(f).parent), "cbl")
            # asked once: a second start never asks again
            again = SK.ensure_chosen(f, interactive=True, ask=lambda prompt: self.fail("asked twice"), out=out)
            self.assertEqual(again, "cbl")

    def test_without_a_terminal_a_new_seat_takes_the_default_without_asking(self):
        f = _seat()
        with _env():
            k = SK.ensure_chosen(f, interactive=False, ask=lambda p: self.fail("asked"), out=io.StringIO())
        self.assertEqual(k, "sqlite")
        self.assertEqual(SK.chosen(Path(f).parent), "sqlite")

    def test_an_existing_couchbase_lite_seat_keeps_its_store_and_is_never_asked(self):
        f = _seat()
        (Path(f).parent / "field.cblite").mkdir()
        with _env():
            self.assertEqual(SK.resolve(f), "cbl")
            self.assertEqual(SK.ensure_chosen(f, interactive=True, ask=lambda p: self.fail("asked"),
                                              out=io.StringIO()), "cbl")

    def test_the_environment_overrides_the_choice(self):
        f = _seat()
        SK.choose(Path(f).parent, "cbl")
        with _env(MEMBRANE_EFIELD_STORE="sqlite"):
            self.assertEqual(SK.resolve(f), "sqlite")


def _fill(be):
    v = np.random.default_rng(3).standard_normal(1024).astype(np.float32)
    v /= np.linalg.norm(v)
    import base64
    packed = "f16:" + base64.b64encode(v.astype(np.float16).tobytes()).decode()
    be.upsert("entity_states", "e1", {"origin": "o", "mass": 2.0})
    be.upsert("sentences", "s1", {"text": "the boat is blue", "vec": packed})
    be.db.coll("sessions")
    be.db.put_json("sessions", "ses_1", json.dumps({"s": {"id": "ses_1", "title": "welcome back"}}))
    be.db.coll("files")
    be.db.put_json("files", "file:watches.json", json.dumps({"d": {"next": 7}}))
    be.flush()
    return v


@unittest.skipUnless(HAVE_CBL, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class Migrating(unittest.TestCase):
    def _round(self, first, second):
        f = _seat()
        SK.choose(Path(f).parent, first)
        with _env():
            be = efstore.open_backend(f)
            v = _fill(be)
            be.close()
            rep = SK.migrate(f, second, out=io.StringIO())
            self.assertEqual(rep["from"], first)
            self.assertEqual(SK.resolve(f), second)
            self.assertTrue(Path(rep["backup"]).exists())          # the source stays, a backup
            be = efstore.open_backend(f)
        try:
            self.assertEqual(type(be).__name__, "SQLiteBackend" if second == "sqlite" else "CBLiteBackend")
            self.assertEqual(be.get("entity_states", "e1")["mass"], 2.0)
            self.assertEqual(be.get("sentences", "s1")["text"], "the boat is blue")
            self.assertEqual(json.loads(be.db.get_json("sessions", "ses_1"))["s"]["title"], "welcome back")
            self.assertEqual(json.loads(be.db.get_json("files", "file:watches.json"))["d"]["next"], 7)
            be.catch_up()
            top = be.vector_top_k("sentences", "vec", v, 1)
            self.assertEqual(top[0][0], "s1")                       # the vectors came along
        finally:
            be.close()

    def test_sqlite_to_couchbase_lite(self):
        self._round("sqlite", "cbl")

    def test_couchbase_lite_to_sqlite(self):
        self._round("cbl", "sqlite")

    def test_a_running_seat_is_never_migrated(self):
        f = _seat()
        with _env():
            be = efstore.open_backend(f)                            # the live seat holds the writer lock
            try:
                with self.assertRaises(Exception) as cm:
                    SK.migrate(f, "cbl", out=io.StringIO())
                self.assertIn("busy", str(cm.exception))
                self.assertEqual(SK.resolve(f), "sqlite")
                self.assertFalse((Path(f).parent / "field.cblite").exists())   # no half-made target
            finally:
                be.close()

    def test_an_existing_target_is_never_overwritten(self):
        f = _seat()
        with _env():
            efstore.open_backend(f).close()
            (Path(f).parent / "field.cblite").mkdir()
            with self.assertRaises(ValueError):
                SK.migrate(f, "cbl", out=io.StringIO())


class Status(unittest.TestCase):
    def test_status_says_which_store_and_what_is_in_it(self):
        f = _seat()
        with _env():
            be = efstore.open_backend(f)
            be.upsert("entity_states", "e1", {"origin": "o"})
            be.close()
            st = SK.status(f)
        self.assertEqual(st["uses"], "sqlite")
        self.assertEqual(st["stores"]["sqlite"]["collections"]["entity_states"], 1)


class MigrateFinalCheck(unittest.TestCase):
    """The last check compares the new store with the SOURCE's count, not with the copy loop's own tally:
    a source that hands back nothing for a listed row must fail the migration, not pass it."""

    def test_a_source_that_drops_a_row_fails_the_migration(self):
        from membrane import sqlitedb as SDB
        f = _seat()
        SK.choose(Path(f).parent, "sqlite")
        with _env():
            be = efstore.open_backend(f)
            _fill(be)
            be.close()

            class Lossy:
                def __init__(self, db):
                    self._db = db

                def __getattr__(self, n):
                    return getattr(self._db, n)

                def get_json(self, c, k):
                    return None if (c, k) == ("sessions", "ses_1") else self._db.get_json(c, k)

            def fake_open(kind, d):
                db = SDB.Database("field", str(d))
                return Lossy(db) if kind == "sqlite" else db

            with mock.patch.object(SK, "_open", fake_open):
                with self.assertRaises(RuntimeError) as cm:
                    SK.migrate(f, "cbl", out=io.StringIO())
            self.assertIn("sessions", str(cm.exception))
