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


def test_an_armed_wait_is_asked_to_check_on_every_beat_after_it_armed(seat, monkeypatch):
    """As the old every-beat trigger did: from the first beat after arming, every beat asks for a check (the adapter's
    check_wait paces the real checks by every_s). A wait already armed when it's loaded is asked too (after a
    restart), never only once."""
    checks = []
    monkeypatch.setattr(GG, "check_wait", lambda srv, eid: checks.append(eid) or "still armed")
    Wt = MEMBRANE_NS + "wait:t:w"
    base = time.time()
    seat.entity_store.upsert(QuantumEntity(eid=Wt, cls="Wait", properties={"goal": GOAL, "status": "armed",
                                                                          "armed_ts": base, "every_s": 30}))
    settle(seat)
    assert checks == []                                   # not at the moment it appears
    for k in range(1, 5):
        seat.live.tick(base + 10 * k)
        settle(seat)
    assert len(checks) == 4, checks                       # one ask per beat; the adapter paces the real checks



def test_the_field_engine_is_on_by_default(monkeypatch):
    """Behaviour lives on the field: with the engine built, no switch is needed."""
    monkeypatch.delenv("MEMBRANE_FIELDS", raising=False)
    assert FL.enabled()
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    assert FL.enabled()


def test_fields_zero_is_the_kill_switch(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "0")
    assert not FL.enabled()


def test_without_the_engine_the_triggers_stay(monkeypatch):
    """A seat whose native engine isn't built keeps the 12 triggers: the goal graph never goes dark."""
    monkeypatch.delenv("MEMBRANE_FIELDS", raising=False)
    import membrane as _pkg
    monkeypatch.delattr(_pkg, "membrane_vm", raising=False)
    monkeypatch.setitem(sys.modules, "membrane.membrane_vm", None)     # import fails
    assert not FL.enabled()


LAMPS = """
Lamp.power on-setter glow:
  WHEN this.power == 'on': this.lit = True
  ELSE: this.lit = False
"""


def test_a_pack_in_the_field_loads_its_class_runs_live_and_reloads_when_edited(seat):
    est = seat.entity_store
    L = MEMBRANE_NS + "lamp:desk"
    est.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    P = FL.PACK_NS + "lamps"
    est.upsert(QuantumEntity(eid=P, cls=FL.PACK_CLASS, properties={"name": "lamps", "c1f": LAMPS}))
    settle(seat)
    assert "Lamp" in seat.live.classes                         # the pack's class is mirrored now
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") is True
    # edited live: the new text replaces the pack
    d = est.docs[P]
    d.properties = {**d.properties, "c1f": "Lamp.power on-setter glow:\n  ALWAYS: this.lit = 'always'\n"}
    est.upsert(d)
    settle(seat)
    write(seat, L, "power", "off")
    assert p(seat, L, "lit") == "always"
    # a broken edit is refused and the last good pack stays
    d = est.docs[P]
    d.properties = {**d.properties, "c1f": "Lamp.power on-setter:\n  this.lit = 1\n"}
    est.upsert(d)
    settle(seat)
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") == "always"
    assert "pack lamps" in p(seat, P, "error")


def test_packs_in_the_pack_folder_load_at_start(tmp_path, monkeypatch):
    (tmp_path / "lamps.c1f").write_text(LAMPS)
    monkeypatch.setattr(FL, "PACK_DIR", tmp_path)
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est)
    L = MEMBRANE_NS + "lamp:desk"
    est.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    live = FL.start(srv, thread=False)
    try:
        assert "Lamp" in live.classes and live.f.has(L)
        d = est.docs[L]
        with W.as_writer("llm:test"):
            d.properties = {**d.properties, "power": "on"}
            est.upsert(d)
        live.settle()
        assert est.docs[L].properties.get("lit") is True
    finally:
        FL.stop(srv)
