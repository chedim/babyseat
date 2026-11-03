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
    log = {"left": total, "turns": [], "wiped": 0, "keeps": []}

    def create(agent, title=None, keep=False):
        """Like opencode.loop_session (#570): one session per entity, wiped on reuse unless keep."""
        log["keeps"].append(keep)
        if not keep:
            log["wiped"] += 1
        return "ses_task"

    def run(sid, text, agent, **kw):
        k = min(WORKER_CEILING, log["left"])
        log["left"] -= k
        log["turns"].append({"sid": sid, "text": text, "calls": k, "kw": kw})
        if log["left"] > 0:
            return {"text": "", "capped": {"session": f"sdk-{sid}", "calls": k,
                                           "progress": "- bash chunk %d -> completed: ok" % len(log["turns"])}}
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


def test_45_calls_run_as_3_chunks_of_at_most_20_and_keep_their_rows(seat):
    """Memo on #913: loop_session wiped the earlier chunks' rows, so /membrane/stats counted only the
    last chunk. Later chunks reuse the task's session with keep=True."""
    log = _go(seat)
    assert [t["calls"] for t in log["turns"]] == [20, 20, 5]
    assert log["keeps"][1:] == [True, True]


def test_later_chunks_do_not_resume_the_first(seat):
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

    def create(agent, title=None, keep=False):
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


def test_a_redo_is_not_told_to_skip_the_work(seat):
    """Memo on #913: nothing cleared `progress`, so a send-back was told "don't redo what's done"."""
    _go(seat)
    assert not seat.entity_store.docs[A].properties.get("progress")
    assert "earlier session" not in GG.worker_text(seat.entity_store, A)


def test_a_restart_between_chunks_keeps_that_chunks_progress(seat, monkeypatch):
    """Memo on #913: the restart check returned before the note was written."""
    log = _chunked_turn(seat)
    calls = {"n": 0}

    def restarting():
        calls["n"] += 1
        return True                                   # the seat restarts right after the first chunk
    monkeypatch.setattr(WA, "_restarting", restarting)
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    assert len(log["turns"]) == 1
    prog = seat.entity_store.docs[A].properties.get("progress") or []
    assert prog and prog[0].startswith("session 1:")


def test_the_progress_note_keeps_the_first_calls_too():
    """Memo on #913: the clips kept the end and dropped the start (the worktree, the branch)."""
    from membrane import opencode as OC
    parts = [{"type": "tool", "tool": "bash", "state": {"input": {"cmd": f"step {i} " + "z" * 150},
                                                      "status": "completed", "output": "ok " * 60}}
             for i in range(1, 21)]
    note = OC.chunk_progress(parts, "now writing the tests")
    assert "step 1 " in note and "step 20 " in note
    assert "Its last words: now writing the tests" in note
    assert len(note) < 3000 + 200


def test_the_task_text_keeps_the_first_sessions_setup():
    est = WA_seat_est()
    est.docs[A].properties["progress"] = ["session 1:\nworktree /tmp/wt-x on branch b" + "y" * 3000] + \
        [f"session {i}:\n" + "w" * 3000 for i in range(2, 6)]
    text = GG.worker_text(est, A)
    assert "worktree /tmp/wt-x on branch b" in text
    assert "session 5:" in text


def WA_seat_est():
    from membrane.entities import EntityStore, QuantumEntity
    import tempfile
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    est.upsert(QuantumEntity(eid=A, cls="Task", properties={"title": "do a", "goal": GOAL}), embedder=None)
    return est


def test_loop_session_keeps_rows_only_when_asked():
    from types import SimpleNamespace
    from membrane import opencode as OC
    deleted = []

    class Store:
        _lock = __import__("threading").Lock()
        sessions = {"ses_t": {"id": "ses_t", "agent": "worker: x", "time": {"updated": 1}}}

        def touch(self, sid):
            pass
    srv = SimpleNamespace(_loop_runner=SimpleNamespace(sessions=Store(),
                                                       messages=SimpleNamespace(delete=deleted.append)))
    assert OC.loop_session(srv, "worker: x", keep=True) == "ses_t"
    assert deleted == []
    assert OC.loop_session(srv, "worker: x") == "ses_t"
    assert deleted == ["ses_t"]


def test_a_worker_session_replays_no_earlier_chunk():
    """Memo on #913: with the rows kept, TurnRunner._prior_history replayed the previous chunk, so
    chunk 3's prompt held the task twice. A worker's task text carries what it needs."""
    import membrane.server as MS
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), None, None, system="", model="m")
    w = r.sessions.create(title="worker: membrane::task:x:k1")
    r.sessions.sessions[w["id"]]["agent"] = "worker: membrane::task:x:k1"
    c = r.sessions.create(title="chat")
    for sid in (w["id"], c["id"]):
        r.messages.append(sid, {"id": "msg_u1", "role": "user"}, [{"type": "text", "text": "You are a WORKER on task x"}])
        r.messages.append(sid, {"id": "msg_a1", "role": "assistant"}, [{"type": "text", "text": "working on it"}])
    assert r._prior_history(w["id"], "") == []
    assert r._prior_history(c["id"], "")                 # a person's chat still replays


def test_a_re_owed_task_keeps_its_rows_and_counts_on(seat):
    """Memo on #913: the first session of a re-owed task wiped its rows, and the count restarted."""
    log = _chunked_turn(seat, total=25)
    write(seat, GOAL, "plan", PLAN)
    est = seat.entity_store
    est.docs[A].properties["progress"] = ["session 1:\n- bash step -> completed: ok"]
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    assert log["keeps"][0] is True                          # the re-owed task's first session keeps the rows
    assert "earlier session" in log["turns"][0]["text"]
