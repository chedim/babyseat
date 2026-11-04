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


class Regress923(unittest.TestCase):
    def _w(self):
        est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est)
        return est

    def test_923_same_name_component_class_owner_binds(self):
        est = self._w()
        pump = est.docs[est.add(cls=CLASS_COMPONENT, name="Pump", summary="x")["eid"]]
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "dev:p1", cls="Pump", properties={"name": "p1"}))
        d = N.method_define(None, est, None, None, "Pump", "run923", "true")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], pump.eid, d)
        m, _via = N.resolve_method(est, est.docs[MEMBRANE_NS + "dev:p1"], "run923")
        self.assertIsNotNone(m, d)

    def test_923_minted_class_then_member_binds_same_owner(self):
        est = self._w()
        d1 = N.method_define(None, est, None, None, "Thing", "first923", "true")
        self.assertTrue(d1["ok"], d1)
        thing = d1["owner"]["eid"]
        self.assertIn("thing", thing.lower())
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "dev:t1", cls="Thing", properties={"name": "t1"}))
        d2 = N.method_define(None, est, None, None, "Thing", "second923", "true")
        self.assertTrue(d2["ok"], d2)
        self.assertEqual(d2["owner"]["eid"], thing, d2)
        m, _via = N.resolve_method(est, est.docs[MEMBRANE_NS + "dev:t1"], "second923")
        self.assertIsNotNone(m, d2)

    def test_923_gap_register_cls_gap_binds_real_class(self):
        # chosen outcome: BIND to a real class Register (not the gap entity)
        est = self._w()
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "gap:register", cls="Gap",
                                 properties={"name": "register"}))
        for n in ("r1", "r2"):
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "reg:" + n, cls="Register",
                                     properties={"name": n}))
        d = N.method_define(None, est, None, None, "Register", "probe", "true")
        self.assertTrue(d["ok"], d)
        self.assertNotIn("gap:register", d["owner"]["eid"], d)
        self.assertEqual(d["owner"]["kind"], "class", d)
        m, _via = N.resolve_method(est, est.docs[MEMBRANE_NS + "reg:r1"], "probe")
        self.assertIsNotNone(m, d)

    def test_923_lowercase_pump_and_ha_light_bind_plain_entities(self):
        est = self._w()
        pump = est.docs[est.add(cls=CLASS_COMPONENT, name="pump", summary="x")["eid"]]
        d = N.method_define(None, est, None, None, "pump", "m923", "echo pump923")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], pump.eid)
        light = est.docs[est.add(cls=CLASS_COMPONENT, name="ha light", summary="x")["eid"]]
        d = N.method_define(None, est, None, None, "ha light", "ml923", "echo light923")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["owner"]["eid"], light.eid)


if __name__ == "__main__":
    unittest.main()
