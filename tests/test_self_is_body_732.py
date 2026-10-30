"""#732: Blurp's body IS `Component:self` (CH.SELF), not a second `Component:self:body`.
Blurp is where `self.at` says; its own move is a granted guess writing `self.at`
live (counted `nudged`); the current's push is its own reading, `self.current`
(counted `ticked`), so the two never overwrite each other."""
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import ch_body as BODY  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

OLD = CH.MEMBRANE_NS + "Component:self:body"


def _v(*xs):
    v = np.array(xs, dtype=float)
    return v / np.linalg.norm(v)


class Field:
    """The field's corner the body reads: entity states, the current, its generation."""
    def __init__(self):
        self.states = {field_id(B.SEAT_FID): _v(0, 0, 1), field_id("w:a"): _v(1, 0, 0),
                       field_id("w:b"): _v(0, 1, 0), field_id("w:c"): _v(1, 1, 0)}
        self.cur, self.gen, self.near = _v(0, 1, 0), 0, "w:b"

    def get(self, fid):
        v = self.states.get(fid)
        return None if v is None else types.SimpleNamespace(state=v)

    def current(self):
        return self.cur

    def current_gen(self):
        return self.gen

    def recall(self, v, k=1, by="state"):
        return [(self.near, 1.0)]


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=Field(), membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    TB.bind(srv)
    B.attach(srv)
    srv.ch_purse.awake = True
    seen = []
    W.on_write(seen.append)
    srv.seen = seen
    yield srv
    W.off_write(seen.append)
    srv.ch_vm.detach()
    TB._SEAT["srv"] = None


def _self(srv):
    return srv.entity_store.docs[CH.SELF].properties


def _look(srv):
    return [t for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF
            and (t.properties or {}).get("kind") == "reach"]


def test_the_readings_land_on_self_and_there_is_no_second_body(seat):
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(1, 1, 0), near="w:b", gen=1)
    p = _self(seat)
    assert p["nudged"] == 1 and p["ticked"] == 1 and p["current"] == "w:b" and p["closer"] is True
    assert "at" not in p                               # the world never writes where Blurp is
    assert OLD not in seat.entity_store.docs
    assert BODY.BODY == CH.SELF
    assert not hasattr(L, "BODY") and not hasattr(L, "BODY_NAME")


def test_the_body_is_where_self_is_not_where_the_newest_firing_watched(seat):
    vm = seat.ch_vm
    B._show_body(seat, vm)
    assert list(seat._ch_body_at) == list(_v(0, 0, 1))          # at the seat before any move
    vm.recent.append({"trig": "x", "from": CH.SELF, "to": CH.SELF, "ts": 0, "mode": "shadow"})
    ent = seat.entity_store.docs.get(CH.SELF) or QuantumEntity(eid=CH.SELF, cls="Component", properties={})
    ent.properties["at"] = "w:c"
    seat.entity_store.upsert(ent, embedder=None)
    B._show_body(seat, vm)
    assert list(seat._ch_body_at) == list(_v(1, 1, 0))          # self.at, whatever fired


def _teach_and_grant_look(seat):
    f = seat.efield
    B._show_body(seat, seat.ch_vm)                               # places the body
    f.gen, f.near = 1, "w:b"
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()           # the current pushes: ticked, current
    looks = _look(seat)
    assert looks and all(t.properties["write"][0].endswith(".at") for t in looks)
    for t in looks:
        seat.ch_vm.learner._hit(t.eid)
        seat.ch_vm.learner._hit(t.eid)
    assert B.grant(seat, "skill:reach")


def test_a_granted_look_guess_moves_blurp_for_real_and_counts_nudged(seat):
    _teach_and_grant_look(seat)
    f = seat.efield
    f.gen, f.near = 2, "w:c"
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()           # the push fires the granted look
    assert _self(seat)["at"] == "w:b"
    assert any(w.get("eid") == CH.SELF and w.get("prop") == "at"
               and str(w.get("writer")).startswith("trigger:") for w in seat.seen)
    before = int(_self(seat).get("nudged") or 0)
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()           # the body is where self.at says
    assert list(seat._ch_body_at) == list(_v(0, 1, 0))
    assert int(_self(seat)["nudged"]) == before + 1


def test_the_push_and_blurps_move_never_overwrite_each_other(seat):
    _teach_and_grant_look(seat)
    f = seat.efield
    f.gen, f.near = 2, "w:c"
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()
    ticked = int(_self(seat)["ticked"])
    f.gen, f.near = 3, "w:a"
    for t in _look(seat):                                        # a world reading with no live move
        seat.ch_vm.learner._bal[t.eid] = -1.0
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()
    p = _self(seat)
    assert p["current"] == "w:a" and p["at"] == "w:b"           # the push moved its reading, not Blurp
    assert int(p["ticked"]) == ticked + 1


def test_the_old_body_and_its_triggers_go_once(seat):
    est = seat.entity_store
    est.upsert(QuantumEntity(eid=OLD, cls="Component", properties={"name": "self:body"}), embedder=None)
    seat.ch_vm.define("cand-old", owner=CH.SELF, when=[["changed", "?x.ticked"]],
                      where=[["isa", "?x", "Component"], ["is", "?y", OLD]], write=["?y.at", "'w:a'"])
    keep = seat.ch_vm.define("cand-keep", owner=CH.SELF, when=[["changed", "?x.ticked"]],
                             where=[["isa", "?x", "Component"]], write=["?x.at", "'w:a'"])
    out = B.retire_old_body(seat)
    assert OLD not in est.docs
    assert CH.MEMBRANE_NS + "Trigger:cand-old" not in est.docs and keep in est.docs
    assert out["removed"] == 2
    assert B.retire_old_body(seat)["removed"] == 0
