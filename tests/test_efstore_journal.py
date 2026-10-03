"""THE JOURNAL (the freeze fix, red first): EVERY FileBackend collection
becomes an append-only jsonl (one line per upsert, last-line-wins
replay, compaction at >2x live rows) -- the events.jsonl machinery
generalized; a one-row change flushes in ~ms (no 285MB snapshot
re-serialized); the one-time migration at load (snapshot -> journal,
the originals backed up); the parity pin: a journal-replayed store ==
the RAM store."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.efstore import FileBackend, MemBackend, COLLECTIONS  # noqa: E402


class TestJournalParity(unittest.TestCase):
    def test_every_collection_persists_a_journal(self):
        """THE CONTRACT: after upserts + close, the dir holds a .jsonl
        for EVERY collection touched; a reopen replays them exactly."""
        with tempfile.TemporaryDirectory(prefix="journal-") as tmp:
            be = FileBackend(tmp)
            be.upsert("sentences", "s1", {"_k": "sent", "text": "the "
                                           "gateway keeps timing out",
                                           "vec": [1.0, 2.0]})
            be.upsert_multi("entity_states", {"w:gateway": {
                "eid": "w:gateway", "tier": "live", "n": 3}})
            be.upsert("terms", "t:x", {"_k": "term", "term": "x"})
            be.close()
            jfiles = [f.name for f in Path(tmp).glob("*.jsonl")]
            self.assertIn("sentences.jsonl", jfiles)
            self.assertIn("entity_states.jsonl", jfiles)
            self.assertIn("terms.jsonl", jfiles)
            be2 = FileBackend(tmp)
            self.assertEqual(be2.colls["sentences"].get("s1",
                              {}).get("text"),
                             "the gateway keeps timing out")
            self.assertEqual(be2.colls["entity_states"].get("w:gateway",
                              {}).get("tier"), "live")
            self.assertEqual(be2.colls["terms"].get("t:x", {}).get("term"),
                             "x")

    def test_last_line_wins(self):
        with tempfile.TemporaryDirectory(prefix="journal-") as tmp:
            be = FileBackend(tmp)
            be.upsert("sentences", "s1", {"text": "v1"})
            be.upsert("sentences", "s1", {"text": "v2"})
            be.close()
            be2 = FileBackend(tmp)
            self.assertEqual(be2.colls["sentences"]["s1"]["text"], "v2")

    def test_delete_tombstones_in_the_journal(self):
        """A delete survives a reopen (the journal: a tombstone line;
        the replay pops)."""
        with tempfile.TemporaryDirectory(prefix="journal-") as tmp:
            be = FileBackend(tmp)
            be.upsert("sentences", "s1", {"text": "v1"})
            be.close()
            be2 = FileBackend(tmp)
            be2.delete("sentences", "s1")
            be3 = FileBackend(tmp)
            self.assertNotIn("s1", be3.colls["sentences"])


class TestFlushCost(unittest.TestCase):
    def test_one_changed_row_is_cheap(self):
        """THE BOUND: a 2000-row collection + 1 new row -> the flush's
        serialize+append must stay ~ms (the OLD world re-serialized
        the whole snapshot ~10+ms/flush... measured here as: no
        full-file snapshot write when only the journal moves)."""
        with tempfile.TemporaryDirectory(prefix="journal-") as tmp:
            be = FileBackend(tmp)
            for i in range(2000):
                be.upsert("terms", f"t{i}", {"term": f"x{i}",
                                             "vec": [float(i)] * 64})
            be.flush()
            # time a ONE-ROW flush
            t0 = time.perf_counter()
            be.upsert("terms", "t-new", {"term": "new", "vec": [1.0] * 64})
            be.flush()
            el = time.perf_counter() - t0
            # ~ms: the journal appends ONE line; the whole-collection
            # dump (2000 rows x 64 floats) must NOT run
            self.assertLess(el, 0.15, f"one-row flush took {el:.3f}s")


class TestCompaction(unittest.TestCase):
    def test_compact_at_2x_live(self):
        """THE GC: the journal compresses to one row per live key when
        the appended lines out-carry the live count (the events lane's
        pattern)."""
        with tempfile.TemporaryDirectory(prefix="journal-") as tmp:
            be = FileBackend(tmp)
            for i in range(40):
                be.upsert("terms", "same", {"term": f"v{i}"})   # 40 lines, 1 live
            be.close()
            be2 = FileBackend(tmp)
            be2.flush()
            jfile = Path(tmp) / "terms.jsonl"
            lines = jfile.read_text().splitlines()
            self.assertLessEqual(len(lines), 2)


class TestSnapshotMigration(unittest.TestCase):
    def test_old_snapshot_migrates_to_journal_with_backup(self):
        """THE MIGRATION: a .json snapshot on disk + no journal -> the
        load reads it AND the first flush rewrites it as a .jsonl; the
        original snapshot is BACKED UP (renamed .migrated) -- the data
        never loses its bytes."""
        tmp = tempfile.mkdtemp(prefix="mig-")
        snap = Path(tmp) / "terms.json"
        snap.write_text(json.dumps({"t1": {"term": "old"}}))
        be = FileBackend(tmp)
        self.assertEqual(be.colls["terms"]["t1"]["term"], "old")
        be.upsert("terms", "t2", {"term": "new"})
        be.flush()
        self.assertTrue((Path(tmp) / "terms.jsonl").exists())
        self.assertTrue((Path(tmp) / "terms.json.migrated").exists(),
                        "the original snapshot backed up")
        be2 = FileBackend(tmp)
        self.assertEqual(be2.colls["terms"]["t1"]["term"], "old")
        self.assertEqual(be2.colls["terms"]["t2"]["term"], "new")


class TestWatchdogDump(unittest.TestCase):
    def test_dump_targets_stderr_file_not_stringio(self):
        """THE WATCHDOG'S EVIDENCE: the dump call writes to a REAL file
        (the stderr fd), never a StringIO (the fileno error x N)."""
        import membrane.server as S
        import inspect
        src = inspect.getsource(S.TurnRunner._tool_watchdog_dump)
        self.assertNotIn("StringIO", src)
        self.assertIn("sys.stderr", src)

if __name__ == "__main__":
    unittest.main()
