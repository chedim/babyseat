"""Loop turns run in parallel up to the lanes' approved sizes (2026-10-31: the one drainer
thread ran every turn to its end before taking the next -- WORKERS = 3 and PLANNERS = 2
were checked at admission but only one turn was ever in flight)."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import watch_agents as WA  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, write  # noqa: E402,F401

PLAN = {"tasks": [{"key": "a", "title": "do a"}, {"key": "c", "title": "do c"}]}


def test_two_ready_tasks_run_their_turns_at_the_same_time(seat):
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session = lambda agent, title=None: "ses_" + agent[-6:]
    both = threading.Barrier(2, timeout=5)
    done = []

    def run(sid, text, agent):
        both.wait()                         # passes only if the two turns overlap
        done.append(agent)
        return {"text": "DONE ok"}
    seat.run_loop_turn = run
    write(seat, GOAL, "plan", PLAN)
    evs = [e for e in seat.events_pool.take() if e.get("kind") == "task"]
    assert len(evs) == 2
    dr = WA._LoopDrainer(seat)
    for ev in evs:
        dr.dispatch(ev)
    for _ in range(100):
        if len(done) == 2:
            break
        time.sleep(0.05)
    settle(seat)
    assert len(done) == 2, "the second turn waited for the first"


def test_a_requeue_is_not_a_wake_outcome(seat, monkeypatch):
    from membrane import wakes as WK
    logged = []
    monkeypatch.setattr(WK, "log", lambda srv, ev, out, **k: logged.append(out))
    dr = WA._LoopDrainer(seat)
    monkeypatch.setattr(dr, "_handle", lambda ev: "requeue")
    dr.handle({"kind": "task", "entity": "x"})
    assert "requeue" not in logged
