"""#1120 (operator 2026-11-11, live on #1111: c2/c3 running and k7c2/k7c3 open for the same work): a plan that
creates a task matching an open or running task of the same goal is applied, but the new task carries
`dup_of: <eid>` and the planner is told (plan_result, the graph lines), so its next plan drops one. Matching: the
seat's existing line likeness (recall_banner: the same word stems, DEDUPE_JACCARD). RED first."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

A, B, C = (GG.task_eid(GOAL, k) for k in ("c2", "k7c2", "k9"))


def _plan(seat, plan):
    write(seat, GOAL, "plan", plan)
    return seat.entity_store.docs[GOAL].properties.get("plan_result")


def test_a_new_task_like_a_running_one_is_made_and_flagged(seat):
    _plan(seat, {"tasks": [{"key": "c2", "title": "Write the release note for the ledger"}]})
    GG.take(seat, A)
    r = _plan(seat, {"tasks": [{"key": "k7c2", "title": "write the release notes for the ledger"}]})
    assert B in seat.entity_store.docs
    assert seat.entity_store.docs[B].properties["dup_of"] == A
    assert "duplicate" in str(r) and A in str(r)
    assert f"duplicate of {A}" in GG.planner_text(seat.entity_store, GOAL, "x")


def test_an_open_one_counts_too(seat):
    _plan(seat, {"tasks": [{"key": "c2", "title": "Run the ledger tests"}]})
    _plan(seat, {"tasks": [{"key": "k7c2", "title": "run the ledger tests"}]})
    assert seat.entity_store.docs[B].properties.get("dup_of") == A


def test_different_work_is_not_flagged(seat):
    _plan(seat, {"tasks": [{"key": "c2", "title": "Write the release note for the ledger"}]})
    _plan(seat, {"tasks": [{"key": "k9", "title": "Open the PR and request a review"}]})
    assert not seat.entity_store.docs[C].properties.get("dup_of")


def test_an_ended_task_is_not_a_duplicate(seat):
    _plan(seat, {"tasks": [{"key": "c2", "title": "Run the ledger tests"}]})
    GG.take(seat, A)
    GG.finish(seat, A, "done", "ok")
    settle(seat)
    _plan(seat, {"tasks": [{"key": "k7c2", "title": "run the ledger tests"}]})
    assert not seat.entity_store.docs[B].properties.get("dup_of")
