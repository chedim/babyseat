"""THE CURIOSITY LOOP (self-extinguishing attention): a confirmed
branch's question is served -- the hunger decays (x0.4, floor 0.05);
an unconfirmed branch nearing the GC horizon gets hungrier (+0.2, cap
1.0); the thinker's idle attention goes to the HUNGRIEST branch, not
the oldest; the elect persists the flip + the decay to the shared doc
(best-effort)."""

import unittest

from membrane.entities import (CLASS_TIMELINE, CLASS_SESSION, EntityStore,
                               QuantumEntity, MEMBRANE_NS)


def _store():
    return EntityStore(bucket="test-curiosity", shared=False)


def _seed_now(est):
    now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                        properties={"name": "now", "grain": 0.003})
    est.upsert(now)
    return now


class TestCuriosityDecay(unittest.TestCase):
    def test_confirm_decays_curiosity(self):
        est = _store()
        _seed_now(est)
        est.make_timeline(terminal="cache the prefix table",
                          vec=[1.0, 0.0], seed_intent="speed")
        tl = [e for e in est.docs.values() if e.cls == CLASS_TIMELINE][0]
        c0 = float(tl.properties["curiosity"])
        est.elect_timeline([1.0, 0.0], {"speed": 0.9})
        self.assertEqual(tl.properties["status"], "confirmed")
        c1 = float(tl.properties["curiosity"])
        self.assertLess(c1, c0)
        self.assertGreaterEqual(c1, 0.05)

    def test_confirm_decay_floors(self):
        """The floor: never fully dead -- a confirmed branch still teaches."""
        est = _store()
        _seed_now(est)
        est.make_timeline(terminal="almost served", vec=[1.0, 0.0],
                          seed_intent="s")
        tl = [e for e in est.docs.values() if e.cls == CLASS_TIMELINE][0]
        tl.properties["curiosity"] = 0.05
        est.elect_timeline([1.0, 0.0], {"s": 0.9})
        self.assertGreaterEqual(float(tl.properties["curiosity"]), 0.05)

    def test_think_ahead_picks_hungriest_branch(self):
        """The attention rule: the hungriest branch is extended first
        (ties -> newest), not simply the oldest."""
        import membrane.thinker as think_mod
        from membrane.thinker import Thinker
        from tests.test_thinker import _Srv
        srv = _Srv()
        th = Thinker(srv)
        est = srv.entity_store
        _seed_now(est)
        low = est.make_timeline(terminal="low curiosity branch",
                                vec=[1.0, 0.0], seed_intent="a")
        low.properties["curiosity"] = 0.1
        high = est.make_timeline(terminal="high curiosity branch",
                                 vec=[0.0, 1.0], seed_intent="b")
        high.properties["curiosity"] = 0.9
        # the token walk produces one step for whichever branch is picked
        tt = type("TT", (), {"walk": staticmethod(
            lambda toks, max_steps=14: toks + ["next"])})()
        srv.membrane.timeline = tt
        th._idle_think()
        got = (est.docs.get(high.eid).properties or {}).get("steps")
        self.assertEqual(got, ["next"],
                         "the hungriest branch must be extended first")

    def test_gc_grows_hunger_near_horizon(self):
        """2/3 of the horizon, unconfirmed -> +0.2 (cap 1.0); past the
        horizon -> GC (removed)."""
        import time as _t
        from membrane.dreamer import Dreamer, GC_HORIZON_S
        from tests.test_thinker import _Srv
        srv = _Srv()
        th = Dreamer(srv, idle=60.0)
        est = srv.entity_store
        _seed_now(est)
        br = est.make_timeline(terminal="approaching expiry",
                               vec=[1.0, 0.0])
        # 2/3 horizon old:
        br.properties["born"] = _t.time() - GC_HORIZON_S * 0.8
        th._gc(srv.membrane)
        c = float((est.docs.get(br.eid).properties or {}).get("curiosity"))
        self.assertGreater(c, 0.7)
        # past the horizon: removed
        br2 = est.make_timeline(terminal="past expiry", vec=[0.0, 1.0])
        est.docs[br2.eid].properties["born"] = _t.time() - GC_HORIZON_S * 1.2
        th._gc(srv.membrane)
        self.assertIsNone(est.docs.get(br2.eid))


if __name__ == "__main__":
    unittest.main()
