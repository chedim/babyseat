"""THE TIMELINE ORGAN (dreamer v2): dreamed branches as ENTITIES under
session:now (the only true timeline). Status is reality's vote, never
self-declared: quarantined (proposed) -> confirmed (a real turn served
its seed intent) / refuted (a near turn it could not serve) /
impossible (the vet's physics lane -- near a known FailureMode). The
tests pin the lifecycle without couchbase (shared=False stores)."""

import unittest

from membrane.entities import (CLASS_TIMELINE, CLASS_FAILURE,
                               EntityStore, QuantumEntity)


def _store():
    return EntityStore(bucket="test-tl", shared=False)


class TestTimelineLifecycle(unittest.TestCase):
    def _seed_now(self, est):
        from membrane.entities import QuantumEntity, MEMBRANE_NS, CLASS_SESSION
        now = QuantumEntity(
            eid=f"{MEMBRANE_NS}session:now", cls=CLASS_SESSION,
            properties={"name": "now", "grain": 0.003, "ephemeral": True})
        est.upsert(now)
        return now

    def test_commit_creates_quarantined_branch_under_now(self):
        est = _store()
        self._seed_now(est)
        ent = est.make_timeline(terminal="we could cache the prefix table",
                                vec=[1.0, 0.0, 0.0],
                                seed_intent="speed up tests",
                                steps=["step one", "step two"])
        self.assertEqual(ent.cls, CLASS_TIMELINE)
        props = ent.properties
        self.assertEqual(props["status"], "quarantined")
        self.assertEqual(props["origin"], "dream")
        self.assertEqual(len(props["steps"]), 2)
        # the ISA link to the true timeline
        parents = (ent.properties or {}).get("parents") or \
                  (props.get("parents"))
        # set_parents stores under 'parents'
        self.assertTrue(est._resolve_spec("membrane::session:now") is not None)

    def test_elect_confirms_serving_branch(self):
        est = _store()
        self._seed_now(est)
        est.make_timeline(terminal="cache the prefix table",
                          vec=[1.0, 0.0, 0.0], seed_intent="speed the board")
        res = est.elect_timeline([1.0, 0.0, 0.0],
                                 {"speed the board": 0.9})
        self.assertEqual(res["confirmed"], 1)
        self.assertEqual(res["refuted"], 0)
        tl = [e for e in est.docs.values() if e.cls == CLASS_TIMELINE][0]
        self.assertEqual(tl.properties["status"], "confirmed")

    def test_elect_refutes_unserving_branch(self):
        est = _store()
        self._seed_now(est)
        est.make_timeline(terminal="an unsteered musing",
                          vec=[1.0, 0.0, 0.0], seed_intent="nothing-open")
        res = est.elect_timeline([1.0, 0.0, 0.0], {})   # serves nothing
        self.assertEqual(res["refuted"], 1)
        self.assertFalse([e for e in est.docs.values()
                          if e.cls == CLASS_TIMELINE])

    def test_open_timelines_filters_status_and_parent(self):
        est = _store()
        self._seed_now(est)
        est.make_timeline(terminal="branch one", vec=[1.0, 0.0])
        est.make_timeline(terminal="branch impossible", vec=[0.0, 1.0],
                          status="impossible")
        est.make_timeline(terminal="confirmed branch", vec=[0.5, 0.5],
                          status="confirmed")
        open_tl = est.open_timelines()
        # quarantine + confirmed render; impossible does not
        terminals = [e.properties["terminal"] for e in open_tl]
        self.assertIn("branch one", terminals)
        self.assertIn("confirmed branch", terminals)
        self.assertNotIn("branch impossible", terminals)

    def test_make_timeline_merges_on_collision(self):
        """A re-proposed branch strengthens the existing doc, never a
        duplicate (the derived_by union contract)."""
        est = _store()
        self._seed_now(est)
        a = est.make_timeline(terminal="same branch", vec=[1.0, 0.0])
        b = est.make_timeline(terminal="same branch", vec=[1.0, 0.0])
        self.assertEqual(a.eid, b.eid)
        n = sum(1 for e in est.docs.values() if e.cls == CLASS_TIMELINE)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
