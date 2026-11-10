"""#994 live switch: MEMBRANE_FIELDS=1 runs the goal graph on the field engine instead of the 12 triggers.
The engine's writes land in the store as `trigger:field:<setter>`, so the adapters (worker pool, planner call,
wait checks, replan) see the same writes as before."""
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

MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")
if not hasattr(MV.Fields, "drain"):
    pytest.skip("membrane_vm built without Fields.drain", allow_module_level=True)

from membrane import adapters as AD  # noqa: E402
from membrane import ch as CH  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

GOAL = MEMBRANE_NS + "Goal:ship-it"
A, B = MEMBRANE_NS + "task:t:a", MEMBRANE_NS + "task:t:b"


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    calls = []
    monkeypatch.setattr(LLM, "request", lambda srv, prompt, about="", caller="", now=None, extra=None,
                        readonly=None: calls.append({"prompt": prompt, "about": about, "extra": extra or {}})
                        or {"rc": 0, "text": "queued"})
    owed = []
    monkeypatch.setattr(GG, "owe", lambda srv, eid: owed.append(eid))
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None)
    est._srv = srv
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    AD.start(srv)
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "llm", cls="Component", properties={"name": "llm"}))
    est.upsert(QuantumEntity(eid=GOAL, cls="Goal", properties={"name": "ship it", "status": "open"}))
    est.upsert(QuantumEntity(eid=A, cls="Task", properties={"goal": GOAL, "status": "running", "blocks": [B]}))
    est.upsert(QuantumEntity(eid=B, cls="Task", properties={"goal": GOAL, "status": "open", "open_blockers": 1}))
    assert GG.seed(srv) == 0                       # the triggers stand down under the switch
    live = FL.start(srv)
    srv.calls, srv.owed, srv.live = calls, owed, live
    yield srv
    FL.stop(srv)
    AD.stop()
    srv.ch_vm.detach()


def settle(srv):
    for _ in range(10):
        srv.live.settle()
        srv.ch_vm.drain()
        AD.drain()
        time.sleep(0.02)


def write(srv, eid, prop, value):
    d = srv.entity_store.docs[eid]
    with W.source("test", "x"), W.as_writer("llm:test"):
        d.properties[prop] = value
        srv.entity_store.upsert(d)
    settle(srv)


def p(srv, eid, k):
    return (srv.entity_store.docs[eid].properties or {}).get(k)


def test_no_goal_graph_trigger_is_defined_under_the_switch(seat):
    assert not [t for t in seat.ch_vm.triggers() if "goal-graph" in t.eid]


def test_a_done_blocker_makes_its_task_owed_and_the_goal_replans_then_plans(seat):
    write(seat, A, "status", "done")
    assert p(seat, B, "open_blockers") == 0
    assert p(seat, B, "status") == "owed"
    assert seat.owed == [B]                        # the worker pool heard the field's write
    assert p(seat, GOAL, "replan") == "task " + A + " done"
    assert p(seat, GOAL, "replan_by").startswith("trigger:field:")   # not a send-back
    assert [c["about"] for c in seat.calls] == [GOAL]                  # the planner was called once
    assert seat.calls[0]["extra"].get("lane") == "planner"


def test_a_new_task_born_later_joins_the_field(seat):
    C = MEMBRANE_NS + "task:t:c"
    seat.entity_store.upsert(QuantumEntity(eid=C, cls="Task", properties={"goal": GOAL, "status": "open", "open_blockers": 1}))
    a = seat.entity_store.docs[A]
    a.properties["blocks"] = [B, C]
    seat.entity_store.upsert(a)
    settle(seat)
    write(seat, A, "status", "done")
    assert p(seat, C, "status") == "owed"


def test_an_armed_wait_is_checked_every_every_s_not_once(seat, monkeypatch):
    checks = []
    monkeypatch.setattr(GG, "check_wait", lambda srv, eid: checks.append(eid) or (
        srv.entity_store.docs[eid].properties.update({"last_check_ts": now[0]}) or "still armed"))
    Wt = MEMBRANE_NS + "wait:t:w"
    seat.entity_store.upsert(QuantumEntity(eid=Wt, cls="Wait", properties={"goal": GOAL, "status": "armed",
                                                                          "armed_ts": 1000.0, "every_s": 30}))
    settle(seat)
    now = [1000.0]
    for t in (1010.0, 1020.0, 1031.0, 1040.0, 1050.0, 1062.0):
        now[0] = t
        seat.live.tick(t)
        settle(seat)
        w = seat.entity_store.docs[Wt]
        with W.as_writer("adapter:check"):               # the adapter's write of the check time, as the store hears it
            seat.entity_store.upsert(w)
        settle(seat)
    assert len(checks) == 2, checks                       # at 1031 and at 1062: every 30 s, not once, not every beat
