"""THE HOT ROWS FROM THE WARM SNAPSHOT (#1115): a seat on a big field read every hot row back from its
database at each boot (1,004,349 rows in 25-74 s on Memo). The clean stop's warm snapshot now keeps
the rows too (marshal: JSON-shaped values, no code runs on load), and the next boot takes them from
it instead -- only when it provably matches the database: the same format and Python, the database
file's proof (`_warm_proof`: size, mtime, WAL bytes) unchanged since the save, and the same row count
per collection. Anything else rebuilds exactly as before: a write after the save, a damaged file, an
older format, a count that disagrees, writes still pending at the save.
"""
import json
import marshal
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efstore  # noqa: E402
from membrane.sqlstore import SQLiteBackend  # noqa: E402

HOT = ("events", "entity_states", "relations")


def _fill(be, n=40):
    for i in range(n):
        be.upsert("events", f"e{i}", {"origin": "o", "seq": i, "text": f"event {i}", "sids": [f"s{i}"]})
        be.upsert("entity_states", f"o|x{i}", {"origin": "o", "eid": f"x{i}", "mass": i, "tier": "live"})
    be.upsert("relations", "r1", {"origin": "o", "s": "x1", "t": "x2", "rel": "uses"})


def _rows(be):
    return {c: dict(be.colls[c]) for c in HOT}, {c: dict(be._ord[c]) for c in HOT}, dict(be._next)


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rows-snap-")
        self.path = Path(self.dir) / "field.sqlite"
        be = SQLiteBackend(self.path)
        _fill(be)
        be.close()
        self.want = _rows(be)
        self.be = be

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _warm(self):
        return self.path / efstore.WARM_DIR

    def _open_no_db_read(self):
        """An open that fails if it reads the hot rows from the database."""
        real = SQLiteBackend._load_hot_db

        def guard(be):
            raise AssertionError("the hot rows were read from the database")
        with mock.patch.object(SQLiteBackend, "_load_hot_db", guard):
            be = SQLiteBackend(self.path)
        self.assertIsNotNone(real)
        return be

    def _open(self):
        return SQLiteBackend(self.path)


class TheRoundTrip(_Case):
    def test_the_rows_come_back_from_the_snapshot(self):
        man = self.be.save_warm()
        self.assertTrue(man.get("rows"), man)
        be = self._open_no_db_read()
        try:
            self.assertEqual(_rows(be), self.want)
            self.assertFalse(self._warm().exists())          # read once
        finally:
            be.close()

    def test_a_new_row_after_the_load_gets_the_next_ordinal(self):
        self.be.save_warm()
        be = self._open_no_db_read()
        try:
            be.upsert("events", "new", {"origin": "o", "seq": 99})
            self.assertEqual(be._ord["events"]["new"], self.want[2]["events"])
        finally:
            be.close()
        be = self._open()
        try:
            self.assertIn("new", be.colls["events"])
        finally:
            be.close()

    def test_a_read_only_open_neither_uses_nor_consumes_it(self):
        self.be.save_warm()
        ro = SQLiteBackend(self.path, readonly=True)
        self.assertEqual(_rows(ro)[0], self.want[0])
        self.assertTrue((self._warm() / "manifest.json").exists())


class TheRebuilds(_Case):
    def _rebuilt(self):
        be = self._open()
        try:
            got = _rows(be)
        finally:
            be.close()
        self.assertFalse(self._warm().exists())
        return got

    def test_a_write_after_the_save_rebuilds(self):
        self.be.save_warm()
        keep = Path(self.dir) / "kept"
        shutil.copytree(self._warm(), keep)
        be = self._open()
        be.upsert("events", "late", {"origin": "o", "seq": 500})
        be.close()
        shutil.copytree(keep, self._warm())                   # the old snapshot back
        got = self._rebuilt()
        self.assertIn("late", got[0]["events"])

    def test_a_damaged_file_rebuilds(self):
        man = self.be.save_warm()
        f = self._warm() / man["rows"]["file"]
        f.write_bytes(f.read_bytes()[: f.stat().st_size // 2])
        self.assertEqual(self._rebuilt(), self.want)

    def test_garbage_of_the_right_length_rebuilds(self):
        man = self.be.save_warm()
        f = self._warm() / man["rows"]["file"]
        f.write_bytes(marshal.dumps({"events": "not rows"}))
        self.assertEqual(self._rebuilt(), self.want)

    def test_an_older_format_rebuilds(self):
        self.be.save_warm()
        m = self._warm() / "manifest.json"
        doc = json.loads(m.read_text())
        doc["rows"]["version"] = -1
        m.write_text(json.dumps(doc))
        self.assertEqual(self._rebuilt(), self.want)

    def test_another_python_rebuilds(self):
        self.be.save_warm()
        m = self._warm() / "manifest.json"
        doc = json.loads(m.read_text())
        doc["rows"]["python"] = [2, 7]
        m.write_text(json.dumps(doc))
        self.assertEqual(self._rebuilt(), self.want)

    def test_a_count_that_disagrees_rebuilds_even_with_a_matching_proof(self):
        man = self.be.save_warm()
        keep = Path(self.dir) / "kept"
        shutil.copytree(self._warm(), keep)
        be = self._open()
        be.delete("events", "e3")
        be.close()
        shutil.copytree(keep, self._warm())
        with mock.patch.object(SQLiteBackend, "_warm_proof", lambda s, c: man["rows"]["proof"]):
            be = self._open()
        try:
            self.assertNotIn("e3", be.colls["events"])
        finally:
            be.close()

    def test_writes_still_pending_at_the_save_keep_no_rows(self):
        be = self._open()
        with mock.patch.object(be, "drain", lambda: 0):       # the write-behind holds it
            be._pending[("events", "pending")] = (999, {"origin": "o"})
            man = be.save_warm()
            be._pending.clear()
        be.close()
        self.assertFalse(man.get("rows"))


class TheTeardownClosesTheStoreFirst(unittest.TestCase):
    """The proof is the database file's stat: the field's store closes (its write-behind drained,
    the WAL checkpointed away) after the entity layer wrote into it and before the snapshot, so no
    later close or write moves the file under a saved proof. Before this the store was never closed
    in the teardown: its file moved at process exit and no snapshot could ever match."""

    def test_the_field_store_closes_after_the_entities_and_before_the_snapshot(self):
        src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
        fin = src[src.index("# THE TEARDOWN'S BOUND"):]
        ents = fin.index('getattr(getattr(srv, "entity_store", None), "close", None)')
        field = fin.index('getattr(getattr(getattr(srv, "efield", None), "b", None), "close", None)')
        self.assertLess(ents, field)
        self.assertLess(field, fin.index("save_warm(srv)"))


class TheMeasurement(unittest.TestCase):
    """The load from the snapshot vs the rebuild from the database, on a synthetic store
    (MEMBRANE_SNAPSHOT_BENCH_ROWS rows; 30,000 in the suite)."""

    def test_the_snapshot_loads_faster_than_the_rebuild(self):
        n = int(os.environ.get("MEMBRANE_SNAPSHOT_BENCH_ROWS") or 30000)
        d = tempfile.mkdtemp(prefix="rows-bench-")
        try:
            path = Path(d) / "field.sqlite"
            be = SQLiteBackend(path)
            for i in range(n):
                be.upsert("entity_deltas", f"d{i}", {"origin": "seat@host", "eid": f"x{i % 977}",
                                                     "seq": i, "sidx": i % 7, "event": f"e{i // 3}",
                                                     "w": 0.25 + (i % 13) / 13, "kind": "mention",
                                                     "ts": 1762800000.0 + i})
            be.close()
            t = time.perf_counter()
            be = SQLiteBackend(path)                          # no snapshot: the rebuild
            rebuild = time.perf_counter() - t
            be.close()
            t = time.perf_counter()
            be.save_warm()
            save = time.perf_counter() - t
            t = time.perf_counter()
            be2 = SQLiteBackend(path)
            snap = time.perf_counter() - t
            try:
                self.assertEqual(len(be2.colls["entity_deltas"]), n)
            finally:
                be2.close()
            print(f"\n[rows snapshot] {n} rows: rebuild {rebuild:.2f}s, snapshot load {snap:.2f}s, "
                  f"save {save:.2f}s", file=sys.stderr)
            self.assertLess(snap, rebuild)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
