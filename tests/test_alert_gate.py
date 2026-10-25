"""THE ALERT BUDGET (2026-10-08, the seat's own review, red first):

  "More observers = more next-turn interrupts = longer turns = 300s
   watchdog deaths (three turns eaten tonight). An alert budget per turn
   -- consolidate into the WHERE WE ARE block instead of N interrupt
   turns, with a digest mode this seat can dial."

Every watch event used to become a full thinking turn. The AlertGate lets
at most `budget` events per rolling window become interrupt turns; the
rest fold into a DIGEST (grouped per entity) that rides the next turn's
WHERE WE ARE block. Mode 'digest' folds everything. Goal events
(intent-planted/landed) are the operator's own asks: never folded, never
budgeted. The SEAT turns the dial (the attention tool); membrane only
keeps it.
"""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.attention import AlertGate  # noqa: E402


def ev(eid, kind="field:changed", **k):
    return {"kind": kind, "entity": eid, "ts": k.pop("ts", 0.0), **k}


class TestGate(unittest.TestCase):
    def test_budget_then_digest_grouped(self):
        g = AlertGate(budget=2, window_s=600)
        self.assertTrue(g.allow(ev("c:a"), now=0))
        self.assertTrue(g.allow(ev("c:b"), now=1))
        self.assertFalse(g.allow(ev("c:c", verb="pos"), now=2))
        self.assertFalse(g.allow(ev("c:c", verb="pos"), now=3))
        self.assertFalse(g.allow(ev("c:d", kind="watch-attached"), now=4))
        lines = g.digest(now=5)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("c:c" in l and "×2" in l for l in lines), lines)
        self.assertEqual(g.take_digest(now=5), lines)          # take clears
        self.assertEqual(g.digest(now=5), [])

    def test_window_refills(self):
        g = AlertGate(budget=1, window_s=60)
        self.assertTrue(g.allow(ev("c:a"), now=0))
        self.assertFalse(g.allow(ev("c:b"), now=30))
        self.assertTrue(g.allow(ev("c:b"), now=61))

    def test_new_goal_events_are_never_folded(self):
        # a NEW plant, its landing, and the pulse's rewake of a still-open
        # goal all pass: a goal is an ask, not noise (#551)
        g = AlertGate(budget=0, window_s=600, mode="digest")
        self.assertTrue(g.allow(ev("goal:1", kind="intent-planted", created=True), now=0))
        self.assertTrue(g.allow(ev("goal:1", kind="intent-landed"), now=0))
        self.assertEqual(g.digest(now=0), [])
        self.assertTrue(g.allow(ev("goal:2", kind="intent-planted", rewake=True), now=0))
        self.assertEqual(g.digest(now=0), [])

    def test_counter_wakes_cannot_starve_a_goal_rewake(self):
        g = AlertGate(budget=2, window_s=600)
        self.assertTrue(g.allow(ev("c:a"), now=0))
        self.assertTrue(g.allow(ev("c:b"), now=1))
        self.assertFalse(g.allow(ev("c:c"), now=2))        # budget spent
        self.assertTrue(g.allow(ev("goal:3", kind="intent-planted", rewake=True), now=3))

    def test_digest_mode_folds_everything_else(self):
        g = AlertGate(budget=10, window_s=600, mode="digest")
        self.assertFalse(g.allow(ev("c:a"), now=0))
        self.assertEqual(len(g.digest(now=0)), 1)

    def test_the_dial(self):
        g = AlertGate()
        out = g.set(budget=4, window_min=20, mode="digest")
        self.assertEqual((out["budget"], out["window_min"], out["mode"]), (4, 20, "digest"))
        with self.assertRaises(ValueError):
            g.set(mode="loud")
        with self.assertRaises(ValueError):
            g.set(budget=-1)
        self.assertEqual(AlertGate.from_settings(g.settings()).settings(), g.settings())

    def test_digest_is_bounded(self):
        g = AlertGate(budget=0, window_s=600)
        for i in range(100):
            g.allow(ev(f"c:{i}"), now=i)
        self.assertLessEqual(len(g.digest(now=200)), AlertGate.DIGEST_MAX + 1)  # + the '+N more' line


class _Pool:
    def __init__(self, evs):
        self.evs = list(evs)

    def take(self):
        out, self.evs = self.evs, []
        return out

    def put(self, e):
        self.evs.append(e)


class _Srv:
    pass


class TestDrainerHonorsTheGate(unittest.TestCase):
    def test_only_the_budget_becomes_turns(self):
        srv = _Srv()
        srv.events_pool = _Pool([ev("c:a"), ev("c:b"), ev("c:c")])
        srv.loops = WA.LoopRunner(max_loops=3)
        srv.entity_store = None
        srv.ambient_mode = "day"
        srv._alert_gate = AlertGate(budget=1, window_s=600)
        turns = []
        srv.create_loop_session = lambda agent, title: "ses_x"
        srv.run_loop_turn = lambda sid, text, agent: turns.append(agent) or {"verdict_text": "ignored"}
        d = WA._LoopDrainer(srv, poll_s=0.01).start()
        time.sleep(0.3)
        d.stop()
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(srv._alert_gate.digest(now=time.time())), 2)


if __name__ == "__main__":
    unittest.main()


class TestRefundAndWiring(unittest.TestCase):
    def test_a_requeued_event_is_not_charged_twice(self):
        g = AlertGate(budget=1, window_s=600)
        self.assertTrue(g.allow(ev("c:a"), now=0))
        g.refund()                               # the loop pool was full: re-queued
        self.assertTrue(g.allow(ev("c:a"), now=1))

    def test_the_digest_rides_where_we_are_and_drains_once(self):
        from membrane import lanes as L
        srv = _Srv()
        srv._alert_gate = AlertGate(budget=0, window_s=600)
        srv._alert_gate.allow(ev("c:anchor", verb="pos"), now=0)
        peek = L._digest_lines(srv, drain=False)
        self.assertTrue(any("c:anchor" in l for l in peek), peek)
        self.assertEqual(L._digest_lines(srv, drain=True), peek)
        self.assertEqual(L._digest_lines(srv, drain=True), [])

    def test_the_dial_is_the_seats_hand(self):
        from membrane import mcp_server as M
        from membrane import server as S
        self.assertIn("attention", S.OPERATOR_TOOLS)
        self.assertIn("attention", S.LISTENER_TOOLS)
        tool = next(t for t in M.TOOLS if t["name"] == "attention")
        self.assertEqual(set(tool["inputSchema"]["properties"]["mode"]["enum"]),
                         {"interrupt", "digest"})
        self.assertIn("attention", M.TOOL_IMPLS)
