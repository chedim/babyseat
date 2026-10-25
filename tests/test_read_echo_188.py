"""ISSUE #188 (red first): "dropped fieldwatch probe leaves its watch lane
loop running". The seat's audit says the probe was never the loop's source:
the 'watch: membrane::Component:i' passes were all "a reading changed on
'membrane::Component:i' (its getter ran) New values: beats=770, hits=713 ..."
-- getter-applied events. An open goal's loop read the eye every pass; the
read refreshed the getter as a JOB (jobs._read), the job delivered the fresh
values back to the reader -- and the same refresh ALSO posted getter-applied,
waking a second loop on the read entity. A counter (beats) changes on every
read, so 'a same reading is not news' never held it: every read was a new
pass on the eye, and dropping every board watch changed nothing.

The reader's own read is answered to the reader (the job's event); it is not
news for another loop. The field's own reads still post."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402
from membrane import jobs as J  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

EYE = MEMBRANE_NS + "Component:i"
GOAL_AGENT = "watch: membrane::goal:1394b99c65ac"


def _wait(cond, s=5.0):
    t = time.time()
    while time.time() - t < s:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


def _world():
    """The eye: a getter whose reading is a counter that ticks on every read."""
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    eye = QuantumEntity(eid=EYE, cls="Component", properties={
        "name": "i",
        "getter_script": ("n = int((membrane.me.properties.get('script_data') or {}).get('beats') or 0)\n"
                          "result = {'state': {'beats': n + 1}}"),
        "script_data": {"beats": 769},
        "watch_fields": [{"verb": "vel", "arg": 1}]})   # watched: only then does a changed reading post (#549)
    est.docs[eye.eid] = eye
    pool = _Pool()
    srv = type("S", (), {"entity_store": est, "events_pool": pool, "efield": None, "membrane": None})()
    est._srv = srv
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), lambda b: {}, lambda *a: "",
                      system="", model="m", tools=None)
    r._turn_watchdog = lambda *a, **k: None
    r.srv = srv
    r._agents = {"ses_goal": GOAL_AGENT}       # the reader: a goal's loop pass
    return r, srv, est, pool


class TestAReadIsNotAnEcho(unittest.TestCase):
    def _read(self, r, pool):
        WA._getter_last_ts.clear()             # the getter's 5 s pacing: each pass is minutes apart live
        n = len([e for e in pool.items if e["kind"] == "job-done"])
        J.dispatch(r, "ses_goal", "entity_read", {"spec": "Component:i"}, "c1", lambda a: "stored")
        self.assertTrue(_wait(lambda: len([e for e in pool.items if e["kind"] == "job-done"]) > n))

    def test_a_loops_read_of_a_ticking_getter_wakes_no_loop_on_the_read_entity(self):
        r, srv, est, pool = _world()
        for _ in range(3):                     # three goal passes, three reads, the counter ticks each time
            self._read(r, pool)
        self.assertEqual(est.docs[EYE].properties["script_data"]["beats"], 772)
        kinds = [e["kind"] for e in pool.items]
        self.assertEqual(kinds.count("job-done"), 3)               # the reader got its fresh values ...
        self.assertEqual({e["entity"] for e in pool.items if e["kind"] == "job-done"},
                         {"membrane::goal:1394b99c65ac"})          # ... on its OWN pass
        self.assertNotIn("getter-applied", kinds,                  # ... and no second loop wakes on the eye
                         [e for e in pool.items if e["kind"] == "getter-applied"])

    def test_the_drainer_never_runs_a_pass_on_the_read_entity(self):
        r, srv, est, pool = _world()
        self._read(r, pool)
        ran = []
        srv.loops = WA.LoopRunner()
        srv.loops.titles = []
        srv.create_loop_session = lambda agent, title=None: "ses_" + str(len(ran))
        srv.run_loop_turn = lambda sid, text, agent: (ran.append(agent), {"text": "ignored: tick"})[1]
        drainer = WA._LoopDrainer(srv)
        for ev in list(pool.items):
            drainer._handle(ev)
        self.assertNotIn(WA.loop_agent_name("watch", EYE), ran, ran)   # no pass on the eye ...
        self.assertIn(GOAL_AGENT, ran, ran)                           # ... the reader's own pass gets the values

    def test_a_define_first_fire_is_answered_to_the_definer_only(self):
        r, srv, est, pool = _world()
        got = []
        orig = J.deliver
        try:
            J.deliver = lambda *a, **k: got.append(a) or "loop"
            J._define(r, "ses_goal", "entity_edit", {"spec": "Component:i", "getter_script": "x"},
                      lambda a: "ok: edited")
            self.assertTrue(_wait(lambda: bool(got)))
        finally:
            J.deliver = orig
        self.assertNotIn("getter-applied", [e["kind"] for e in pool.items])

    def test_the_fields_own_read_still_posts_the_change(self):
        r, srv, est, pool = _world()
        WA._getter_last_ts.clear()
        WA.entity_read_back(srv, est.docs[EYE], by="field")
        self.assertEqual([e["kind"] for e in pool.items], ["getter-applied"])


if __name__ == "__main__":
    unittest.main()
