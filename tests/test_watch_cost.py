"""THE WATCH LOOPS' BILL (2026-10-08, the transcript review, red first).

116 loop runs / 2056 tool calls in 8h (glm-5.3-flash:cloud, every one):

  * every BOOT re-fired every open goal -- no staleness, no backoff, no
    last landing -- and `intent-planted` was alert-exempt, so the gate
    covered 0 of 116 runs (47 duplicate runs, 37% of the tool calls);
  * `fulfill` refused the very eid the prompt hands out
    (membrane::goal:<id>, m:goal:<id>) and its name fallback closed the
    WRONG goal once;
  * a run cut off mid-tool (no text) was booked 'reacted' -- an empty
    landing that reset the backoff and told the next pass nothing;
  * 'Done' without `fulfill` kept a finished goal running 6 more times;
  * the prompt named tools that do not exist, cut the goal to 120 chars,
    set no budget, and 'ignored' parsed only as a literal prefix.

Pinned here: the boot wake rides the pulse's rules; only a NEW plant is
alert-exempt; the goal spec normalises; the loop's last line is a
structured verdict (DONE / STEP / BLOCKED / ignored, aborted when
silent); quiet passes back off; BLOCKED parks until the operator speaks;
DONE-but-open gets one 'call fulfill now' nudge.
"""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import attention as AT  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_GOAL, EntityStore, MEMBRANE_NS,  # noqa: E402
                               QuantumEntity)


def _store(name):
    return EntityStore(bucket=f"test-watch-cost-{name}-{time.time_ns()}", shared=False)


def _goal(est, slug, name, **props):
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
                        properties={"name": name, "status": "open", **props})
    est.upsert(ent, embedder=None)
    return ent.eid


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = _Pool()
        self.loops = WA.LoopRunner(max_loops=3)
        self._boot_ts = 1


class TestBootWake(unittest.TestCase):
    def setUp(self):
        self.est = _store("boot")
        self.srv = _Srv(self.est)
        self.now = time.time()

    def _woken(self):
        return [e["entity"] for e in self.srv.events_pool.items]

    def test_fresh_goal_is_not_refired_on_boot(self):
        _goal(self.est, "fresh", "fresh", intent_planted_ts=self.now - 60)
        self.assertEqual(WA.boot_wake_goals(self.srv, now=self.now), 0)

    def test_stale_goal_wakes_as_a_continuation(self):
        eid = _goal(self.est, "old", "old", intent_planted_ts=0,
                    landings=[{"op": "step", "decision": "pkg repaired"}])
        self.assertEqual(WA.boot_wake_goals(self.srv, now=self.now), 1)
        ev = self.srv.events_pool.items[0]
        self.assertEqual(ev["entity"], eid)
        self.assertTrue(ev["rewake"])
        self.assertIn("pkg repaired", WA.loop_task_text(ev))

    def test_backoff_holds_across_boots(self):
        _goal(self.est, "parked", "parked",
              landings=[{"op": "ignored"}, {"op": "aborted"}],
              intent_planted_ts=self.now - 3 * 1800)      # x4 wait = 7200
        self.assertEqual(WA.boot_wake_goals(self.srv, now=self.now), 0)

    def test_boot_needs_no_idle_pool(self):
        _goal(self.est, "old", "old", intent_planted_ts=0)
        self.srv.events_pool.put({"kind": "around"})
        self.assertEqual(WA.boot_wake_goals(self.srv, now=self.now), 1)

    def test_blocked_goal_parks_until_the_operator_speaks(self):
        _goal(self.est, "sig", "needs the operator's signature",
              intent_planted_ts=0, blocked_ts=self.now - 100,
              blocked_on="operator signature")
        self.assertEqual(WA.boot_wake_goals(self.srv, now=self.now), 0)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800), 0)
        self.srv._operator_last_ts = self.now - 10
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800), 1)


class TestQuietStreak(unittest.TestCase):
    def test_only_a_step_resets_the_backoff(self):
        S = WA._ignored_streak
        self.assertEqual(S({"landings": [{"op": "step"}, {"op": "ignored"}, {"op": "aborted"}]}), 2)
        self.assertEqual(S({"landings": [{"op": "ignored"}, {"op": "step"}]}), 0)
        self.assertEqual(S({"landings": [{"op": "reacted"}, {"op": "done"}]}), 2)


class TestGateCoversRewakes(unittest.TestCase):
    def test_every_goal_wake_is_exempt_watch_events_pay(self):
        g = AT.AlertGate(budget=1, window_s=600)
        new = {"kind": "intent-planted", "entity": "membrane::goal:a", "created": True}
        rewake = {"kind": "intent-planted", "entity": "membrane::goal:b", "rewake": True}
        boot = {"kind": "intent-planted", "entity": "membrane::goal:c", "created": False}
        self.assertTrue(g.allow(new, now=0))
        self.assertTrue(g.allow(new, now=1))          # still exempt
        self.assertTrue(g.allow(rewake, now=2))       # a goal's rewake: exempt (#551)
        self.assertTrue(g.allow(boot, now=3))
        self.assertTrue(g.allow({"kind": "field:changed", "entity": "c:a"}, now=4))   # the 1 slot
        self.assertFalse(g.allow({"kind": "field:changed", "entity": "c:b"}, now=5))

    def test_refund_after_an_exempt_event_returns_nothing(self):
        g = AT.AlertGate(budget=1, window_s=600)
        self.assertTrue(g.allow({"kind": "around", "entity": "c:a"}, now=0))
        self.assertTrue(g.allow({"kind": "intent-planted", "created": True}, now=1))
        g.refund()                                   # the exempt one went back
        self.assertFalse(g.allow({"kind": "around", "entity": "c:b"}, now=2))


class TestGoalSpec(unittest.TestCase):
    def setUp(self):
        self.est = _store("spec")
        self.eid = _goal(self.est, "abc123def456", "wire the energy-saving automation")

    def test_every_spelling_the_prompt_hands_out_resolves(self):
        for spec in (f"{MEMBRANE_NS}goal:abc123def456", "m:goal:abc123def456",
                     "goal:abc123def456", "#abc123def456", "abc123def456",
                     "MEMBRANE::GOAL:ABC123DEF456"):
            g = L._resolve_goal(self.est, spec)
            self.assertIsNotNone(g, spec)
            self.assertEqual(g.eid, self.eid)

    def test_an_eid_spec_never_name_matches_another_goal(self):
        _goal(self.est, "ffff00001111", "re-plant of membrane::goal:zzz999 (stale)")
        self.assertIsNone(L._resolve_goal(self.est, "membrane::goal:zzz999"))

    def test_a_closed_goal_says_so(self):
        _goal(self.est, "closed000001", "old", status="fulfilled")
        self.assertIsNone(L._resolve_goal(self.est, "membrane::goal:closed000001"))
        self.assertIn("already fulfilled",
                      L.goal_miss_reason(self.est, "membrane::goal:closed000001"))
        self.assertIn("no goal", L.goal_miss_reason(self.est, "membrane::goal:nope"))


class TestVerdict(unittest.TestCase):
    def test_the_last_line_decides(self):
        P = WA.parse_verdict
        self.assertEqual(P("")[0], "aborted")
        self.assertEqual(P("   ")[0], "aborted")
        self.assertEqual(P("**ignored**: blocked on the operator")[0], "ignored")
        self.assertEqual(P("`ignored: nothing new`")[0], "ignored")
        self.assertEqual(P("Checked it.\n\nDONE pkg repaired rc=0")[0], "done")
        self.assertEqual(P("built the sensor\n**STEP** sensor built, wiring next")[0], "step")
        self.assertEqual(P("BLOCKED on the operator's signature")[0], "blocked")
        self.assertEqual(P("A long essay with no verdict line.")[0], "reacted")
        v, detail = P("x\nBLOCKED: operator signature on fleet law")
        self.assertIn("operator signature", detail)


class TestTaskText(unittest.TestCase):
    def test_the_goal_rides_whole_with_its_ledger_and_budget(self):
        text = "wire the energy-saving automation " + "x" * 400 + " END"
        ev = WA.notify_intent_planted(None, None, "membrane::goal:g", text, created=False,
                                      rewake=True, last_landing="older",
                                      landings=[{"op": "step", "decision": "one"},
                                                {"op": "ignored", "decision": "two"},
                                                {"op": "step", "decision": "three"}])
        task = WA.loop_task_text(ev)
        self.assertIn(" END", task)                      # not cut at 120
        for d in ("one", "two", "three"):
            self.assertIn(d, task)
        self.assertIn("8 tool calls", task)
        for verdict in ("DONE", "STEP", "BLOCKED", "ignored"):
            self.assertIn(verdict, task)

    def test_no_phantom_tools(self):
        for kind in ("intent-planted", "around", "script-error"):
            task = WA.loop_task_text({"kind": kind, "entity": "membrane::goal:x", "text": "t"})
            for ghost in ("entity_run", "read/write/edit", "speak)"):
                self.assertNotIn(ghost, task)


class _Runner:
    """A fake loop turn: scripted replies per call."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, sid, text, agent):
        self.calls.append(text)
        return self.replies.pop(0) if self.replies else {"text": ""}


# #738: the goal-loop pass and its verdicts (the DONE nudge, BLOCKED parking, the landing
# ledger) are gone -- a goal is worked as a graph (tests/test_goal_graph_738.py)


if __name__ == "__main__":
    unittest.main()
