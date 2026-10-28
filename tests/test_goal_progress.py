"""THE BOARD THAT NEVER MOVED (2026-10-08, the ThinkPhone seat).

The boot queued each open goal ONCE (34 goals -> 35 loop turns, then an
idle hour), and only the operator's `membrane: fulfill #N` frame could
close a goal -- the loops landed real work (pkg repaired rc=0, mosquitto
installed) and the board stayed 'byte for byte' the same.

Pinned: the listeners' `fulfill` (a POST route with the receipt rule: no
evidence, no close), its lane membership (operator + listener, never
plan), and the board's pulse (idle-only, stale-first, batch-capped,
backing off a goal its loops keep ignoring).
"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import server as SRV  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (CLASS_GOAL, EntityStore, MEMBRANE_NS,  # noqa: E402
                               QuantumEntity)


def _store(name):
    return EntityStore(bucket=f"test-goal-progress-{name}", shared=False)


def _goal(est, slug, name, **props):
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
                        properties={"name": name, "status": "open",
                                    **props})
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


class TestFulfillRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from membrane import opencode as OC
        cls.est = _store("route")

        class _Membrane:
            embedder = None
            store = None
            gate_notes = []

        srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                  OC.MembraneOpenAIHandler)
        srv.membrane = _Membrane()
        srv.entity_store = cls.est
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.srv, cls.port = srv, srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, body):
        rq = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/membrane/fulfill",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(rq, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_closes_with_evidence(self):
        eid = _goal(self.est, "ca669752948d", "PKG REPAIR: pkg upgrade")
        code, out = self._post({"spec": "ca669752948d",
                                "evidence": "pkg upgrade rc=0; adb version "
                                            "prints 35.0.2", "by": "watch"})
        self.assertEqual(code, 200, out)
        props = self.est.docs[eid].properties
        self.assertEqual(props["status"], "fulfilled")
        self.assertIn("rc=0", props["fulfilled_evidence"])
        self.assertEqual(props["fulfilled_by"], "watch")

    def test_no_evidence_no_close(self):
        eid = _goal(self.est, "3f6bcad6459e", "MQTT CREDS READ")
        code, out = self._post({"spec": "3f6bcad6459e", "evidence": "done"})
        self.assertEqual(code, 400, out)
        self.assertEqual(self.est.docs[eid].properties["status"], "open")

    def test_unknown_goal(self):
        code, _ = self._post({"spec": "nosuchgoal00",
                              "evidence": "rc=0 on everything"})
        self.assertEqual(code, 404)


class TestFulfillTool(unittest.TestCase):
    def test_aliases_reach_the_route(self):
        seen = {}

        def fake(port, method, path, body=None):
            seen.update(path=path, body=body)
            return {"ok": True, "eid": "membrane::goal:x", "name": "n"}
        orig, M._http = M._http, fake
        try:
            out = M.tool_fulfill({"eid": "x", "receipt": "rc=0 output"}, 0)
        finally:
            M._http = orig
        self.assertEqual(seen["path"], "/membrane/fulfill")
        self.assertEqual(seen["body"]["spec"], "x")
        self.assertEqual(seen["body"]["evidence"], "rc=0 output")
        self.assertIn("fulfilled", out)

    def test_lanes(self):
        self.assertIn("fulfill", SRV.LISTENER_TOOLS)
        self.assertIn("fulfill", SRV.OPERATOR_TOOLS)
        self.assertNotIn("fulfill", SRV.PLAN_TOOLS)
        self.assertIn("fulfill", M.TOOL_IMPLS)
        self.assertIn("fulfill", {t["name"] for t in M.TOOLS})

    def test_task_text_names_the_close(self):
        task = WA.loop_task_text({"kind": "intent-planted",
                                  "entity": "membrane::goal:x",
                                  "text": "wire HA"})
        self.assertIn("fulfill", task)


class TestBoardPulse(unittest.TestCase):
    def setUp(self):
        self.est = _store(f"pulse-{time.time_ns()}")
        self.srv = _Srv(self.est)
        self.now = time.time()

    def test_stale_open_goals_requeue_oldest_first_capped(self):
        for i in range(5):
            _goal(self.est, f"g{i}", f"goal {i}",
                  intent_planted_ts=self.now - 4000 - i * 10)
        n = WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800,
                                 batch=3)
        self.assertEqual(n, 3)
        evs = self.srv.events_pool.items
        self.assertEqual([e["entity"] for e in evs],
                         [f"{MEMBRANE_NS}goal:g{i}" for i in (4, 3, 2)])
        self.assertTrue(all(e["rewake"] for e in evs))
        self.assertIn("still open", WA.loop_task_text(evs[0]))

    def test_fresh_and_closed_goals_skip(self):
        _goal(self.est, "fresh", "fresh", intent_planted_ts=self.now - 60)
        _goal(self.est, "done", "done", status="fulfilled",
              intent_planted_ts=0)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)

    def test_running_loop_holds(self):
        _goal(self.est, "old", "old", intent_planted_ts=0)
        self.srv.loops._held.add(f"{MEMBRANE_NS}goal:other")
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)

    def test_busy_pool_holds(self):
        _goal(self.est, "old", "old", intent_planted_ts=0)
        self.srv.events_pool.put({"kind": "around"})
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)

    def test_ignored_streak_backs_off(self):
        lands = [{"op": "ignored"}] * 2       # x4 wait
        _goal(self.est, "parked", "parked", landings=lands,
              intent_planted_ts=self.now - 3 * 1800)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)
        self.assertEqual(WA.rewake_open_goals(
            self.srv, now=self.now + 1800, min_age_s=1800), 1)

    def test_last_landing_rides_the_task(self):
        _goal(self.est, "cont", "cont", intent_planted_ts=0,
              landings=[{"op": "reacted",
                         "decision": "pkg repaired; mosquitto next"}])
        WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800)
        task = WA.loop_task_text(self.srv.events_pool.items[0])
        self.assertIn("mosquitto next", task)

    def test_the_wake_restamps(self):
        eid = _goal(self.est, "once", "once", intent_planted_ts=0)
        WA.rewake_open_goals(self.srv, now=self.now, min_age_s=1800)
        self.srv.events_pool.take()
        self.assertGreater(self.est.docs[eid].properties[
            "intent_planted_ts"], self.now - 5)
        self.assertEqual(WA.rewake_open_goals(self.srv, min_age_s=1800), 0)

    def test_step_continues_at_the_next_beat(self):
        # a STEP is progress: the goal does not wait out the 30-min gap
        _goal(self.est, "going", "going", landings=[{"op": "step"}],
              intent_planted_ts=self.now - 60)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 1)

    def test_step_streak_cap_restores_the_gap(self):
        n = WA._STEP_STREAK_CAP
        _goal(self.est, "runaway", "runaway", landings=[{"op": "step"}] * n,
              intent_planted_ts=self.now - 60)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)
        _goal(self.est, "ok", "ok", landings=[{"op": "step"}] * (n - 1),
              intent_planted_ts=self.now - 60)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 1)

    def test_quiet_pass_after_a_step_backs_off_again(self):
        _goal(self.est, "stuck", "stuck",
              landings=[{"op": "step"}, {"op": "ignored"}],
              intent_planted_ts=self.now - 60)
        self.assertEqual(WA.rewake_open_goals(self.srv, now=self.now,
                                              min_age_s=1800), 0)


if __name__ == "__main__":
    unittest.main()
