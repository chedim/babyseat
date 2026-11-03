import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from membrane import night as N
from membrane.entities import EntityStore, CLASS_COMPONENT


class T(unittest.TestCase):
    def _w(self):
        est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est)
        return est

    def test_reply_names_owner(self):
        est = self._w()
        light = est.docs[est.add(cls=CLASS_COMPONENT, name="ha light", summary="x")["eid"]]
        d = N.method_define(None, est, None, None, light.eid, "m918", "echo hi")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], light.eid)
        self.assertIn("kind", d["owner"])

    def test_non_class_name_match_refused(self):
        est = self._w()
        est.add(cls=CLASS_COMPONENT, name="gap", summary="x")
        d = N.method_define(None, est, None, None, "gap", "m918b", "echo hi")
        self.assertFalse(d["ok"], d)
        self.assertIn("gap", d["error"])


if __name__ == "__main__":
    unittest.main()
