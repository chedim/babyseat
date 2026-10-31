"""A task set back to open with nothing blocking it goes to the worker pool (2026-10-31:
planners "retried" stuck tasks by setting them open; the ready trigger fires only when the
blocker count CHANGES to 0, so an open task already at 0 sat forever -- #764, #757, #769)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

PLAN = {"tasks": [{"key": "a", "title": "do a"}],
        "waits": [{"key": "w", "title": "wait w", "condition": {"field": GOAL, "prop": "x", "equals": "y"},
                   "every_s": 30, "deadline_min": 45}]}
A, W = GG.task_eid(GOAL, "a"), GG.wait_eid(GOAL, "w")


def test_a_task_set_back_to_open_is_owed_at_the_recount(seat):
    write(seat, GOAL, "plan", PLAN)
    seat.events_pool.take()
    assert GG.take(seat, A)
    GG.finish(seat, A, "stuck", "cut off")
    settle(seat)
    write(seat, A, "status", "open")                # what the planners did
    seat.events_pool.take()
    GG.recount(seat, GOAL)
    settle(seat)
    assert st(seat, A) == "owed"
    assert A in [e["entity"] for e in seat.events_pool.take() if e.get("kind") == "task"]


def test_a_wait_set_back_to_open_is_armed_at_the_recount(seat):
    write(seat, GOAL, "plan", PLAN)
    write(seat, W, "status", "open")
    GG.recount(seat, GOAL)
    settle(seat)
    assert st(seat, W) == GG.ARMED
