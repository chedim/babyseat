"""#773: a goal graph's third node class, Wait. A wait blocks tasks until a condition
holds -- a field property, or a method that passes -- and nothing runs while it waits:
"push, wait for green CI, answer the review" is a task, a wait, and a task."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, tasks_in_pool, write  # noqa: E402,F401

PR = MEMBRANE_NS + "Component:pr-757"
PUSH, ANSWER = GG.task_eid(GOAL, "push"), GG.task_eid(GOAL, "answer")
CI = GG.wait_eid(GOAL, "ci")
EVERY, DEADLINE = 30, 45                      # the planner's choice for this wait


def _plan(condition):
    return {"tasks": [{"key": "push", "title": "push the fix"},
                      {"key": "answer", "title": "answer the review", "after": ["ci"]}],
            "waits": [{"key": "ci", "title": "CI green", "condition": condition, "every_s": EVERY,
                       "deadline_min": DEADLINE, "after": ["push"]}]}


def _push_done(srv):
    assert GG.take(srv, PUSH)
    GG.finish(srv, PUSH, "done", "pushed")
    settle(srv)


def _sweep(srv, dt, due=True):
    """The trigger's beat dt seconds on; `due`: the wait's own interval has passed too."""
    d = srv.entity_store.docs.get(CI)
    if due and d is not None and d.properties.get("last_check_ts"):
        d.properties = {**d.properties, "last_check_ts": time.time() - EVERY - 1}
        srv.entity_store.upsert(d, embedder=None)
    srv.ch_vm.sweep(now=time.time() + dt)
    settle(srv)


def test_a_plan_makes_a_wait_between_two_tasks(seat):
    write(seat, GOAL, "plan", _plan({"field": PR, "prop": "ci", "equals": "green"}))
    est = seat.entity_store
    assert est.docs[CI].cls == GG.WAIT and st(seat, CI) == "open"
    assert est.docs[PUSH].properties["blocks"] == [CI]
    assert est.docs[CI].properties["blocks"] == [ANSWER]
    assert st(seat, PUSH) == "owed" and st(seat, ANSWER) == "open"
    assert est.docs[ANSWER].properties["open_blockers"] == 1


def test_a_wait_arms_when_what_it_follows_is_done(seat):
    write(seat, GOAL, "plan", _plan({"field": PR, "prop": "ci", "equals": "green"}))
    assert st(seat, CI) == "open"
    _push_done(seat)
    assert st(seat, CI) == GG.ARMED and st(seat, ANSWER) == "open"


def test_a_field_condition_releases_the_task(seat):
    seat.entity_store.upsert(QuantumEntity(eid=PR, cls="Component", properties={"name": "pr-757", "ci": "pending"}))
    write(seat, GOAL, "plan", _plan({"field": PR, "prop": "ci", "equals": "green"}))
    _push_done(seat)
    _sweep(seat, GG.WAIT_TICK_S + 1)
    assert st(seat, CI) == GG.ARMED                                   # pending: still waiting
    write(seat, PR, "ci", "green")
    _sweep(seat, 2 * GG.WAIT_TICK_S + 2)
    assert st(seat, CI) == "done" and st(seat, ANSWER) == "owed"
    assert ANSWER in tasks_in_pool(seat)


def test_a_method_condition_holds_when_the_method_passes(seat, monkeypatch):
    calls, rc = [], {"rc": 1}
    monkeypatch.setattr(N, "method_call", lambda srv, est, fld, mem, obj, name, args=None, _depth=0:
                        calls.append((obj, name, args)) or {"rc": rc["rc"], "head": "checks"})
    write(seat, GOAL, "plan", _plan({"method": "gh.pr_checks", "args": {"pr": "757"}}))
    _push_done(seat)
    _sweep(seat, GG.WAIT_TICK_S + 1)
    assert calls == [("gh", "pr_checks", {"pr": "757"})] and st(seat, CI) == GG.ARMED
    rc["rc"] = 0
    _sweep(seat, 2 * GG.WAIT_TICK_S + 2)
    assert st(seat, CI) == "done" and st(seat, ANSWER) == "owed"


def test_nothing_is_checked_before_the_wait_is_armed(seat, monkeypatch):
    calls = []
    monkeypatch.setattr(N, "method_call", lambda *a, **k: calls.append(a) or {"rc": 0})
    write(seat, GOAL, "plan", _plan({"method": "gh.pr_checks", "args": {}}))
    _sweep(seat, GG.WAIT_TICK_S + 1)
    assert calls == [] and st(seat, CI) == "open"


def test_a_wait_past_its_deadline_fails_and_goes_to_the_chat(seat):
    seat.entity_store.upsert(QuantumEntity(eid=PR, cls="Component", properties={"name": "pr-757", "ci": "pending"}))
    write(seat, GOAL, "plan", _plan({"field": PR, "prop": "ci", "equals": "green"}))
    _push_done(seat)
    _sweep(seat, GG.WAIT_TICK_S + 1)                                  # armed and checked once
    d = seat.entity_store.docs[CI]
    d.properties = {**d.properties, "armed_ts": time.time() - DEADLINE * 60 - 5}
    seat.entity_store.upsert(d, embedder=None)
    seat.calls.clear()
    _sweep(seat, 2 * GG.WAIT_TICK_S + 2)
    assert st(seat, CI) == "failed" and st(seat, ANSWER) == "open"
    assert [t for t in seat.told if t[0] == CI] and "failed" in [t for t in seat.told if t[0] == CI][0][1]
    assert not [c for c in seat.calls if c["prompt"].startswith("plan the goal: wait")]   # the chat decides


def test_a_wait_is_checked_only_as_often_as_the_planner_chose(seat, monkeypatch):
    calls = []
    monkeypatch.setattr(N, "method_call", lambda *a, **k: calls.append(a) or {"rc": 1})
    write(seat, GOAL, "plan", _plan({"method": "gh.pr_checks", "args": {}}))
    _push_done(seat)
    _sweep(seat, GG.WAIT_TICK_S + 1)
    _sweep(seat, 2 * GG.WAIT_TICK_S + 2, due=False)         # the beat again, inside every_s
    assert len(calls) == 1
    _sweep(seat, 3 * GG.WAIT_TICK_S + 3)                     # every_s passed
    assert len(calls) == 2


def test_the_planner_must_choose_the_interval_and_the_deadline(seat):
    cond = {"field": PR, "prop": "ci", "equals": "green"}
    for w, word in (({"condition": cond, "deadline_min": 5}, "every_s"),
                    ({"condition": cond, "every_s": 30}, "every_s"),
                    ({"condition": cond, "every_s": GG.WAIT_TICK_S - 1, "deadline_min": 5}, "at least"),
                    ({"condition": cond, "every_s": 30, "deadline_min": 0}, "deadline_min")):
        write(seat, GOAL, "plan", {"tasks": [{"key": "a"}], "waits": [{"key": "w", "after": ["a"], **w}]})
        assert word in str(seat.entity_store.docs[GOAL].properties.get("plan_result")), w
        assert GG.wait_eid(GOAL, "w") not in seat.entity_store.docs


def test_a_wait_without_a_condition_is_refused(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}], "waits": [{"key": "w", "after": ["a"], "every_s": 30,
                                                                  "deadline_min": 5}]})
    assert "condition" in str(seat.entity_store.docs[GOAL].properties.get("plan_result"))
    assert GG.wait_eid(GOAL, "w") not in seat.entity_store.docs


def test_a_cycle_through_a_wait_is_refused(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "after": ["w"]}],
                               "waits": [{"key": "w", "condition": {"field": PR, "prop": "ci", "equals": 1},
                                          "every_s": 30, "deadline_min": 5, "after": ["a"]}]})
    assert "cycle" in str(seat.entity_store.docs[GOAL].properties.get("plan_result"))


def test_the_graph_and_the_planner_show_waits(seat):
    write(seat, GOAL, "plan", _plan({"field": PR, "prop": "ci", "equals": "green"}))
    v = GG.view(seat.entity_store, GOAL)
    assert [w["eid"] for w in v["waits"]] == [CI]
    text = GG.planner_text(seat.entity_store, GOAL, "test")
    assert f"wait {CI} [open] CI green" in text
    assert '"waits"' in GG.PLANNER_TEXT and "every_s" in text and "deadline_min" in text
