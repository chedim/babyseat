"""#799, part 2: the seat's small data files (the absorb watermark, the router's state,
the watch board, the background-usage tally) live in its Couchbase Lite store, one
document per file, when the seat registered its database; a file migrates the first
time it is read and stays renamed as a backup. Anything outside the seat's data folder,
and every seat without the database, keeps its files."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import datafiles as DF  # noqa: E402

try:
    from membrane.cblstore import CBLiteBackend
    CBLiteBackend(Path(tempfile.mkdtemp()) / "probe.cblite").close()
    HAVE = True
except Exception:
    HAVE = False


class _Seat(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.be = CBLiteBackend(self.d / "field.cblite")
        self.addCleanup(self.be.close)
        DF.use(self.be.db, self.d)
        self.addCleanup(DF.use, None, None)


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestTheAdapter(_Seat):
    def test_a_document_round_trips_and_no_file_is_written(self):
        DF.write_json(self.d / "x.json", {"a": 1})
        self.assertEqual(DF.read_json(self.d / "x.json"), {"a": 1})
        self.assertFalse((self.d / "x.json").exists())

    def test_a_missing_document_reads_as_the_default(self):
        self.assertIsNone(DF.read_json(self.d / "nothing.json"))
        self.assertEqual(DF.read_json(self.d / "nothing.json", {}), {})

    def test_a_file_migrates_on_first_read(self):
        (self.d / "old.json").write_text(json.dumps({"b": 2}))
        self.assertEqual(DF.read_json(self.d / "old.json"), {"b": 2})
        self.assertFalse((self.d / "old.json").exists())
        self.assertTrue(list(self.d.glob("old.json.migrated-*")))
        self.assertEqual(DF.read_json(self.d / "old.json"), {"b": 2})

    def test_registering_migrates_the_seat_files_at_once(self):
        DF.use(None, None)
        (self.d / "watches.json").write_text(json.dumps({"next_wid": 3, "watches": []}))
        DF.use(self.be.db, self.d)
        self.assertFalse((self.d / "watches.json").exists())
        self.assertEqual(DF.read_json(self.d / "watches.json")["next_wid"], 3)

    def test_a_path_outside_the_seat_folder_stays_a_file(self):
        other = Path(tempfile.mkdtemp()) / "y.json"
        DF.write_json(other, {"c": 3})
        self.assertTrue(other.exists())
        self.assertEqual(DF.read_json(other), {"c": 3})


class TestWithoutTheDatabase(unittest.TestCase):
    def test_files_as_before(self):
        d = Path(tempfile.mkdtemp())
        DF.write_json(d / "z.json", {"d": 4})
        self.assertEqual(json.loads((d / "z.json").read_text()), {"d": 4})
        self.assertEqual(DF.read_json(d / "z.json"), {"d": 4})


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestTheStores(_Seat):
    def test_the_absorb_watermark(self):
        from membrane import absorb as AB
        p = str(self.d / "serve-absorb.json")
        AB.stamp_marker(p, {"version": 3, "last_run_ts": 5.0, "imported": []})
        self.assertEqual(AB.load_marker(p)["version"], 3)
        self.assertFalse(Path(p).exists())

    def test_the_background_usage_tally(self):
        from membrane import bgusage as BG
        with mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": str(self.d)}):
            BG.record("night", "qwen3:8b", input=10, output=2)
            BG.record("night", "qwen3:8b", input=5)
            doc = BG.load(BG.path())
        self.assertEqual(doc["lanes"]["night"]["qwen3:8b"]["calls"], 2)
        self.assertEqual(doc["lanes"]["night"]["qwen3:8b"]["input"], 15)
        self.assertFalse((self.d / "bg-usage.json").exists())

    def test_stats_reads_the_tally_from_the_database(self):
        from membrane import bgusage as BG
        from membrane import stats as ST
        with mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": str(self.d)}):
            BG.record("night", "qwen3:8b", input=10)
        self.assertEqual(ST.read_cbl_file(self.d / "field.cblite", BG.FILE)["lanes"]["night"]["qwen3:8b"]["calls"], 1)

    def test_the_watch_board(self):
        from membrane.watch import WatchBoard
        b = WatchBoard(self.d / "watches.json")
        b._next_wid = 7
        b.save()
        self.assertFalse((self.d / "watches.json").exists())
        self.assertEqual(WatchBoard.load(self.d / "watches.json")._next_wid, 7)

    def test_the_router_state(self):
        from membrane.router import Registry
        r = Registry("http://127.0.0.1:9", state_path=self.d / "router.json")
        r.spend_today = 1.25
        r.save()
        self.assertFalse((self.d / "router.json").exists())
        self.assertEqual(Registry("http://127.0.0.1:9", state_path=self.d / "router.json").spend_today, 1.25)


if __name__ == "__main__":
    unittest.main()
