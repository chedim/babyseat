"""Every plan ends with a message into the chat (operator, 2026-10-31: "force all plans to
end with turn-starting messages into chat"): when a goal's graph has nothing left moving
(no task open/owed/running, no wait open/armed, no card open), the chat hears it once,
with the receipts, and its turn decides."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from test_goal_graph_738 import GOAL, _drainer, seat, settle, st, write  # noqa: E402,F401

A, B = GG.task_eid(GOAL, "a"), GG.task_eid(GOAL, "b")
CI = GG.wait_eid(GOAL, "ci")


def _planner(seat, reply="UNCHANGED"):
    dr, _ = _drainer(seat, reply)
    dr._handle({"kind": "llm-call", "entity": GOAL, "prompt": "plan the goal: x", "lane": "planner",
                "caller": "trigger:x", "depth": 1})
    settle(seat)


def _done(seat, task, receipt):
    assert GG.take(seat, task)
    GG.finish(seat, task, "done", receipt)
    settle(seat)


def test_a_finished_graph_tells_the_chat_once_with_its_receipts(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "push the fix"}]})
    _done(seat, A, "pushed abc123")
    seat.told.clear()
    _planner(seat)
    assert len(seat.told) == 1
    ent, line = seat.told[0]
    assert ent == GOAL and "push the fix" in line and "pushed abc123" in line and "close" in line
    assert st(seat, GOAL) == "open"
    _planner(seat)                                   # the same graph again: no second message
    assert len(seat.told) == 1


def test_a_graph_still_moving_tells_no_one(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a"},
                                         {"key": "b", "title": "do b", "after": ["a"]}]})
    _done(seat, A, "did a")
    seat.told.clear()
    _planner(seat)
    assert st(seat, B) == "owed" and not seat.told


def test_a_stuck_graph_tells_the_chat_too(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a"}]})
    assert GG.take(seat, A)
    GG.finish(seat, A, "stuck", "no worktree")
    settle(seat)
    seat.told.clear()
    _planner(seat, "UNCHANGED waiting")
    assert len(seat.told) == 1 and "no worktree" in seat.told[0][1]


def test_a_wait_that_ends_the_graph_tells_the_chat(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "push"}],
                               "waits": [{"key": "ci", "title": "CI green on the head",
                                          "condition": {"field": GOAL, "prop": "ci", "equals": "green"},
                                          "every_s": 30, "deadline_min": 45, "after": ["a"]}]})
    _done(seat, A, "pushed")
    assert st(seat, CI) == GG.ARMED
    seat.told.clear()
    write(seat, GOAL, "ci", "green")
    GG.check_wait(seat, CI, now=time.time() + 60)
    settle(seat)
    assert st(seat, CI) == GG.DONE
    assert len(seat.told) == 1 and "CI green on the head" in seat.told[0][1]


def test_no_plan_yet_tells_no_one(seat):
    seat.told.clear()
    _planner(seat, "PLANNED nothing yet")
    assert not seat.told


def test_the_planners_fulfilled_and_the_finished_graph_are_one_message(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "push"}]})
    _done(seat, A, "pushed abc123")
    seat.told.clear()
    _planner(seat, "FULFILLED: head abc123, CI green on abc123")
    assert len(seat.told) == 1 and "CI green on abc123" in seat.told[0][1]
