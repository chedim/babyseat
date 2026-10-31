"""A goal-graph loop never closes a goal: the planner's FULFILLED goes to the chat and
the chat turn decides (operator, 2026-10-31). A replan cut off by a restart is planned
again at boot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import server as SRV  # noqa: E402
from membrane import toolset  # noqa: E402
from test_goal_graph_738 import GOAL, _drainer, seat, settle, st, write  # noqa: E402,F401


def _ev(prompt="plan the goal: task x done"):
    return {"kind": "llm-call", "entity": GOAL, "prompt": prompt, "lane": "planner",
            "caller": "trigger:x", "depth": 1}


def test_a_graph_loop_cannot_close_a_goal():
    for agent in ("planner: " + GOAL, "worker: membrane::task:x:k1"):
        r = SRV._graph_refusal("fulfill", agent)
        assert r and "FULFILLED" in r and "chat" in r
        assert "fulfill" not in toolset.legacy_names(SRV._openai_tools_for(agent))
    assert SRV._graph_refusal("fulfill", "watch: f:x.py") is None
    assert SRV._graph_refusal("fulfill", "build") is None
    assert SRV._graph_refusal("entity_edit", "planner: " + GOAL) is None


def test_the_planners_fulfilled_goes_to_the_chat_and_the_goal_stays_open(seat):
    dr, _ = _drainer(seat, "checked k1 and k2\nFULFILLED: PR #9 head abc123, CI green on abc123")
    seat.told.clear()
    assert dr._handle(_ev()) == "planned"
    settle(seat)
    assert st(seat, GOAL) == "open"
    assert len(seat.told) == 1
    ent, line = seat.told[0]
    assert ent == GOAL and "PR #9 head abc123, CI green on abc123" in line and "close" in line


def test_a_planner_that_is_not_done_tells_no_chat(seat):
    for reply in ("PLANNED k3", "UNCHANGED waiting on CI", "nothing to say"):
        dr, _ = _drainer(seat, reply)
        seat.told.clear()
        dr._handle(_ev())
        assert not seat.told, reply


def test_the_planner_is_told_it_never_closes():
    text = GG.PLANNER_TEXT
    assert "close it with fulfill" not in text
    assert "FULFILLED" in text and "chat" in text


def test_boot_plans_again_a_replan_its_planner_never_finished(seat):
    d = seat.entity_store.docs[GOAL]
    d.properties["replan"] = "task x done at 22:55:00"
    seat.entity_store.upsert(d)
    seat.calls.clear()
    assert GG.replan_lost(seat) == 1
    c = seat.calls[-1]
    assert c["about"] == GOAL and c["prompt"] == "plan the goal: task x done at 22:55:00"
    assert c["extra"].get("lane") == "planner"


def test_a_finished_planner_pass_is_not_planned_again_at_boot(seat):
    d = seat.entity_store.docs[GOAL]
    d.properties["replan"] = "task x done at 22:55:00"
    seat.entity_store.upsert(d)
    dr, _ = _drainer(seat, "UNCHANGED")
    dr._handle(_ev("plan the goal: task x done at 22:55:00"))
    settle(seat)
    seat.calls.clear()
    assert GG.replan_lost(seat) == 0 and not seat.calls


def test_a_closed_goal_is_not_planned_again_at_boot(seat):
    d = seat.entity_store.docs[GOAL]
    d.properties.update({"replan": "task x done", "status": "fulfilled"})
    seat.entity_store.upsert(d)
    seat.calls.clear()
    assert GG.replan_lost(seat) == 0
