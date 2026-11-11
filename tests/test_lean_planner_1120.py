"""#1120 (operator 2026-11-11): the lean planner. Planner calls carried ~7k tokens of prompt over the chat's 5.5k
system prompt, and #1116's splitting makes many more of them. Now:
(a) the planner's message carries what changed since its last plan (tasks whose status changed or that are new,
    with their receipts, and the replan reason) plus a one-line index of the rest;
(b) the planner runs on a short system prompt of its own (the rules; byte-stable, so the prompt cache reads it),
    not the chat doctrine.
RED first. Sizes are printed (pytest -s) for the PR."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

GOAL = "membrane::Goal:lean"
RECEIPT = ("checked the three files and wrote the summary into the note; the totals match the source table, "
           "the two open questions are listed at the end, and the copy was sent to the person for review. ") * 2


def graph():
    """A realistic graph: 30 tasks (done with receipts, running, open, split with sub-tasks), 3 cards, a wait."""
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    est.upsert(QuantumEntity(eid=GOAL, cls="Goal", properties={"name": "lean", "text": "plan the autumn haul-out "
                                                                "and the winter projects", "status": "open"}),
               embedder=None)
    for i in range(30):
        st = "done" if i < 18 else "running" if i < 21 else "split" if i < 23 else "open"
        p = {"goal": GOAL, "title": f"step {i}: look up the yard's prices and book the slot {i}", "status": st,
             "plan_key": f"k{i}", "blocks": [GG.task_eid(GOAL, f"k{i + 1}")] if i % 3 == 0 else []}
        if st == "done":
            p["receipt"] = RECEIPT
        if i >= 24 and i % 2 == 0:
            p["parent"] = GG.task_eid(GOAL, "k21")
        est.upsert(QuantumEntity(eid=GG.task_eid(GOAL, f"k{i}"), cls="Task", properties=p), embedder=None)
    for i in range(3):
        est.upsert(QuantumEntity(eid=f"membrane::Decision:c{i}", cls="Decision",
                                 properties={"goal": GOAL, "name": f"Which yard {i}", "status": "open",
                                             "plan_key": f"c{i}", "blocks": [GG.task_eid(GOAL, "k25")]}),
                   embedder=None)
    est.upsert(QuantumEntity(eid=GG.wait_eid(GOAL, "w1"), cls="Wait",
                             properties={"goal": GOAL, "title": "the yard replies", "status": "armed",
                                         "plan_key": "w1", "condition": {"field": "x", "prop": "y", "equals": 1},
                                         "every_s": 60, "deadline_min": 600}), embedder=None)
    return est


def _set(est, eid, **kw):
    d = est.docs[eid]
    d.properties = {**d.properties, **kw}
    est.upsert(d, embedder=None)


def test_the_planner_has_its_own_short_stable_system_prompt():
    assert GG.PLANNER_SYSTEM and "{goal}" not in GG.PLANNER_SYSTEM and "{tick_s}" not in GG.PLANNER_SYSTEM   # byte-stable
    assert "plan" in GG.PLANNER_SYSTEM and "split" in GG.PLANNER_SYSTEM and "wait" in GG.PLANNER_SYSTEM
    doctrine = (Path(GG.__file__).resolve().parent / "doctrine.md").read_text()
    assert len(GG.PLANNER_SYSTEM) < len(doctrine)


def test_the_first_plan_sees_everything_then_only_what_changed():
    est = graph()
    first = GG.planner_text(est, GOAL, "planted")
    assert RECEIPT[:80] in first                                        # nothing seen yet: every receipt
    GG.planner_seen(est, GOAL)                                          # the pass ran
    _set(est, GG.task_eid(GOAL, "k19"), status="done", receipt="booked the slot for 14 Oct")
    second = GG.planner_text(est, GOAL, "task k19 done")
    assert "booked the slot for 14 Oct" in second                       # what changed, in full
    assert RECEIPT[:80] not in second                                   # the rest: one index line each
    assert "k3" in second and "[done]" in second                        # still listed, by key
    assert "task k19 done" in second
    full = GG._graph_lines(est, GOAL)
    print(f"\n[lean planner] first pass: {len(first)} chars; a later pass: {len(second)} chars; "
          f"the full graph lines alone: {len(full)} chars; planner system: {len(GG.PLANNER_SYSTEM)} chars")
    assert len(second) < len(first) / 2


def test_a_new_task_counts_as_changed():
    est = graph()
    GG.planner_text(est, GOAL, "planted")
    GG.planner_seen(est, GOAL)
    est.upsert(QuantumEntity(eid=GG.task_eid(GOAL, "n1"), cls="Task",
                             properties={"goal": GOAL, "title": "new thing", "status": "open", "plan_key": "n1",
                                         "receipt": "fresh receipt here"}), embedder=None)
    assert "fresh receipt here" in GG.planner_text(est, GOAL, "x")


def test_a_planner_session_runs_on_the_planner_system_prompt():
    from test_background_work_minds import _runner, _session, _Srv
    srv = _Srv(tempfile.mkdtemp())
    r, bodies = _runner(srv)
    r.system = "THE CHAT DOCTRINE"
    sid = _session(r, "planner: " + GOAL, "")
    r.run(sid, "plan")
    assert bodies[-1]["messages"][0] == {"role": "system", "content": GG.PLANNER_SYSTEM}
    sid = _session(r, "build", "")
    r.run(sid, "hi")
    assert bodies[-1]["messages"][0]["content"] == "THE CHAT DOCTRINE"
