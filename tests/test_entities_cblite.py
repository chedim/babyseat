"""ENTITIES LIVE IN field.cblite (goal e619d8c3a764): the entity store reads and
writes through the cblite backend, imports an existing entities.json exactly
once, and never writes entities.json again. Failing first: EntityStore has no
`backend=` yet and always mirrors to the json file. Skipped where the Couchbase
Lite libraries are not fetched."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402
from membrane.entities import CLASS_DECISION, EntityStore, QuantumEntity  # noqa: E402

HAVE = cblite.lib_paths() is not None
if HAVE:
    from membrane.cblstore import CBLiteBackend  # noqa: E402

EID = "membrane::synth:cbl-a"


def _ent(summary="s"):
    return QuantumEntity(eid=EID, cls=CLASS_DECISION,
                         properties={"name": "cbl-a", "summary": summary},
                         links=[1], derived_by=["operator"])


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestEntitiesInCblite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.json = self.tmp / "entities.json"
        self.open = []

    def tearDown(self):
        for b in self.open:
            try:
                b.close()
            except Exception:
                pass

    def _be(self):
        b = CBLiteBackend(self.tmp / "field.cblite")
        self.open.append(b)
        return b

    def _close(self, b):
        b.close()
        self.open.remove(b)

    def _store(self, be):
        return EntityStore(path=str(self.json), shared=False, backend=be)

    def test_a_entities_survive_reopen_from_cblite_alone(self):
        be = self._be()
        st = self._store(be)
        st.upsert(_ent("kept"))
        st.flush()
        self._close(be)
        self.json.unlink(missing_ok=True)            # entities.json is gone
        be2 = self._be()
        st2 = self._store(be2)
        got = st2.docs.get(EID)
        self.assertIsNotNone(got, "entity not loaded from cblite alone")
        self.assertEqual(got.properties.get("summary"), "kept")
        self.assertFalse(self.json.exists(), "loading recreated entities.json")

    def test_b_existing_json_imported_once_never_overwrites_newer(self):
        legacy = EntityStore(path=str(self.json), shared=False)   # the old store
        legacy.upsert(_ent("from-json"))
        legacy.flush()
        self.assertTrue(self.json.exists())
        be = self._be()
        st = self._store(be)                          # first start: imports
        self.assertEqual(st.docs[EID].properties.get("summary"), "from-json")
        st.upsert(_ent("newer-in-cblite"))
        st.flush()
        self._close(be)
        be2 = self._be()                              # second start, json still there
        st2 = self._store(be2)
        self.assertEqual(st2.docs[EID].properties.get("summary"), "newer-in-cblite",
                         "second start re-imported/overwrote newer cblite data")

    def test_c_no_entities_json_write_after_save(self):
        be = self._be()
        st = self._store(be)
        st.upsert(_ent())
        st.save()
        st.flush()
        self.assertFalse(self.json.exists(), "save() wrote entities.json")
        self.json.write_text(json.dumps({"docs": {}}))     # a sentinel
        before = self.json.read_bytes()
        st.upsert(_ent("again"))
        st.save()
        self.assertEqual(self.json.read_bytes(), before, "save() rewrote entities.json")


if __name__ == "__main__":
    unittest.main()
