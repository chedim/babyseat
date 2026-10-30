"""A GOAL IS PICKED UP ONCE (2026-10-27, #597 built four times).

Memo planted a goal for the bugfix list and kept working the list in its
chat; the planted notification woke a loop that worked the same list, and
the board's pulse woke it again every half hour. Nothing on the goal said
someone had it, so #597 came back as two PRs and two more worktrees.

Pinned: a loop that takes a goal marks it picked up (who, when); a goal
notification sent before the goal's latest pickup is dropped instead of
running a second pass; the pool keeps one queued notification per goal;
and every chat's banner shows who picked the goal up.
"""

import os
import sys
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import conv_banner as CB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_GOAL, EntityStore, MEMBRANE_NS,  # noqa: E402
                               QuantumEntity)


def _store():
    return EntityStore(bucket=f"test-goal-pickup-{time.time_ns()}", shared=False)


def _goal(est, slug, **props):
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
                        properties={"name": slug, "status": "open", **props})
    est.upsert(ent, embedder=None)
    return ent.eid


def _srv(est):
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(),
                                loops=WA.LoopRunner(max_loops=3), turns=[])
    srv.create_loop_session = lambda agent, title=None: "ses_x"
    srv.run_loop_turn = lambda sid, text, agent: srv.turns.append(text) or {"text": "STEP moved"}
    return srv


class TestPickup(unittest.TestCase):
    # #738: a goal is worked by its PLANNER (a planted notification asks it; the planner's
    # pass is the one that takes the goal)
    @staticmethod
    def _planner_ev(eid):
        return {"kind": "llm-call", "lane": "planner", "entity": eid, "prompt": "plan the goal: planted",
                "depth": 1, "ts": time.time()}

    def test_the_planner_marks_the_goal_it_takes(self):
        est = _store()
        srv = _srv(est)
        eid = _goal(est, "list")
        t0 = time.time()
        WA._LoopDrainer(srv).handle(self._planner_ev(eid))
        p = est.docs[eid].properties
        self.assertTrue(p["picked_up_by"].startswith("planner"))
        self.assertGreaterEqual(p["picked_up_ts"], t0)
        self.assertEqual(len(srv.turns), 1)

    def test_a_notification_older_than_the_pickup_asks_no_second_plan(self):
        est = _store()
        srv = _srv(est)
        eid = _goal(est, "list")
        old = {"kind": "intent-planted", "entity": eid, "text": "list", "ts": time.time() - 5}
        WA._LoopDrainer(srv).handle(self._planner_ev(eid))
        self.assertEqual(WA._LoopDrainer(srv).handle(dict(old)), "skip")   # queued before that pass
        self.assertEqual(WA._LoopDrainer(srv).handle({**old, "ts": time.time() + 1, "created": True}),
                         "planned")                                       # a later plant still asks
        self.assertEqual(len(srv.turns), 1)

    def test_a_notification_sent_during_the_planner_pass_asks_no_second_plan(self):
        est = _store()
        srv = _srv(est)
        eid = _goal(est, "list")
        during = []

        def turn(sid, text, agent):            # the pulse/a re-plant lands mid-pass
            srv.turns.append(text)
            during.append({"kind": "intent-planted", "entity": eid, "text": "list",
                           "ts": time.time()})
            return {"text": "PLANNED two tasks"}
        srv.run_loop_turn = turn
        WA._LoopDrainer(srv).handle(self._planner_ev(eid))
        self.assertEqual(WA._LoopDrainer(srv).handle(during[0]), "skip")
        self.assertEqual(len(srv.turns), 1)

    def test_the_pool_keeps_one_notification_per_goal(self):
        pool = WA.WatchEvents()
        pool.put({"kind": "intent-planted", "entity": "membrane::goal:a", "ts": 1})
        pool.put({"kind": "intent-planted", "entity": "membrane::goal:b", "ts": 2})
        pool.put({"kind": "intent-planted", "entity": "membrane::goal:a", "ts": 3, "rewake": True})
        evs = pool.take()
        self.assertEqual([(e["entity"], e["ts"]) for e in evs],
                         [("membrane::goal:a", 3), ("membrane::goal:b", 2)])

    def test_the_banner_says_who_is_working_it_and_who_last_did(self):
        est = _store()
        ts = time.mktime((2026, 10, 27, 9, 31, 0, 0, 0, -1))
        end = time.mktime((2026, 10, 27, 9, 36, 0, 0, 0, -1))
        run = _goal(est, "run", picked_up_by="loop-1", picked_up_ts=ts)
        _goal(est, "done", picked_up_by="loop-2", picked_up_ts=ts, picked_up_end_ts=end)
        _goal(est, "dead", picked_up_by="loop-3", picked_up_ts=ts)    # its seat died mid-pass
        loops = WA.LoopRunner(max_loops=3)
        loops.admit(WA.LoopDesc("watch", eid=run))
        srv = types.SimpleNamespace(entity_store=est, loops=loops)
        items = {i.text: i.src for i in CB.open_items(srv, "c1")}
        self.assertIn("being worked by loop-1 since 09:31", items["run"])
        self.assertIn("last worked by loop-2 at 09:36", items["done"])
        self.assertIn("last worked by loop-3 at 09:31", items["dead"])

    def test_a_goal_nobody_took_says_nothing(self):
        est = _store()
        _goal(est, "free")
        items = {i.text: i.src for i in CB.open_items(types.SimpleNamespace(entity_store=est), "c1")}
        self.assertNotIn("worked", items["free"])

    def test_a_failed_mark_is_logged(self):
        srv = _srv(_store())
        eid = _goal(srv.entity_store, "list")
        srv.entity_store.upsert = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            WA._mark_picked_up(srv, eid, "loop-1")
        self.assertIn("disk full", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
