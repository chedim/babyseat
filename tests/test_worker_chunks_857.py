"""#857: a worker answer runs in chunks of at most 20 tool calls, each a NEW SDK
session (no resume); the task's state comes from the field. Chat and other lanes
are unchanged. RED first."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from test_claude_agent_384 import key_fixture  # noqa: E402
from test_claude_agent_checkin_425 import scripted_sdk  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

A = GG.task_eid(GOAL, "a")
PLAN = {"tasks": [{"key": "a", "title": "do a"}]}
WORKER_CEILING = 20


class _SDK(unittest.TestCase):
    def setUp(self):
        self.sdk = scripted_sdk([[("tools", 45), ("text", "done")]])
        self._p = [mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), key_fixture()]
        for p in self._p:
            p.start()
        CA.reset_stats()

    def tearDown(self):
        DR.context(None)
        for p in reversed(self._p):
            p.stop()

    def _run(self, lane):
        DR.context(lane=lane)
        return list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                                  stream=False, port=9))


class TestCeilingPerLane(_SDK):
    def test_a_worker_answer_stops_at_20_calls_and_is_marked_capped(self):
        items = self._run("worker")
        self.assertEqual(len(self.sdk.tool_runs), WORKER_CEILING)
        caps = [i for i in items if i[0] == "capped"]
        self.assertTrue(caps, "no cap mark at the worker ceiling")
        self.assertEqual(caps[0][1]["calls"], WORKER_CEILING)

    def test_the_chat_lane_ceiling_is_unchanged(self):
        self.assertEqual(CA.MAX_TOOL_CALLS_PER_ANSWER, 2048)
        self._run("chat")
        self.assertEqual(len(self.sdk.tool_runs), 45)

    def test_another_lane_is_unchanged(self):
        self._run("loop")
        self.assertEqual(len(self.sdk.tool_runs), 45)


def _chunked_turn(seat, total=45):
    """run_loop_turn stand-in: a 45-call answer, at most 20 calls per session; a
    session that stops at the ceiling comes back capped, the last one says DONE."""
    log = {"left": total, "turns": []}
    n = [0]

    def create(agent, title=None):
        n[0] += 1
        return f"ses_{n[0]}"

    def run(sid, text, agent, **kw):
        k = min(WORKER_CEILING, log["left"])
        log["left"] -= k
        log["turns"].append({"sid": sid, "text": text, "calls": k, "kw": kw})
        if log["left"] > 0:
            return {"text": "", "capped": {"session": f"sdk-{sid}", "calls": k}}
        return {"text": f"DONE all {total} landed"}
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session, seat.run_loop_turn = create, run
    return log


def _go(seat):
    log = _chunked_turn(seat)
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    return log


def test_45_calls_run_as_3_sessions_of_at_most_20(seat):
    log = _go(seat)
    assert [t["calls"] for t in log["turns"]] == [20, 20, 5]
    assert len({t["sid"] for t in log["turns"]}) == 3


def test_later_sessions_do_not_resume_the_first(seat):
    log = _go(seat)
    assert len(log["turns"]) == 3
    for t in log["turns"]:
        assert not t["kw"], "a resume/session argument was passed"
        assert "sdk-" not in t["text"]                 # no old session id carried in
    assert A in log["turns"][1]["text"]                # the task as worker_text builds it


def test_the_task_finishes_with_its_result_on_the_entity(seat):
    _go(seat)
    p = seat.entity_store.docs[A].properties
    assert st(seat, A) == "done"
    assert p.get("result")
    assert "45" in (p.get("receipt") or "")


def test_a_worker_keeps_the_loop_toolset():
    """Workers were "loop" lane and got _LOOP_HIDDEN; the new "worker" lane must keep it."""
    DR.context(lane="worker")
    try:
        worker = set(CA.tool_names())
    finally:
        DR.context(None)
    DR.context(lane="loop")
    try:
        loop = set(CA.tool_names())
    finally:
        DR.context(None)
    assert worker == loop


def test_chunks_stop_at_the_answers_old_ceiling(seat, monkeypatch):
    """A task that never finishes doesn't chunk forever: all its chunks together stay within
    MAX_TOOL_CALLS_PER_ANSWER."""
    monkeypatch.setattr(CA, "MAX_TOOL_CALLS_PER_ANSWER", 60)
    log = _chunked_turn(seat, total=10_000)
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    assert sum(t["calls"] for t in log["turns"]) <= 60
    assert len(log["turns"]) == 3


def test_the_next_chunk_is_told_what_the_last_one_did(seat):
    """Without it a chunk starts the task over, redoes the first 20 calls, and is cut again."""
    log = {"left": 45, "turns": []}
    n = [0]

    def create(agent, title=None):
        n[0] += 1
        return f"ses_{n[0]}"

    def run(sid, text, agent, **kw):
        k = min(WORKER_CEILING, log["left"])
        log["left"] -= k
        log["turns"].append(text)
        if log["left"] > 0:
            return {"text": "", "capped": {"session": f"sdk-{sid}", "calls": k,
                                           "progress": "- bash step %d -> completed: ok" % len(log["turns"])}}
        return {"text": "DONE all landed"}
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session, seat.run_loop_turn = create, run
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    assert "earlier session" not in log["turns"][0]
    assert "step 1" in log["turns"][1] and "Continue from there" in log["turns"][1]
    assert "step 1" in log["turns"][2] and "step 2" in log["turns"][2]
