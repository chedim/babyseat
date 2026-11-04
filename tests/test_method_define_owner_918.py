import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from membrane import night as N
from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS, CLASS_COMPONENT


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

    def _register_world(self, stray):
        est = self._w()
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "gap:register", cls=CLASS_COMPONENT,
                                 properties={"name": "register"}))
        for n in ("r1", "r2"):
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "reg:" + n, cls="Register",
                                     properties={"name": n}))
        if stray:
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "c:stray", cls=CLASS_COMPONENT,
                                     properties={"method_name": "old", "command": "true",
                                                 "method_of": [MEMBRANE_NS + "gap:register"]}))
        return est

    def _check_918(self, stray):
        est = self._register_world(stray)
        d = N.method_define(None, est, None, None, "Register", "probe", "true")
        if d["ok"]:
            self.assertNotIn("gap:register", d["owner"]["eid"], d)
            m, _via = N.resolve_method(est, est.docs[MEMBRANE_NS + "reg:r1"], "probe")
            self.assertIsNotNone(m, d)
        else:
            self.assertIn("register", d["error"].lower())

    def test_918_class_name_not_bound_to_entity(self):
        self._check_918(False)

    def test_918_class_name_with_stray_method(self):
        self._check_918(True)

    def test_plain_entity_names_still_bind(self):
        est = self._w()
        pump = est.docs[est.add(cls=CLASS_COMPONENT, name="pump", summary="x")["eid"]]
        d = N.method_define(None, est, None, None, "pump", "mp", "echo pump918")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], pump.eid)
        light = est.docs[est.add(cls=CLASS_COMPONENT, name="ha light", summary="x")["eid"]]
        d = N.method_define(None, est, None, None, "ha light", "ml", "echo light918")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], light.eid)


if __name__ == "__main__":
    unittest.main()
