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


def test_a_pack_that_no_longer_parses_at_boot_loads_its_last_good_text(seat):
    est = seat.entity_store
    L = MEMBRANE_NS + "lamp:desk"
    est.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    P = FL.PACK_NS + "lamps"
    est.upsert(QuantumEntity(eid=P, cls=FL.PACK_CLASS, properties={"name": "lamps", "c1f": LAMPS}))
    settle(seat)
    assert est.docs[P].properties.get("good_c1f") == LAMPS         # the text that parsed is kept on the entity
    d = est.docs[P]
    d.properties = {**d.properties, "c1f": "Lamp.power on-setter:\n  this.lit = 1\n"}
    est.upsert(d)
    settle(seat)
    FL.stop(seat)                                                  # the seat restarts with the broken text in the field
    seat.live = FL.start(seat)
    settle(seat)
    assert "Lamp" in seat.live.classes
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") is True                               # the old pack runs
    assert "pack lamps" in p(seat, P, "error")                     # and the error is still shown


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


def test_packs_import_each_other_and_create_their_own_entities(seat):
    """A pack waits for its import, then runs; it creates DomNode entities through `IMPORT dom` and they land in
    the store; a pack touching Task without IMPORT goalgraph is refused with the reason on its entity."""
    est = seat.entity_store
    INF, DOM, BAD = FL.PACK_NS + "inflight", FL.PACK_NS + "dom", FL.PACK_NS + "bad"
    est.upsert(QuantumEntity(eid=INF, cls=FL.PACK_CLASS, properties={"name": "inflight", "c1f":
        "PACK inflight 1\nIMPORT goalgraph 1\nIMPORT dom 1\nTask.status on-setter row:\n"
        "  ALWAYS: ('DomNode:inflight:' + this.eid).text = this.status\n"}))
    settle(seat)
    assert "imports dom 1, which isn't loaded" in p(seat, INF, "error")
    est.upsert(QuantumEntity(eid=DOM, cls=FL.PACK_CLASS, properties={"name": "dom", "c1f": "PACK dom 1\nDomNode.html getter: this.text\n"}))
    settle(seat)
    assert not p(seat, INF, "error") and not p(seat, DOM, "error")         # the import arrived: inflight is active
    write(seat, A, "status", "done")
    node = est.docs.get("DomNode:inflight:" + A)
    assert node is not None and node.cls == "DomNode" and node.properties.get("text") == "done"
    est.upsert(QuantumEntity(eid=BAD, cls=FL.PACK_CLASS, properties={"name": "bad", "c1f":
        "PACK bad 1\nTask.status on-setter meddle:\n  ALWAYS: this.note = 1\n"}))
    settle(seat)
    assert "IMPORT goalgraph 1" in p(seat, BAD, "error")


def test_a_pack_file_edited_while_the_seat_runs_reloads_on_the_next_beat(tmp_path, monkeypatch, seat):
    """Memo, 2026-11-09: a fixed .c1f file didn't load until a restart. A changed file reloads on the next beat."""
    monkeypatch.setattr(FL, "PACK_DIR", tmp_path)
    f = tmp_path / "lamps.c1f"
    f.write_text("PACK lamps 1\nLamp.power on-setter glow:\n  ALWAYS: this.lit = 'v1'\n")
    seat.live.on_sweep(time.time())                        # the beat sees the new file
    L = MEMBRANE_NS + "lamp:desk"
    seat.entity_store.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    settle(seat)
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") == "v1"
    time.sleep(0.01)
    f.write_text("PACK lamps 1\nLamp.power on-setter glow:\n  ALWAYS: this.lit = 'v2'\n")
    os.utime(f, (time.time() + 5, time.time() + 5))       # a later mtime, whatever the clock's resolution
    seat.live.on_sweep(time.time())
    write(seat, L, "power", "off")
    assert p(seat, L, "lit") == "v2"


def test_a_pack_is_known_by_its_id_whatever_class_a_tool_gave_it(seat):
    """Memo's entity tool saves every entity as a Component: a `membrane::pack:<name>` with `c1f` is still a pack."""
    est = seat.entity_store
    est.upsert(QuantumEntity(eid=FL.PACK_NS + "lamps", cls="Component", properties={"name": "lamps", "c1f":
        "PACK lamps 1\nLamp.power on-setter glow:\n  ALWAYS: this.lit = True\n"}))
    settle(seat)
    L = MEMBRANE_NS + "lamp:desk"
    est.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    settle(seat)
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") is True


def test_any_entity_holding_c1f_is_a_pack_named_after_pack_colon(seat):
    """Memo's entity tool names it `Component:pack:inflight` and forces class Component: still a pack, `inflight`."""
    est = seat.entity_store
    PE = "Component:pack:lamps"
    est.upsert(QuantumEntity(eid=PE, cls="Component", properties={"c1f":
        "PACK lamps 1\nLamp.power on-setter glow:\n  ALWAYS: this.lit = 'from Component'\n"}))
    settle(seat)
    L = MEMBRANE_NS + "lamp:desk"
    est.upsert(QuantumEntity(eid=L, cls="Lamp", properties={"power": "off"}))
    settle(seat)
    write(seat, L, "power", "on")
    assert p(seat, L, "lit") == "from Component"


def test_the_page_gets_a_snapshot_and_a_field_write_per_change(seat):
    """The browser VM's feed: a snapshot of the mirrored classes and the packs' text, then a `field.write` event on
    the seat's /event hub for every change to a mirrored entity (the engine's own writes included)."""
    events = []
    seat._loop_runner = types.SimpleNamespace(hub=types.SimpleNamespace(publish=events.append))
    snap = FL.CURRENT.snapshot()
    assert "goalgraph" not in snap["packs"] or snap["packs"]["goalgraph"]       # built in; file and entity packs listed
    eids = {e["eid"]: e for e in snap["entities"]}
    assert eids[A]["cls"] == "Task" and eids[GOAL]["cls"] == "Goal"
    write(seat, A, "status", "done")
    mine = [e for e in events if e["type"] == "field.write"]
    assert {"type": "field.write", "eid": A, "cls": "Task", "prop": "status", "new": "done"} in mine
    assert any(e["eid"] == B and e["prop"] == "open_blockers" for e in mine), "the engine's own writes stream too"
    C = MEMBRANE_NS + "task:t:c"
    seat.entity_store.upsert(QuantumEntity(eid=C, cls="Task", properties={"goal": GOAL, "status": "open"}))
    settle(seat)
    born = [e for e in events if e.get("eid") == C and "props" in e]
    assert born and born[0]["props"]["status"] == "open", "a birth carries its fields"


def test_view_packs_waiting_for_dom_reach_the_page_and_stream_their_edits(seat):
    """A view pack imports `dom`, which only pages have: on the seat it waits, but the page's snapshot carries its
    text, and an edit streams to open pages as `field.pack`."""
    events = []
    seat._loop_runner = types.SimpleNamespace(hub=types.SimpleNamespace(publish=events.append))
    VIEW = "PACK view 1\nIMPORT goalgraph 1\nIMPORT dom 1\nTask.status on-setter row:\n  ALWAYS: ('DomNode:view:' + this.eid).text = this.status\n"
    P = FL.PACK_NS + "view"
    seat.entity_store.upsert(QuantumEntity(eid=P, cls=FL.PACK_CLASS, properties={"name": "view", "c1f": VIEW}))
    settle(seat)
    assert "imports dom 1, which isn't loaded" in p(seat, P, "error")       # waits on the seat
    assert FL.CURRENT.snapshot()["packs"]["view"] == VIEW                     # but the page gets it
    assert {"type": "field.pack", "name": "view", "c1f": VIEW} in events
