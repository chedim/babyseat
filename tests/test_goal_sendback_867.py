"""#867: sending a goal back to its planner must never do nothing and look finished."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_goal_graph_738 import (GOAL, A, GG, seat, settle, st,  # noqa: E402,F401
                                 tasks_in_pool, write)


def done_goal(seat, status="done"):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a"}]})
    tasks_in_pool(seat)
    d = seat.entity_store.docs[A]
    d.properties = {**d.properties, "status": status}
    seat.entity_store.upsert(d, embedder=None)
    settle(seat)
    seat.calls.clear()
    seat.told.clear()


def result(seat):
    return seat.entity_store.docs[GOAL].properties.get("plan_result")


def test_listing_a_done_task_without_redo_is_reported_as_updated_and_nothing_runnable(seat):
    done_goal(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a again"}]})
    r = str(result(seat))
    assert st(seat, A) == "done"
    assert "updated" in r and A in r, r
    assert "nothing runnable" in r, r


def test_a_planner_can_reopen_a_done_task_with_redo(seat):
    done_goal(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a again", "redo": True}]})
    assert st(seat, A) == "owed" and tasks_in_pool(seat) == [A]
    r = str(result(seat))
    assert "reopened" in r and A in r, r
    assert "nothing runnable" not in r


def test_redo_on_a_running_task_is_ignored(seat):
    done_goal(seat, status="running")
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "x", "redo": True}]})
    assert st(seat, A) == "running"


def test_a_new_task_is_created_not_updated(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a"}]})
    r = str(result(seat))
    assert A in r and "'updated': []" in r and "'reopened': []" in r, r


def test_a_send_back_answered_with_nothing_runnable_is_told_as_that(seat):
    done_goal(seat)
    assert GG.report(seat, GOAL, None, sent_back="rework the thing at 10:00:00")
    line = seat.told[-1][1]
    assert "sent back" in line and "nothing runnable" in line, line
    assert "its graph has nothing left moving" not in line


def test_a_replan_on_a_fulfilled_goal_reopens_it_and_plans(seat):
    done_goal(seat)
    g = seat.entity_store.docs[GOAL]
    g.properties = {**g.properties, "status": "fulfilled"}
    seat.entity_store.upsert(g, embedder=None)
    settle(seat)
    write(seat, GOAL, "replan", "rework: the PR bounced", writer="llm:chat")
    assert seat.entity_store.docs[GOAL].properties["status"] == "open"
    assert any(c["prompt"].startswith("plan the goal: rework") for c in seat.calls), seat.calls


def test_a_stuck_or_failed_task_listed_again_still_retries(seat):
    for s in ("stuck", "failed"):
        done_goal(seat, status=s)
        write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "again"}]})
        assert st(seat, A) == "owed", s
        tasks_in_pool(seat)
