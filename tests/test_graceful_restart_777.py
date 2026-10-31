"""#777: a restart waits only for people's turns. A loop's turn (worker, planner, watch)
is interrupted, and a worker cut off by it goes back to the pool at boot instead of
being finished stuck."""
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.server import TurnRunner  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401


def _srv(busy: dict, loops: int = 0):
    """busy: sid -> its session agent; every one of them mid-turn."""
    rows = {sid: {"id": sid, "agent": agent} for sid, agent in busy.items()}
    r = TurnRunner(None, types.SimpleNamespace(sessions=rows), None, None, None, system="", model="m")
    for sid in busy:
        r._busy[sid] = {"type": "busy"}
    return types.SimpleNamespace(_loop_runner=r, loops=types.SimpleNamespace(running=lambda: loops))


def test_a_loop_turn_does_not_hold_a_restart():
    s = _srv({"ses_w": "worker: membrane::task:x:k1", "ses_t": "watch: f:x.py"}, loops=2)
    with mock.patch.object(RS.time, "sleep", side_effect=AssertionError("held for a loop")):
        assert RS.hold_until_idle(s) == []
    assert RS.busy(s, people_only=True) == []


def test_a_persons_turn_still_holds_it():
    s = _srv({"ses_c": "build", "ses_w": "worker: membrane::task:x:k1"}, loops=1)
    assert RS.busy(s, people_only=True) == ["turn ses_c"]
    calls = []

    def sleep(_):
        calls.append(1)
        s._loop_runner._busy.pop("ses_c")
    with mock.patch.object(RS.time, "sleep", side_effect=sleep):
        assert RS.hold_until_idle(s) == ["turn ses_c"]
    assert calls


def test_busy_without_the_flag_still_lists_everything():
    s = _srv({"ses_c": "build", "ses_w": "worker: membrane::task:x:k1"}, loops=1)
    assert RS.busy(s) == ["turn ses_c", "turn ses_w", "1 loop(s)"]


PLAN = {"tasks": [{"key": "a", "title": "do a"}]}
A = GG.task_eid(GOAL, "a")


def _work(seat, monkeypatch, out):
    """One worker pass over task A with the turn returning `out` (or raising it)."""
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session = lambda agent, title=None: "ses_w"

    def run(sid, text, agent):
        if isinstance(out, Exception):
            raise out
        return out
    seat.run_loop_turn = run
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    r = WA._LoopDrainer(seat)._handle(ev)
    settle(seat)
    return r


def test_a_worker_cut_off_by_a_restart_stays_running_and_boot_re_owes_it(seat, monkeypatch):
    monkeypatch.setitem(RS.RESTART, "requested", True)
    _work(seat, monkeypatch, {"text": ""})
    assert st(seat, A) == "running"
    monkeypatch.setitem(RS.RESTART, "requested", False)
    assert GG.reowe(seat) == 1 and st(seat, A) == "owed"


def test_a_worker_that_raises_during_a_restart_stays_running(seat, monkeypatch):
    monkeypatch.setitem(RS.RESTART, "requested", True)

    _work(seat, monkeypatch, ConnectionError("the seat is going down"))
    assert st(seat, A) == "running"


def test_a_worker_with_a_verdict_during_a_restart_is_finished(seat, monkeypatch):
    monkeypatch.setitem(RS.RESTART, "requested", True)
    _work(seat, monkeypatch, {"text": "DONE made a"})
    assert st(seat, A) == "done"


def test_without_a_restart_a_silent_worker_is_still_stuck(seat, monkeypatch):
    monkeypatch.setitem(RS.RESTART, "requested", False)
    _work(seat, monkeypatch, {"text": ""})
    assert st(seat, A) == "stuck"


def test_a_plan_retries_a_stuck_task_by_listing_it_again(seat):
    write(seat, GOAL, "plan", PLAN)
    assert GG.take(seat, A)
    GG.finish(seat, A, "stuck", "cut off")
    settle(seat)
    assert st(seat, A) == "stuck"
    seat.events_pool.take()
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a, again"}]})
    assert st(seat, A) == "owed"
    assert A in [e["entity"] for e in seat.events_pool.take() if e.get("kind") == "task"]


def test_a_done_task_listed_again_keeps_its_state(seat):
    write(seat, GOAL, "plan", PLAN)
    assert GG.take(seat, A)
    GG.finish(seat, A, "done", "made a")
    settle(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a"}]})
    assert st(seat, A) == "done"


def test_a_plan_drops_a_stuck_task_and_what_it_blocked_moves_on(seat):
    """A wait planned after a push that got stuck: dropping the stuck push (a new one
    done) lets the wait arm -- a stuck task must not block the graph for good."""
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "push"}, {"key": "b", "title": "answer", "after": ["a"]}]})
    assert GG.take(seat, A)
    GG.finish(seat, A, "stuck", "cut off")
    settle(seat)
    b = GG.task_eid(GOAL, "b")
    assert st(seat, b) == "open"
    write(seat, GOAL, "plan", {"drop": ["a"]})
    assert st(seat, A) == "dropped" and st(seat, b) == "owed"
