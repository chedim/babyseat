"""The goals trio is writes and a getter (the operator, 2026-11-12: everything moves to cliffhanger): `Intent:board.intend =
{text, ttl?, by?}` creates the Goal (or finds the open one with the same plain text), `.fulfill = {spec, evidence, by?}` closes
one with its receipt (at least 8 characters of evidence, no receipt no close), `report` lists them. The tools, their routes and
their bridges are gone. (The pin, the cap and the ranked active set stay with the dream loop's selection until it moves.)"""
import os
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

EID = "Intent:board"


@pytest.fixture
def seat(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=types.SimpleNamespace(),
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, facts={"agent_writes": True})
    yield srv, est
    AD.stop()
    FL.stop(srv)


def _until(fn, timeout=6.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def _write(est, prop, value):
    assert _until(lambda: EID in est.docs)
    d = est.docs[EID]
    d.properties = {**d.properties, prop: value}
    est.upsert(d, embedder=None)


def _goals(est):
    return [d for d in est.docs.values() if d.cls == "Goal"]


def test_intend_creates_the_goal_with_its_text_and_who_planted_it(seat):
    srv, est = seat
    _write(est, "intend", {"text": "Ship the 0.7.5 release", "by": "operator"})
    r = _until(lambda: est.docs[EID].properties.get("intend_result"))
    assert r["ok"] is True and r["created"] is True
    (g,) = _goals(est)
    p = g.properties
    assert r["eid"] == g.eid and p["text"] == "Ship the 0.7.5 release" and p["name"] == "Ship the 0.7.5 release"
    assert p["status"] == "open" and p["planted_by"] == "operator" and p["born"] > 0


def test_the_same_words_in_any_case_and_spacing_are_the_same_goal(seat):
    srv, est = seat
    _write(est, "intend", {"text": "Ship the 0.7.5 release"})
    first = _until(lambda: est.docs[EID].properties.get("intend_result"))
    _write(est, "intend", {"text": "  ship   the 0.7.5 RELEASE "})
    r = _until(lambda: (est.docs[EID].properties.get("intend_result") or {}).get("id") != first["id"]
               and est.docs[EID].properties.get("intend_result"))
    assert r["ok"] is True and r["created"] is False and r["eid"] == first["eid"]
    assert len(_goals(est)) == 1


def test_a_different_text_is_another_goal(seat):
    srv, est = seat
    _write(est, "intend", {"text": "Ship the 0.7.5 release"})
    _until(lambda: len(_goals(est)) == 1)
    _write(est, "intend", {"text": "Ship the 0.7.5 release notes"})
    assert _until(lambda: len(_goals(est)) == 2)


def test_an_empty_intend_is_refused(seat):
    srv, est = seat
    _write(est, "intend", {"text": "   "})
    r = _until(lambda: est.docs[EID].properties.get("intend_result"))
    assert r["ok"] is False and "text required" in r["error"] and _goals(est) == []


def _plant(est, text):
    _write(est, "intend", {"text": text})
    r = _until(lambda: est.docs[EID].properties.get("intend_result"))
    return r["eid"]


def test_fulfill_closes_a_goal_with_its_receipt(seat):
    srv, est = seat
    eid = _plant(est, "Ship the 0.7.5 release")
    _write(est, "fulfill", {"spec": "0.7.5 release", "evidence": "pypi shows 0.7.5, rc=0", "by": "worker"})
    r = _until(lambda: est.docs[EID].properties.get("fulfill_result"))
    assert r["ok"] is True and r["eid"] == eid
    p = est.docs[eid].properties
    assert p["status"] == "fulfilled" and p["fulfilled_by"] == "worker" and p["fulfilled_evidence"] == "pypi shows 0.7.5, rc=0"
    assert p["fulfilled_ts"] > 0


def test_fulfill_by_id_and_no_receipt_no_close(seat):
    srv, est = seat
    eid = _plant(est, "Ship the 0.7.5 release")
    _write(est, "fulfill", {"spec": eid, "evidence": "done"})
    r = _until(lambda: est.docs[EID].properties.get("fulfill_result"))
    assert r["ok"] is False and "evidence required" in r["error"]
    assert est.docs[eid].properties["status"] == "open"
    _write(est, "fulfill", {"spec": eid, "evidence": "the suite is green: 1665 passed"})
    r2 = _until(lambda: (est.docs[EID].properties.get("fulfill_result") or {}).get("id") != r["id"]
                and est.docs[EID].properties.get("fulfill_result"))
    assert r2["ok"] is True and est.docs[eid].properties["status"] == "fulfilled"


def test_an_unknown_goal_and_an_eid_shaped_spec_that_is_not_one_never_name_match(seat):
    srv, est = seat
    _plant(est, "Ship the 0.7.5 release and goal:other")
    _write(est, "fulfill", {"spec": "goal:other", "evidence": "pypi shows 0.7.5, rc=0"})
    r = _until(lambda: est.docs[EID].properties.get("fulfill_result"))
    assert r["ok"] is False and "no open goal" in r["error"]
    assert [g.properties["status"] for g in _goals(est)] == ["open"]


def test_report_lists_open_goals_and_the_recently_fulfilled(seat):
    srv, est = seat
    a = _plant(est, "Ship the 0.7.5 release")
    _plant(est, "Write the migration notes")
    _write(est, "fulfill", {"spec": a, "evidence": "pypi shows 0.7.5, rc=0"})
    assert _until(lambda: est.docs[a].properties.get("status") == "fulfilled")
    rep = _until(lambda: "Write the migration notes" in str(est.docs[EID].properties.get("report") or "")
                 and est.docs[EID].properties.get("report"))
    assert "Open goals" in rep and "- " + _goals(est)[1].eid in rep or "Write the migration notes" in rep
    assert "Fulfilled" in rep and "Ship the 0.7.5 release" in rep


def test_the_tools_the_routes_and_the_bridges_are_gone():
    for t in ("intend", "fulfill", "intents"):
        assert t not in M.METHOD_TOOLS and t not in M.TOOL_IMPLS
        assert not [d for d in M.METHOD_TOOL_DEFS if d["name"] == t]
        assert f"def tool_{t}" not in Path(M.__file__).read_text()
    lanes = Path(L.__file__).read_text()
    for r in ("/membrane/intend", "/membrane/fulfill", "/membrane/intents"):
        assert f'"{r}"' not in lanes


def test_the_lane_rules_the_tools_had_stand_on_the_boards_writes(monkeypatch):
    from membrane import decision_map as DM
    from membrane import diag_requests as DR
    for agent, key, refused in [("plan", "intend", True), ("plan", "fulfill", True), ("watch:abc", "intend", True),
                                ("watch:abc", "fulfill", False), ("planner:g1", "fulfill", True),
                                ("planner:g1", "intend", True), ("build", "intend", False), ("build", "fulfill", False)]:
        monkeypatch.setattr(DR, "current", lambda a=agent: {"agent": a})
        why = DM.refusal("Intent", key, {"text": "x"}, {})
        assert bool(why) is refused, (agent, key, why)
    monkeypatch.setattr(DR, "current", lambda: {"agent": "build"})
    assert DM.refusal("Intent", "report", None, {}) is None
