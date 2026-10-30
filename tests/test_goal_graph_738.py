"""#738: goals as a graph. A planner splits a goal into decisions (cards) and tasks;
decisions escalate; ready tasks go to a worker pool; the machinery is cliffhanger
triggers (seeded once), the planning judgment an llm.call written by a trigger."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as AD  # noqa: E402
from membrane import ch as CH  # noqa: E402
from membrane import decision_map as DM  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

GOAL = MEMBRANE_NS + "Goal:ship-it"


@pytest.fixture
def seat(monkeypatch):
    calls, told = [], []
    monkeypatch.setattr(LLM, "request", lambda srv, prompt, about="", caller="", now=None, extra=None,
                        readonly=None: calls.append({"prompt": prompt, "about": about, "caller": caller,
                                                     "extra": extra or {}}) or {"rc": 0, "text": "queued"})
    monkeypatch.setattr(WA, "escalate", lambda srv, ev, ent, line="": told.append((ent, line)) or True)
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None)
    est._srv = srv
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    AD.start(srv)
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "llm", cls="Component", properties={"name": "llm"}))
    est.upsert(QuantumEntity(eid=GOAL, cls="Goal", properties={"name": "ship it", "text": "ship it",
                                                                "status": "open"}))
    assert GG.seed(srv) == len(GG.TRIGGERS)
    srv.calls, srv.told = calls, told
    yield srv
    AD.stop()
    srv.ch_vm.detach()


def settle(srv, rounds=8):
    for _ in range(rounds):
        srv.ch_vm.drain()
        AD.drain()
        time.sleep(0.02)
    srv.ch_vm.drain()
    assert not srv.ch_vm.errors, srv.ch_vm.errors


def write(srv, eid, prop, value, writer="llm:test"):
    d = srv.entity_store.docs[eid]
    with W.source("test", "x"), W.as_writer(writer):
        d.properties[prop] = value
        srv.entity_store.upsert(d)
    settle(srv)


def st(srv, eid):
    return (srv.entity_store.docs[eid].properties or {}).get("status")


def tasks_in_pool(srv):
    return [e["entity"] for e in srv.events_pool.take() if e.get("kind") == "task"]


PLAN = {"tasks": [{"key": "a", "title": "do a"},
                  {"key": "b", "title": "do b", "after": ["a"]},
                  {"key": "c", "title": "do c"}],
        "cards": [{"key": "q", "name": "Which way", "summary": "left or right",
                   "var": {"type": "option", "options": ["left", "right"]}, "pick": "left", "blocks": ["c"]}]}
A, B, C = (GG.task_eid(GOAL, k) for k in "abc")


def test_the_in_operator_joins_an_entity_to_a_list():
    assert CH.evaluate("'x' in ['x', 'y']", {}) is True
    assert CH.evaluate("'z' not in ['x']", {}) is True
    assert CH.evaluate("1 in None", {}) is False


def test_a_planted_goal_calls_the_planner_from_a_trigger(seat):
    GG.plan_wanted(seat, GOAL, "planted")
    settle(seat)
    assert len(seat.calls) == 1, seat.calls
    c = seat.calls[0]
    assert c["about"] == GOAL and c["prompt"].startswith("plan the goal: planted")
    assert c["extra"].get("lane") == "planner" and c["caller"].startswith("trigger:")


def test_a_plan_writes_tasks_cards_and_edges_and_escalates_the_card(seat):
    write(seat, GOAL, "plan", PLAN)
    est = seat.entity_store
    assert "tasks" in str(est.docs[GOAL].properties.get("plan_result")), est.docs[GOAL].properties
    assert est.docs[A].properties["blocks"] == [B]
    card = [d for d in est.docs.values() if d.cls == "Decision" and d.properties.get("goal") == GOAL][0]
    assert card.properties["blocks"] == [C] and card.properties["status"] == "open"
    assert seat.told and seat.told[0][0] == card.eid                     # escalated to the operator
    assert est.docs[B].properties["open_blockers"] == 1 and st(seat, B) == "open"
    assert est.docs[C].properties["open_blockers"] == 1 and st(seat, C) == "open"
    assert st(seat, A) == "owed"                                          # no blockers: ready
    assert tasks_in_pool(seat) == [A]                                     # to the worker pool
    assert seat.ch_vm.fired.get(MEMBRANE_NS + "Trigger:goal-graph-ready") == 1


def test_a_card_answered_unblocks_its_task_and_replans(seat):
    write(seat, GOAL, "plan", PLAN)
    tasks_in_pool(seat)
    card = [d for d in seat.entity_store.docs.values() if d.cls == "Decision"][0]
    seat.calls.clear()
    with W.source("person", "op"):
        assert DM.answer(seat.entity_store, None, card.eid, "right")["ok"]
    settle(seat)
    assert st(seat, C) == "owed" and tasks_in_pool(seat) == [C]
    assert any(c["prompt"].startswith("plan the goal: card " + card.eid) for c in seat.calls), seat.calls


def test_a_task_blocked_by_a_task_is_ready_when_the_blockers_result_lands(seat):
    write(seat, GOAL, "plan", PLAN)
    assert GG.take(seat, A) and st(seat, A) == "running"
    seat.calls.clear()
    with W.source("worker", A):
        res = GG.finish(seat, A, "done", "made a")
    settle(seat)
    assert seat.entity_store.docs[res].properties["receipt"] == "made a"
    assert st(seat, A) == "done" and st(seat, B) == "owed"
    assert [c["prompt"] for c in seat.calls] == [f"plan the goal: task {A} done"]


def test_a_cycle_is_refused_and_nothing_is_written(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "x", "after": ["y"]}, {"key": "y", "after": ["x"]}]})
    est = seat.entity_store
    assert "cycle" in str(est.docs[GOAL].properties.get("plan_result"))
    assert est.docs[GOAL].properties.get("plan_rc") not in (0, None)
    assert not [d for d in est.docs.values() if d.cls == GG.TASK]


def test_a_cycle_through_an_earlier_plan_is_refused(seat):
    write(seat, GOAL, "plan", PLAN)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a", "title": "do a", "after": ["b"]}]})
    assert "cycle" in str(seat.entity_store.docs[GOAL].properties.get("plan_result"))


def test_a_worker_failure_replans(seat):
    write(seat, GOAL, "plan", PLAN)
    GG.take(seat, A)
    seat.calls.clear()
    with W.source("worker", A):
        GG.finish(seat, A, "failed", "no disk")
    settle(seat)
    assert st(seat, B) == "open"                                          # a failure unblocks nothing
    assert [c["prompt"] for c in seat.calls] == [f"plan the goal: task {A} failed"]


def _drainer(seat, reply):
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session = lambda agent, title: "ses_w"
    seen = []

    def run(sid, text, agent):
        seen.append({"text": text, "agent": agent, "chain": W.current()})
        return {"text": reply}
    seat.run_loop_turn = run
    return WA._LoopDrainer(seat), seen


def test_a_worker_takes_one_ready_task_and_writes_its_result(seat):
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    dr, seen = _drainer(seat, "did it\nDONE wrote a.txt")
    assert dr._handle(ev) == "done"
    settle(seat)
    assert "WORKER on task " + A in seen[0]["text"]
    assert seen[0]["chain"]["via"] == []                                   # a fresh chain: the next round may fire
    assert st(seat, A) == "done" and seat.entity_store.docs[A].properties["receipt"] == "wrote a.txt"
    assert st(seat, B) == "owed"


def test_a_stuck_worker_hands_back_to_the_planner_never_silently(seat):
    write(seat, GOAL, "plan", PLAN)
    ev = [e for e in seat.events_pool.take() if e.get("kind") == "task"][0]
    seat.calls.clear()
    dr, _ = _drainer(seat, "STUCK which bucket to use")
    assert dr._handle(ev) == "stuck"
    settle(seat)
    assert st(seat, A) == "stuck"
    assert [c["prompt"] for c in seat.calls] == [f"plan the goal: task {A} stuck"]
    # the planner turns it into a card, which is escalated
    seat.told.clear()
    write(seat, GOAL, "plan", {"cards": [{"key": "bucket", "name": "Which bucket", "blocks": ["b"]}],
                               "tasks": [{"key": "b", "title": "do b", "after": ["a"]}]})
    assert seat.told and "Which bucket" in seat.told[0][1], seat.entity_store.docs[GOAL].properties.get("plan_result")


def test_the_planner_lane_runs_the_call_with_the_graph_and_a_fresh_chain(seat):
    write(seat, GOAL, "plan", PLAN)
    dr, seen = _drainer(seat, "PLANNED nothing new")
    ev = {"kind": "llm-call", "entity": GOAL, "prompt": "plan the goal: planted", "lane": "planner",
          "caller": "trigger:x", "depth": 1, "chain": {"id": "c1", "root": "t", "via": ["T1"]}}
    dr._handle(ev)
    assert "PLANNER for goal " + GOAL in seen[0]["text"] and "task " + A in seen[0]["text"]
    assert seen[0]["agent"].startswith("planner") and seen[0]["chain"]["via"] == []


def test_a_planted_goal_event_goes_to_the_planner_not_a_goal_loop(seat):
    dr, seen = _drainer(seat, "DONE")
    out = dr._handle({"kind": "intent-planted", "entity": GOAL, "text": "ship it", "created": True})
    settle(seat)
    assert out == "planned" and not seen                                  # no goal loop turn
    assert seat.calls and seat.calls[0]["about"] == GOAL


def test_a_rewake_of_a_planned_goal_is_no_new_plan(seat):
    write(seat, GOAL, "plan", PLAN)
    seat.calls.clear()
    dr, seen = _drainer(seat, "DONE")
    assert dr._handle({"kind": "intent-planted", "entity": GOAL, "rewake": True}) == "skip"
    settle(seat)
    assert not seat.calls and not seen


def test_the_lanes_have_their_own_slots():
    lr = WA.LoopRunner(3)
    assert lr.admit(WA.LoopDesc("planner", eid="g1")) and not lr.admit(WA.LoopDesc("planner", eid="g2"))
    ws = [lr.admit(WA.LoopDesc("worker", eid=f"t{i}")) for i in range(GG.WORKERS + 1)]
    assert ws == [True] * GG.WORKERS + [False]
    assert all(lr.admit(WA.LoopDesc("watch", eid=f"w{i}")) for i in range(3))   # the other loops keep theirs


def test_the_machinery_is_seeded_triggers_with_no_scheduling_loop(seat):
    names = {d.properties["name"] for d in seat.entity_store.docs.values() if d.cls == CH.CLASS}
    assert names == {t[0] for t in GG.TRIGGERS}
    src = Path(GG.__file__).read_text()
    assert "threading" not in src and "while " not in src and "sleep" not in src


def test_reading_a_goal_shows_its_graph(seat):
    write(seat, GOAL, "plan", PLAN)
    v = GG.view(seat.entity_store, GOAL)
    by = {t["eid"]: t for t in v["tasks"]}
    assert by[B]["waits_on"] == [A] and by[A]["status"] == "owed" and v["ready"] == [A]
    assert v["cards"][0]["blocks"] == [C]


def test_boot_puts_an_owed_or_running_task_back_in_the_pool(seat):
    write(seat, GOAL, "plan", PLAN)
    GG.take(seat, A)
    seat.events_pool.take()
    assert GG.reowe(seat) == 1 and st(seat, A) == "owed" and tasks_in_pool(seat) == [A]



def test_a_goal_its_planner_saw_is_not_re_asked_by_a_pulse(seat):
    dr, _ = _drainer(seat, "UNCHANGED nothing to do")
    dr._handle({"kind": "llm-call", "entity": GOAL, "prompt": "plan the goal: planted", "lane": "planner",
                "caller": "trigger:x", "depth": 1})
    assert seat.entity_store.docs[GOAL].properties.get("planner_ts")
    assert dr._handle({"kind": "intent-planted", "entity": GOAL, "rewake": True}) == "skip"
    assert dr._handle({"kind": "intent-planted", "entity": GOAL, "created": False,     # a person re-pins it:
                       "ts": time.time() + 1}) == "planned"                       # the planner is asked again


def test_the_entity_read_tool_shows_the_goal_graph(seat, monkeypatch):
    from membrane import mcp_server as M
    write(seat, GOAL, "plan", PLAN)
    out = {"ok": True, "eid": GOAL, "cls": "Goal", "properties": {"name": "ship it"},
           "goal_graph": GG.view(seat.entity_store, GOAL)}
    monkeypatch.setattr(M, "_http", lambda *a, **k: out)
    text = M.tool_entity_read({"spec": GOAL}, 0)
    assert "the goal's graph: 3 task(s), 1 card(s)" in text
    assert f"- task {B} [open] do b waits on {A}" in text
