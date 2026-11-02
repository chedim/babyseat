"""Every goal's plan request rides one trigger (goal-graph-plan), so they all count as one
caller; at 12 calls an hour per caller, the 13th goal planned in an hour was refused, never
queued and never retried (2026-11-01: new goals and the replans after finished tasks all
stalled from 17:55). A plan request -- the planner lane, about an open goal -- skips the
per-caller budget (the planner lane's two slots pace planning); it still counts in the hour's
global cap, and every other call keeps the per-caller budget."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_llm_entity import _Srv  # noqa: E402
from membrane import llm as L  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402

PLAN = "trigger:" + MEMBRANE_NS + "Trigger:goal-graph-plan"


def _goals(srv, n, status="open"):
    out = []
    for i in range(n):
        g = QuantumEntity(eid=MEMBRANE_NS + f"goal:g{i}", cls="Goal", properties={"name": f"goal {i}", "status": status})
        srv.entity_store.docs[g.eid] = g
        out.append(g.eid)
    return out


class PlannerBudget(unittest.TestCase):
    def setUp(self):
        L._calls.clear()
        L._echo.clear()

    def test_more_goals_than_the_per_caller_cap_are_all_planned(self):
        srv = _Srv()
        for i, g in enumerate(_goals(srv, L.CALLER_PER_HOUR + 3)):
            out = L.request(srv, "plan the goal: planted", about=g, caller=PLAN, now=1000.0 + i,
                            extra={"lane": "planner"})
            self.assertEqual(out["rc"], 0, out)
        self.assertEqual(len(srv.events_pool.items), L.CALLER_PER_HOUR + 3)

    def test_plan_requests_still_count_in_the_hours_global_cap(self):
        srv = _Srv()
        L._calls[:] = [(1000.0, f"x{i}") for i in range(L.GLOBAL_PER_HOUR)]
        g = _goals(srv, 1)[0]
        out = L.request(srv, "plan the goal: planted", about=g, caller=PLAN, now=1001.0, extra={"lane": "planner"})
        self.assertIn("budget is spent", out["text"])

    def test_the_planner_lane_about_a_non_goal_keeps_the_per_caller_cap(self):
        srv = _Srv()
        for i in range(L.CALLER_PER_HOUR):
            L.request(srv, f"q{i}", about="Component:bat", caller="script:a", now=1000.0 + i, extra={"lane": "planner"})
        out = L.request(srv, "q-over", about="Component:bat", caller="script:a", now=1100.0, extra={"lane": "planner"})
        self.assertIn("this hour", out["text"])

    def test_a_closed_goal_keeps_the_per_caller_cap(self):
        srv = _Srv()
        goals = _goals(srv, L.CALLER_PER_HOUR + 1, status="closed")
        for i, g in enumerate(goals[:-1]):
            L.request(srv, "plan", about=g, caller=PLAN, now=1000.0 + i, extra={"lane": "planner"})
        out = L.request(srv, "plan", about=goals[-1], caller=PLAN, now=1100.0, extra={"lane": "planner"})
        self.assertIn("this hour", out["text"])


if __name__ == "__main__":
    unittest.main()
