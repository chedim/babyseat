"""A fulfilled goal wakes the watcher loops (2026-10-28), like an answered or
dropped Decision card: one `goal-fulfilled` event into the events pool, the
drainer admits a loop pass, and closing the same goal twice sends one event."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _seat():
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    est._events_pool = WA.WatchEvents()
    return est


def _goal(est, name="ship the suite"):
    return est.add(cls="Goal", name=name, summary="x")["eid"]


def test_a_fulfilled_goal_enqueues_one_event():
    est = _seat()
    eid = _goal(est)
    ev = WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator",
                                  evidence="rc=0 output")
    assert ev["kind"] == "goal-fulfilled"
    [got] = est._events_pool.take()
    assert got["kind"] == "goal-fulfilled" and got["entity"] == eid
    text = WA.loop_task_text(got)
    assert "fulfilled" in text and "ship the suite" in text and "rc=0 output" in text


def test_the_same_close_is_announced_once():
    est = _seat()
    eid = _goal(est)
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator")
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator")
    assert len(est._events_pool.take()) == 1


def test_the_drainer_wakes_a_loop_pass_on_it():
    est = _seat()
    eid = _goal(est)
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="listener")
    [ev] = est._events_pool.take()

    class _Srv:
        entity_store = est
        events_pool = est._events_pool
        loops = WA.LoopRunner(max_loops=3)
    turns = []
    srv = _Srv()
    srv.create_loop_session = lambda agent, title=None: "ses_loop"
    srv.run_loop_turn = lambda sid, text, agent: turns.append((agent, text)) or {"text": "ignored: noted"}
    WA._LoopDrainer(srv).handle(ev)
    assert len(turns) == 1 and "fulfilled" in turns[0][1]


def _chat_seat():
    est = _seat()
    est.sent = []
    est._decision_chat = lambda sid, text: est.sent.append((sid, text))
    return est


def test_a_chat_goal_tells_its_chat_not_the_pool():
    est = _chat_seat()
    eid = _goal(est)
    est.edit(eid, properties={"convs": ["ses_1", "ses_2"]})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="listener")
    assert est._events_pool.take() == []
    [(sid, text)] = est.sent
    assert sid == "ses_2" and text.startswith(f"goal {eid} fulfilled")


def test_a_chatless_goal_goes_to_the_pool():
    est = _chat_seat()
    eid = _goal(est)
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="listener")
    assert est.sent == []
    assert [e["kind"] for e in est._events_pool.take()] == ["goal-fulfilled"]


def test_a_reopened_goal_closed_again_is_announced_twice():
    est = _chat_seat()
    eid = _goal(est)
    est.edit(eid, properties={"convs": ["ses_1"], "fulfilled_ts": 100.0})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator")
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator")   # same close
    assert len(est.sent) == 1
    est.edit(eid, properties={"status": "open"})                            # reopened
    est.edit(eid, properties={"status": "fulfilled", "fulfilled_ts": 200.0})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="operator")
    assert len(est.sent) == 2
