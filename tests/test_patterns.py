"""THE GENERALIZED PATTERN DETECTOR + THE FN->ENTITY STATE BRIDGE (the
operator's A-generalized + B): three universal pattern classes over the
digest stream (recurrence / failure-resonance / co-activation) and the
fn stamps updating the touched entities' live properties -- the entity
layer as a model of project state, the monitor-getter scripts' feed."""

import unittest

from membrane.entities import (CLASS_FAILURE, EntityStore, QuantumEntity,
                               MEMBRANE_NS)
from membrane.thinker import Thinker


class _Srv:
    def __init__(self):
        self.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})(), "embedder": None})()
        self.entity_store = EntityStore(bucket="test-patterns", shared=False)
        self._speak_queue = []
        self._act_table = None
        self.thinker = None


class TestFnBridge(unittest.TestCase):
    def test_fn_updates_entity_properties(self):
        """The fn stamp moves the touched entity's state: fn_counts
        (per-op, windowed) + last_fn -- the live project-state model."""
        srv = _Srv()
        th = Thinker(srv)
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}files:auth.py",
                            cls="Component", properties={"name": "auth.py"})
        est.upsert(ent)
        th._bridge_fn_state({"op": "edit"}, ["files:auth.py"], est, "call")
        th._bridge_fn_state({"op": "edit"}, ["files:auth.py"], est, "call")
        got = est.docs.get(f"{MEMBRANE_NS}files:auth.py")
        counts = (got.properties or {}).get("fn_counts")
        self.assertEqual(counts.get("edit"), 2)
        self.assertIn("last_fn", got.properties or {})

    def test_bridge_survives_missing_entities(self):
        """A tag resolving to no entity is skipped (never raises)."""
        srv = _Srv()
        th = Thinker(srv)
        th._bridge_fn_state({"op": "bash"}, ["files:does-not-exist.py"],
                            srv.entity_store, "call")


class TestPatterns(unittest.TestCase):
    def test_recurrence_speaks_after_threshold(self):
        """The same signature 3x in the window -> one pattern note; the
        cooldown bounds the repeat."""
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        for _ in range(3):
            th._check_recurrence("the same ask every time", [])
        self.assertEqual(th.stats["spoken"], 1)
        # the cooldown: the immediate re-fire is silent
        th._check_recurrence("the same ask every time", [])
        self.assertEqual(th.stats["spoken"], 1)

    def test_recurrence_silent_below_threshold(self):
        srv = _Srv()
        th = Thinker(srv)
        th._check_recurrence("once", [])
        th._check_recurrence("twice", [])
        self.assertEqual(th.stats["spoken"], 0)

    def test_failure_resonance_pushes_the_fix(self):
        """A live event's tag landing near a FailureMode entity -> the
        proactive recall push (one per digest)."""
        import numpy as np
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        est = srv.entity_store
        f = QuantumEntity(eid=f"{MEMBRANE_NS}failure:flaky-bench",
                          cls=CLASS_FAILURE,
                          properties={"name": "flaky benchmark hang"})
        f.position = [1.0, 0.0, 0.0]
        est.upsert(f)
        # the seat's embedder maps the event ONTO the failure's position
        srv.membrane.embedder = type("Emb", (), {
            "embed": staticmethod(lambda texts: [[1.0, 0.0, 0.0]])})()
        th._failure_resonance(["tools:pytest"], est,
                              "the benchmark hangs again")
        self.assertEqual(th.stats["spoken"], 1)
        # the second call in the same digest does not double-speak (the
        # early return after the first resonance)
        th.stats["spoken"] = 0
        th._failure_resonance([], est, "the benchmark hangs again")
        self.assertEqual(th.stats["spoken"], 0)


if __name__ == "__main__":
    unittest.main()
