"""TOOLS THAT DON'T BLOCK (2026-10-12, the operator: "they're blocking the fun
stuff ... finishing such a task should be treated almost like an event"):
through the real turn loop -- a slow run becomes a job and its result comes
back as a chat continuation; a fast one stays inline; reads answer from the
store and refresh as a job; a broken script is refused at the door; depth caps
the chain; a loop's job wakes a loop pass."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402
from membrane import jobs as J  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _wait(cond, s=5.0):
    t = time.time()
    while time.time() - t < s:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class _Ladder:
    """Round 1: one tool call; afterwards: a text answer naming what it saw last."""
    def __init__(self, tool, args):
        self.tool, self.args, self.seen = tool, args, []

    def __call__(self, body):
        msgs = body.get("messages") or []
        last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
        self.seen.append(last_user)
        if msgs and msgs[-1].get("role") == "user" and "job #" not in last_user and len(self.seen) == 1:
            return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": self.tool, "arguments": json.dumps(self.args)}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"role": "assistant", "content": f"saw: {last_user[-60:]}"},
                             "finish_reason": "stop"}]}


def _runner(ladder, executor):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder, executor,
                      system="", model="m", tools=None)
    r._turn_watchdog = lambda *a, **k: None
    r.srv = type("S", (), {"entity_store": None})()
    return r


class TestRuns(unittest.TestCase):
    def setUp(self):
        self._d = mock.patch.object(J, "DEADLINE_S", 0.2)
        self._d.start()

    def tearDown(self):
        self._d.stop()

    def test_a_slow_run_is_a_job_and_its_result_comes_back_as_an_event(self):
        def ex(name, args, cid):
            time.sleep(0.6)
            return "rc=0 ran: the scan found 3 devices"
        lad = _Ladder("entity_link", {"commands": "scan-the-bus"})
        r = _runner(lad, ex)
        out = r.run("s1", "[HA user: Dmitrii]\nscan the bus")
        tool_out = [p for row in r.messages.list("s1") for p in row.get("parts") or [] if p.get("type") == "tool"
                    and p.get("tool") == "entity_link"][0]["state"]["output"]
        self.assertIn("NOT finished", tool_out)
        self.assertTrue(_wait(lambda: any("job #" in s and "3 devices" in s for s in lad.seen)), lad.seen)
        rows = r.messages.list("s1")
        cards = [p for row in rows for p in row.get("parts") or [] if p.get("type") == "tool" and p.get("tool") == "job"]
        self.assertEqual(len(cards), 1)                                    # a tool box ...
        self.assertIn("3 devices", cards[0]["state"]["output"])
        self.assertFalse(any("[membrane] job #" in "".join(p.get("text") or "" for p in row.get("parts") or [])
                             for row in rows if (row.get("info") or {}).get("role") == "user"))   # ... no bubble

    def test_a_fast_run_stays_inline(self):
        lad = _Ladder("entity_link", {"commands": "true"})
        r = _runner(lad, lambda n, a, c: "rc=0 ran: quick")
        r.run("s2", "[HA user: Dmitrii]\nquick one")
        tool_out = [p for row in r.messages.list("s2") for p in row.get("parts") or [] if p.get("type") == "tool"
                    ][0]["state"]["output"]
        self.assertEqual(tool_out, "rc=0 ran: quick")


class TestMidTurn(unittest.TestCase):
    def test_a_job_landing_mid_turn_joins_the_next_round(self):
        r = _runner(lambda b: {}, lambda *a: "")
        done = threading.Event()
        r._queue = {"s": [{"qid": "q1", "text": "", "user_info": {"jobText": "[membrane] job #3 finished: ok"},
                           "done": done}]}
        said = r._take_interjections("s", "a1")
        self.assertEqual(said[0][0], "[membrane] job #3 finished: ok")


class TestDelivery(unittest.TestCase):
    def test_depth_caps_the_chain(self):
        r = _runner(lambda b: {}, lambda *a: "")
        self.assertEqual(J.deliver(r, "s", 1, "x", "y", 1.0, depth=J.MAX_DEPTH), "dropped: depth")

    def test_a_loops_job_wakes_a_loop_pass(self):
        r = _runner(lambda b: {}, lambda *a: "")
        pool = type("P", (), {"items": [], "put": lambda s, e: s.items.append(e)})()
        r.srv = type("S", (), {"events_pool": pool, "entity_store": None})()
        r._agents = {"ses_loop": "watch: membrane::bat"}
        self.assertEqual(J.deliver(r, "ses_loop", 7, "exec link scan", "rc 0", 9.0, depth=0,
                                   entity="membrane::bat"), "loop")
        ev = pool.items[0]
        self.assertEqual((ev["kind"], ev["job"], ev["job_depth"]), ("job-done", 7, 1))
        self.assertIn("your job #7 finished", WA.loop_task_text(ev))


class TestReadsAndDefines(unittest.TestCase):
    def test_a_read_answers_from_the_store_and_refreshes_as_a_job(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component",
                          properties={"name": "bat", "getter_script": "result = {'state': {'pct': 61}}",
                                      "script_data": {"pct": 64}})
        est.docs[e.eid] = e
        r = _runner(lambda b: {}, lambda *a: "")
        r.srv = type("S", (), {"entity_store": est, "events_pool": None, "efield": None})()
        WA._getter_last_ts.clear()                 # the getter's 5 s pacing: another test may have just read it
        got = []
        seen_args = []
        with mock.patch.object(J, "deliver", lambda *a, **k: got.append(a)):
            out = J.dispatch(r, "s", "entity_read", {"spec": "Component:bat"}, "c1",
                             lambda a: (seen_args.append(a), "stored: pct 64")[1])
            self.assertTrue(seen_args[0].get("nofire"))
            self.assertIn("STORED values", out)
            self.assertTrue(_wait(lambda: bool(got)))
        self.assertIn("pct: 64 -> 61", got[0][4])

    def test_a_getter_returning_its_me_handle_leaks_no_object_repr_into_the_job(self):
        # issue #152: a getter whose state carried the script's `membrane.me`
        # handle posted "me: <..._Me object at 0x..> -> <..._Me object at 0x..>"
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component",
                          properties={"name": "bat",
                                      "getter_script": "result = {'state': {'pct': 61, 'me': membrane.me}}",
                                      "script_data": {"pct": 64}})
        est.docs[e.eid] = e
        r = _runner(lambda b: {}, lambda *a: "")
        r.srv = type("S", (), {"entity_store": est, "events_pool": None, "efield": None})()
        got = []

        def read():
            WA._getter_last_ts.clear()
            n = len(got)
            with mock.patch.object(J, "deliver", lambda *a, **k: got.append(a)):
                J.dispatch(r, "s", "entity_read", {"spec": "Component:bat"}, "c1", lambda a: "stored")
                _wait(lambda: len(got) > n, s=1.5)
        read()
        self.assertTrue(got)
        body = got[0][4]
        self.assertIn("pct: 64 -> 61", body)
        self.assertNotIn("object at 0x", body)
        self.assertNotIn("_Me", body)
        sd = est.docs[e.eid].properties["script_data"]
        self.assertEqual(sd["me"], e.eid)              # the readable stand-in: the entity id
        json.dumps(sd)                                 # the stored values stay json-safe
        read()                                         # the same handle again is no news
        self.assertEqual(len(got), 1, got[1:] and got[1][4])

    def test_a_script_that_cant_compile_is_refused_at_the_door(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:d", cls="Component", properties={"name": "d"})
        est.docs[e.eid] = e
        res = est.edit(e.eid, getter_script="x = 1\nreturn x")
        self.assertFalse(res["ok"])
        self.assertIn("module code", res["error"])


class TestTheAlias(unittest.TestCase):
    def test_mcp_tool_prefix(self):
        self.assertEqual(TS.canonical("MCP_TOOL__membrane__entity_edit", {})[0], "entity_edit")


if __name__ == "__main__":
    unittest.main()
