"""PHASE 4: WHY-RECALL AT TOUCH-TIME -- the first touch of an entity in
a session surfaces its why-stamped cells ('why this code looks this
way'), once per entity per session (the seat-local cooldown), planner-
gated. No why-cells -> silent (the touch still marks)."""

import unittest

from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
from membrane.thinker import Thinker
from membrane.field import Exemplar


class _Srv:
    def __init__(self):
        self.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})(), "embedder": None})()
        self.entity_store = EntityStore(bucket="test-why-touch",
                                        shared=False)
        self._speak_queue = []
        self._act_table = None
        self.thinker = None


def _why_cell(situation, action, why_sim=0.9):
    ex = Exemplar(situation=situation, action=action, outcome="done",
                  vec=[1.0, 0.0, 0.0])
    ex.extra = {"why": why_sim}
    return ex


class TestWhyAtTouch(unittest.TestCase):
    def _entity_with_why(self, srv, tag="files:auth.py"):
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}{tag}", cls="Component",
                            properties={"name": tag.split(":")[1]})
        est.upsert(ent)
        # the why-cell linked to the entity
        cell = _why_cell("why: the auth uses a state machine to avoid "
                         "the race", "token race")
        srv.membrane.store.exemplars.append(cell)
        ent.links = [0]
        est.upsert(ent)
        return est, cell

    def test_first_touch_surfaces_the_why(self):
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        est, _cell = self._why_cell_helper(srv)
        th._why_at_touch(["files:auth.py"], est)
        self.assertEqual(th.stats["spoken"], 1)
        # the render carries the design-intent text
        msgs = [m["text"] for m in srv._speak_queue]
        self.assertTrue(any("auth" in m or "design" in m for m in msgs))

    def _why_cell_helper(self, srv):
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}files:auth.py",
                            cls="Component", properties={"name": "auth.py"})
        est.upsert(ent)
        cell = _why_cell("why: auth uses a state machine to avoid the "
                         "token race", "state machine it is")
        srv.membrane.store.exemplars.append(cell)
        ent.links = [0]
        est.upsert(ent)
        return est, cell

    def test_second_touch_is_silent(self):
        """The cooldown: the same entity's second touch in the session
        says nothing (once per entity per session)."""
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        self._why_cell_helper(srv)
        th._why_at_touch(["files:auth.py"], srv.entity_store)
        self.assertEqual(th.stats["spoken"], 1)
        th._why_at_touch(["files:auth.py"], srv.entity_store)
        self.assertEqual(th.stats["spoken"], 1)

    def test_no_why_cells_silent(self):
        """An entity without why-cells: silent (the touch still marks --
        a later why-cell learn re-arms only next session)."""
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}files:plain.py",
                            cls="Component", properties={"name": "plain.py"})
        est.upsert(ent)
        th._why_at_touch(["files:plain.py"], est)
        self.assertEqual(th.stats["spoken"], 0)
        # the touch still marks (the session's next digest won't re-probe)
        self.assertIn("files:plain.py", th._touched)


if __name__ == "__main__":
    unittest.main()
