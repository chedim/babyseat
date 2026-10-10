"""CONSOLIDATION, step 0 + step 1 (2026-10-10, docs/plans/
2026-10-10-generalization.md, red first). Readings become experience (the
member's own values, deduped, paced, with a ring); classes by geometry are
induced on the OLDER half of the window and tested on whether they MOVE
alike in the NEWER half, against random groups and the name families.
"""
import os
import random
import sys
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import consolidate as C  # noqa: E402


class TestReadings(unittest.TestCase):
    def test_the_members_own_values(self):
        fan = {"ha:light.port_strip": {"state": "on", "brightness": 52},
               "ha:light.aft_cabin_light": {"state": "off"}}
        self.assertEqual(C.own_reading("membrane::Component:ha:light.port_strip", fan),
                         {"state": "on", "brightness": 52})
        self.assertEqual(C.own_reading("x", {"soc": 92}), {"soc": 92})
        big = {f"ha:light.l{i}": {"state": "on"} for i in range(20)}
        self.assertEqual(C.own_reading("ha:light.other", big), {})

    def test_a_reading_is_recorded_once_per_change_and_paced(self):
        ent = type("E", (), {"eid": "membrane::ha:light.port_strip", "properties": {}})()
        srv = type("S", (), {"efield": None})()
        t = time.time()
        self.assertTrue(C.record_reading(srv, ent, {"state": "on"}, t))
        self.assertFalse(C.record_reading(srv, ent, {"state": "on"}, t + 120))   # unchanged
        self.assertFalse(C.record_reading(srv, ent, {"state": "off"}, t + 10))   # too soon
        self.assertTrue(C.record_reading(srv, ent, {"state": "off"}, t + 120))
        self.assertEqual([r["v"]["state"] for r in ent.properties["readings"]], ["on", "off"])


class _Field:
    """Two families that sit apart and move differently, plus loners."""

    def __init__(self, now):
        rng = np.random.default_rng(3)
        d = 64
        base = {"A": rng.normal(size=d), "B": rng.normal(size=d)}
        move = {"A": rng.normal(size=d), "B": rng.normal(size=d)}
        self._states, self._deltas, self.sent = {}, {}, {}
        t0 = now - 7 * 86400
        for fam, n in (("A", 6), ("B", 6), ("L", 8)):
            for i in range(n):
                eid = f"ha:{'light' if fam == 'A' else 'switch'}.{fam.lower()}{i}" if fam != "L" else f"c:loner{i}"
                self._states[eid] = type("St", (), {"eid": eid, "tier": "live"})()
                b0 = base.get(fam, rng.normal(size=d)) + 0.15 * rng.normal(size=d)
                mv = move.get(fam, rng.normal(size=d))
                rows = []
                for k in range(8):
                    late = k >= 4
                    v = b0 + (1.5 * mv if late else 0) + 0.1 * rng.normal(size=d)
                    sid = f"s:{eid}:{k}"
                    self.sent[sid] = {"vec": list(v / np.linalg.norm(v))}
                    rows.append({"sid": sid, "ts": t0 + k * 3600 + (5 * 86400 if late else 0)})
                self._deltas[eid] = rows
        self.b = type("B", (), {"get_multi": lambda _s, coll, keys: {k: self.sent[k] for k in keys if k in self.sent}})()

    def deltas_of(self, eid):
        return self._deltas.get(eid, [])


class TestClassesByGeometry(unittest.TestCase):
    def test_sit_alike_move_alike_beats_random(self):
        now = time.time()
        rep = C.induce_classes(_Field(now), now=now, rng=random.Random(1))
        self.assertGreaterEqual(rep["clusters"], 2)
        self.assertGreater(rep["geometry_gain"], 0.3)
        tops = [set(c["members"]) for c in rep["top_clusters"][:2]]
        self.assertTrue(any(all(m.startswith("ha:light") for m in g) for g in tops))
        self.assertTrue(rep["shadow"])

    def test_mirror_and_symbol_junk_never_cluster(self):
        now = time.time()
        f = _Field(now)
        for eid in list(f._states)[:6]:
            new = "m:m:" + eid
            f._states[new] = type("St", (), {"eid": new, "tier": "live"})()
            f._deltas[new] = f._deltas[eid]
        rep = C.induce_classes(f, now=now, rng=random.Random(1))
        self.assertFalse(any(m.startswith("m:") for c in rep["top_clusters"] for m in c["members"]))

    def test_too_little_history_says_so(self):
        f = type("F", (), {"_states": {}, "deltas_of": lambda s, e: [], "b": None})()
        self.assertIn("note", C.induce_classes(f, now=time.time()))

    def test_the_phase_is_paced_and_logged(self):
        import tempfile
        d = tempfile.mkdtemp()
        srv = type("S", (), {"_models_path": str(Path(d) / "models.json")})()
        now = time.time()
        f = _Field(now)
        self.assertIsNotNone(C.maybe_induce(srv, f, None, now))
        self.assertIsNone(C.maybe_induce(srv, f, None, now + 60))            # paced
        self.assertTrue((Path(d) / "consolidation.jsonl").read_text().strip())


if __name__ == "__main__":
    unittest.main()


class TestClassReadingsSpread(unittest.TestCase):
    """The seat's fan-out probe (2026-10-10): a class getter's one fetch
    held all lights, but only the member being read got a reading -- 27 real
    lights stayed 'unconfirmed'."""

    def test_every_member_in_the_map_gets_its_own_reading(self):
        from membrane.entities import EntityStore, CLASS_COMPONENT
        from membrane import night as N
        est = EntityStore(path=None, shared=False)
        cls = est.docs[est.add(cls=CLASS_COMPONENT, name="ha light", summary="x")["eid"]]
        cls.properties["getter_script"] = "result = {}"
        ms = []
        for n in ("ha:light.a_one", "ha:light.b_two", "ha:light.ghost"):
            d = est.docs[est.add(cls=CLASS_COMPONENT, name=n, summary="x")["eid"]]
            d.properties["parents"] = [cls.eid]
            ms.append(d)
        srv = type("S", (), {"efield": None})()
        C._class_spread_ts.clear()
        state = {"light.a_one": {"state": "on"}, "light.b_two": {"state": "off"}}
        self.assertEqual(C.record_class_readings(srv, est, cls.eid, state, now=1000.0), 2)
        self.assertEqual(ms[1].properties["readings"][-1]["v"], {"state": "off"})
        self.assertNotIn("readings", ms[2].properties)               # the ghost stays unconfirmed
        members, unc = N.member_split(est, cls.eid)
        self.assertEqual({N.short_name_of(m) for m in members}, {"ha:light.a_one", "ha:light.b_two"})
        self.assertEqual([N.short_name_of(m) for m in unc], ["ha:light.ghost"])
        self.assertEqual(C.record_class_readings(srv, est, cls.eid, state, now=1010.0), 0)   # once a minute
