"""#576: Blurp's overlay on the field map. The VM keeps its recent firings
(trigger, from, to, when); GET /membrane/map/blurp lays them out in field ids
with each trigger's state now (shadow / proposal / suggest); the 3D view draws
a line per cross-entity firing, a halo per same-entity one, and a pulse for
each firing it had not seen."""
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


@pytest.fixture(autouse=True)
def _commands_world(monkeypatch):
    """#727 gates the LLM's command branches off (toys first); this file tests that kept code."""
    monkeypatch.setattr(L, "WORLD", "commands")

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


@pytest.fixture
def seat(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    B.attach(srv)
    B.exchange(srv, {"awake": True, "mode": "on"})
    yield srv
    srv.ch_vm.detach()


def _toy(est, name):
    e = QuantumEntity(eid=MEMBRANE_NS + f"Component:toy:{name}", cls="toy", properties={"name": name})
    est.upsert(e)
    return est.docs[e.eid]


def _turn(srv, *writes):
    with W.source("person", "t"):
        for ent, prop, val, who in writes:
            with W.as_writer(who):
                ent.properties[prop] = val
                srv.entity_store.upsert(ent)
    srv.ch_vm.drain()


def test_the_vm_remembers_its_recent_firings(seat):
    est = seat.entity_store
    x, y = _toy(est, "x"), _toy(est, "y")
    _turn(seat, (x, "a", 1, "person:op"), (x, "b", 2, "llm"))
    _turn(seat, (y, "a", 1, "person:op"), (y, "b", 2, "llm"))
    fired = list(seat.ch_vm.recent)
    assert fired and fired[-1]["from"] == y.eid and fired[-1]["to"] == y.eid and fired[-1]["trig"].startswith(
        MEMBRANE_NS + "Trigger:")


def test_the_overlay_is_in_field_ids_with_live_states(seat):
    est = seat.entity_store
    for n in "abc":
        e = _toy(est, n)
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    ov = B.overlay(seat)
    last = ov["firings"][-1]
    assert last["from"] == field_id(MEMBRANE_NS + "Component:toy:c") == last["to"]
    assert last["state"] == "proposal" and ov["counts"]["proposal"] >= 1
    B.grant(seat, "Component:toy")
    assert B.overlay(seat)["firings"][-1]["state"] == "suggest"
    json.dumps(ov)


def test_lines_and_counts_read_one_state(seat):
    """Memo's #587 note: a firing's line and the counts must agree -- both read the
    trigger's own branch (what the learner gates on), never the target's."""
    est = seat.entity_store
    for n in "abc":
        e = _toy(est, n)
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    trig = est.docs[next(f["trig"] for f in B.overlay(seat)["firings"] if f["state"] == "proposal")]
    trig.properties["branch"] = "ha:light"            # the trigger's branch is not the target's kind
    B.grant(seat, "ha:light")
    ov = B.overlay(seat)
    mine = [f for f in ov["firings"] if f["trig"] == trig.eid]
    assert mine and all(f["state"] == "suggest" for f in mine) and ov["counts"]["suggest"] >= 1


def test_the_route_serves_it():
    import inspect
    from membrane import lanes
    src = inspect.getsource(lanes)
    assert '"/membrane/map/blurp"' in src and "_CHB.overlay(" in src


@NODE
def test_the_3d_layers():
    ov = {"firings": [
        {"from": "f:a", "to": "f:b", "state": "suggest", "ts": 10},
        {"from": "f:a", "to": "f:b", "state": "suggest", "ts": 11},        # the same pair: one line
        {"from": "f:c", "to": "f:c", "state": "shadow", "ts": 12},         # same entity: a halo
        {"from": "f:d", "to": "f:z", "state": "proposal", "ts": 13},       # f:z not on the map: dropped
    ]}
    js = ("const L=require(%r); const has=(id)=>id!=='f:z';"
          "process.stdout.write(JSON.stringify(L.blurpLayers(%s, has, 10.5)))") % (str(UI / "field3d.js"), json.dumps(ov))
    out = json.loads(subprocess.check_output(["node", "-e", js]))
    assert out["lines"]["suggest"] == [["f:a", "f:b"]] and out["lines"]["shadow"] == [] and out["lines"]["proposal"] == []
    assert out["halos"] == [{"id": "f:c", "state": "shadow"}]
    assert [(p["a"], p["b"]) for p in out["fresh"]] == [("f:a", "f:b"), ("f:c", "f:c")]     # newer than 10.5
    assert out["last"] == 13
