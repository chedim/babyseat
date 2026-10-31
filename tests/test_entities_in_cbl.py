"""The entity store lives in the seat's Couchbase Lite store (operator, 2026-10-31: "ok,
move it"): a save writes only the documents that changed, never the whole store (the
JSON mirror had grown to 308 MB, rewritten whole every 10 s, holding the interpreter).
The JSON file migrates once and stays as a backup."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

try:
    from membrane.cblstore import CBLiteBackend
    CBLiteBackend(Path(tempfile.mkdtemp()) / "probe.cblite").close()
    HAVE = True
except Exception:
    HAVE = False


def _ent(name, pos=None):
    e = QuantumEntity(eid=f"membrane::thing:{name}", cls="Component", properties={"name": name})
    if pos is not None:
        e.position = [float(x) for x in pos]
        e.position_history = [[1000.0, list(e.position)]]
    return e


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestEntitiesInCBL(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.json = self.d / "entities.json"
        self.be = CBLiteBackend(self.d / "field.cblite")
        self.addCleanup(self.be.close)

    def _store(self):
        return EntityStore(path=str(self.json), shared=False, cbl=self.be.db)

    def _puts(self):
        n = {"put": 0, "purge": 0}
        rp, rg = self.be.db.put_json, self.be.db.purge

        def put(*a, **k):
            n["put"] += 1
            return rp(*a, **k)

        def purge(*a, **k):
            n["purge"] += 1
            return rg(*a, **k)
        self.be.db.put_json, self.be.db.purge = put, purge
        return n

    def test_the_json_file_migrates_once_and_stays_as_a_backup(self):
        old = EntityStore(path=str(self.json), shared=False)
        for i in range(3):
            old.docs[f"membrane::thing:{i}"] = _ent(str(i), pos=[0.5, i, 1.0])
        old.tracks["t1"] = {"key": "t1", "label": "x"}
        old.save()
        s = self._store()
        self.assertEqual({f"membrane::thing:{i}" for i in range(3)} <= set(s.docs), True)
        s.save()
        self.assertFalse(self.json.exists(), "the JSON file is still the live home")
        self.assertTrue(list(self.d.glob("entities.json.migrated-*")), "no backup kept")
        again = self._store()                      # from CBL alone now
        self.assertEqual(again.docs["membrane::thing:2"].position, [0.5, 2.0, 1.0])
        self.assertEqual(again.docs["membrane::thing:2"].position_history[-1][1], [0.5, 2.0, 1.0])
        self.assertEqual(again.tracks.get("t1", {}).get("label"), "x")
        self.assertFalse(self.json.exists())

    def test_a_save_writes_only_what_changed(self):
        s = self._store()
        for i in range(5):
            s.docs[f"membrane::thing:{i}"] = _ent(str(i))
        s.save()
        n = self._puts()
        s.save()
        self.assertEqual(n["put"], 0, "an unchanged store was written again")
        s.docs["membrane::thing:3"].properties["name"] = "changed in place"   # no upsert
        s.save()
        self.assertEqual(n["put"], 1)
        self.assertEqual(self._store().docs["membrane::thing:3"].properties["name"], "changed in place")

    def test_a_removal_is_purged(self):
        s = self._store()
        s.docs["membrane::thing:a"] = _ent("a")
        s.docs["membrane::thing:b"] = _ent("b")
        s.save()
        s.docs.pop("membrane::thing:a")
        n = self._puts()
        s.save()
        self.assertEqual(n, {"put": 0, "purge": 1})
        self.assertNotIn("membrane::thing:a", self._store().docs)

    def test_an_id_cbl_cannot_take_is_kept_under_a_hashed_key(self):
        """A URL entity's id ran past CBL's document id limit and the entity was dropped."""
        long_eid = "membrane::urls:https://github.com/x/y/issues/new?title=" + "a%20" * 200
        s = self._store()
        e = _ent("u")
        e.eid = long_eid
        s.docs[long_eid] = e
        s.save()
        self.assertTrue(s._side_saved)
        self.assertIn(long_eid, self._store().docs)
        s.docs.pop(long_eid)
        s.save()
        self.assertNotIn(long_eid, self._store().docs)

    def test_without_cbl_the_json_file_stays_the_home(self):
        s = EntityStore(path=str(self.json), shared=False)
        s.docs["membrane::thing:a"] = _ent("a")
        s.save()
        self.assertIn("membrane::thing:a", json.loads(self.json.read_text())["docs"])


if __name__ == "__main__":
    unittest.main()
