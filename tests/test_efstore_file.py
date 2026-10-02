"""THE FILE HOME (stage 4, the seat-first flip): the entity field lives
under the seat's OWN folder (<seat>/.membrane/field/) -- events.jsonl (an
append-only last-write-wins log; status changes append too) plus debounced
.json snapshots for the mutable collections. The field SURVIVES RESTARTS
without couchbase; the global tier holds only published entities."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import FileBackend, MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class TestFileBackend(unittest.TestCase):
    def _be(self):
        return FileBackend(tempfile.mkdtemp())

    def test_collections_survive_a_reopen(self):
        """THE RESTART COVENANT (the operator's 'does the local field
        survive restarts? it should'): every collection is readable --
        byte-for-byte semantics -- after the backend is closed and
        re-opened from the same directory."""
        be = self._be()
        be.upsert("events", "e1", {"origin": "o", "seq": 1,
                                   "text": "hello the field"})
        be.upsert_multi("entity_states", {"w:gateway": {
            "eid": "w:gateway", "tier": "live", "n": 3,
            "state": efstore.pack_f16([0.1, 0.9, 0.1])}})
        be.insert_if_absent("sentences", "s1", {"text": "gateway errors"})
        be.cas_update("origin_stats", "o:ostats", lambda cur: {
            **(cur or {}), "seq_last": 7})
        be.flush()
        be.close()
        be2 = FileBackend(be.dir)
        self.assertEqual(be2.get("events", "e1")["text"], "hello the field")
        self.assertEqual(be2.get("sentences", "s1")["text"], "gateway errors")
        self.assertEqual(be2.get("origin_stats", "o:ostats")["seq_last"], 7)
        v = efstore.unpack_f16(be2.get("entity_states", "w:gateway")["state"])
        np.testing.assert_allclose(v, [0.1, 0.9, 0.1], atol=1e-2)
        # the field's ANN reads work off the reopened home
        hits = be2.vector_top_k("entity_states", "state", [0.1, 0.9, 0.1], 2,
                                eq={"tier": "live"})
        self.assertEqual(hits[0][0], "w:gateway")

    def test_event_status_changes_are_last_write_wins(self):
        """Events MUTATE after insertion (pending -> applied -> served):
        the jsonl appends the new row and REPLAY restores the last one.
        The log carries every write; a compacted rewrite carries one."""
        be = self._be()
        be.upsert("events", "e1", {"origin": "o", "seq": 1,
                                   "status": "pending"})
        be.upsert("events", "e1", {"origin": "o", "seq": 1,
                                   "status": "applied"})
        self.assertEqual(be.get("events", "e1")["status"], "applied")
        be.close()
        be2 = FileBackend(be.dir)
        self.assertEqual(be2.get("events", "e1")["status"], "applied")
        self.assertEqual(sum(1 for _ in open(be._events_log)), 2)
        for i in range(400):            # the churn the log collects
            be2.upsert("events", "e1", {"origin": "o", "seq": 2,
                                        "status": "served"})
        self.assertEqual(be2.get("events", "e1")["status"], "served")
        be2.flush()                     # 402 lines >> 2 live: compaction
        self.assertEqual(sum(1 for _ in open(be2._events_log)), 1)
        self.assertEqual(be2.get("events", "e1")["status"], "served")

    def test_debounce_defers_the_snapshot(self):
        """The debounce: mutations inside the window stay RAM-only (a
        hot process does not rewrite five files a second); the NEXT
        mutation past the window flushes; flush() always does."""
        be = self._be()
        be._last_flush = time.time()
        be.upsert("entity_states", "w:a", {"eid": "w:a"})
        self.assertIn("entity_states", be._dirty)     # dirty, not written
        self.assertFalse((be.dir / "entity_states.json").exists())
        be._last_flush = time.time() - 10.0           # the window passed
        be.upsert("entity_deltas", "d1", {"eid": "w:a", "seq": 2})
        self.assertTrue((be.dir / "entity_deltas.json").exists())
        self.assertNotIn("entity_states", be._dirty)  # the flush took both

    def test_delete_is_persisted(self):
        be = self._be()
        be.upsert("entity_states", "w:a", {"eid": "w:a"})
        be.flush()
        be.delete("entity_states", "w:a")
        be.close()
        self.assertIsNone(FileBackend(be.dir).get("entity_states", "w:a"))

    def test_torn_last_line_is_survivable(self):
        """A kill mid-append (the crash the jsonl guards against): the
        torn final line is skipped, every earlier event loads."""
        be = self._be()
        be.upsert("events", "e1", {"origin": "o", "seq": 1})
        with open(be._events_log, "a") as fh:
            fh.write('{"k": "e2", "d": {"seq": 2, "te')
        be.close()
        be2 = FileBackend(be.dir)
        self.assertIsNotNone(be2.get("events", "e1"))
        self.assertIsNone(be2.get("events", "e2"))

    def test_entityfield_lives_on_it_and_restarts_whole(self):
        """The REAL flow: a field on the file home writes a turn,
        processes it, closes; a new EntityField over the re-opened home
        continues the seq (origin_stats persisted) and sees the state."""
        with tempfile.TemporaryDirectory() as d:
            be = FileBackend(os.path.join(d, "field-home"))
            f = EF.EntityField(be, FakeEmbedder(), "seat@host")
            f.write_events([ST.Event(stream="discourse", role="user",
                                     text="the gateway keeps timing out",
                                     ts=10.0)], session="s")
            f.process()
            n0 = f._ostats["seq_last"]
            f.flush()
            be.close()
            be2 = FileBackend(os.path.join(d, "field-home"))
            f2 = EF.EntityField(be2, FakeEmbedder(), "seat@host")
            self.assertEqual(f2._ostats["seq_last"], n0)
            self.assertIsNotNone(f2.get("w:gateway"))


class TestOpenBackendRouting(unittest.TestCase):
    """THE FLIP: the field's default home is the FILE under the seat's
    own folder; couchbase only on MEMBRANE_FIELD_CLOUD=1; a down cluster
    degrades to FILE (never to RAM, which forgets)."""

    def test_default_is_the_file_home(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "rag-field.json")
            with mock.patch("membrane.substrate._is_lab_field_path",
                            return_value=False), \
                    mock.patch("membrane.substrate.cb_open",
                               side_effect=RuntimeError("must not be called")):
                be = efstore.open_backend(fp)
        self.assertIsInstance(be, FileBackend)
        self.assertEqual(be.dir, (Path(d) / ".membrane" / "field").resolve())

    def test_cloud_flag_opts_back_in_and_degrades_to_file(self):
        from _ef_fakes import FakeCluster
        from membrane.efstore import CouchbaseBackend
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "rag-field.json")
            with mock.patch("membrane.substrate._is_lab_field_path",
                            return_value=False), \
                    mock.patch.dict(os.environ,
                                    {"MEMBRANE_FIELD_CLOUD": "1"}), \
                    mock.patch("membrane.substrate.cb_open",
                               return_value=(FakeCluster(), "bkt")):
                be = efstore.open_backend(fp)
            self.assertIsInstance(be, CouchbaseBackend)
            with mock.patch("membrane.substrate._is_lab_field_path",
                            return_value=False), \
                    mock.patch.dict(os.environ,
                                    {"MEMBRANE_FIELD_CLOUD": "1"}), \
                    mock.patch("membrane.substrate.cb_open",
                               side_effect=RuntimeError("down")):
                be = efstore.open_backend(fp)
            self.assertIsInstance(be, FileBackend)  # FILE, never RAM

    def test_the_ram_guard_still_wins(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_EFIELD_STORE": "mem",
                                          "MEMBRANE_FIELD_CLOUD": "1"}):
            self.assertIsInstance(efstore.open_backend(
                os.path.join(tempfile.mkdtemp(), "rag-field.json")),
                MemBackend)


if __name__ == "__main__":
    unittest.main()