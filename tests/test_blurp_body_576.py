"""#576: Blurp's body on the map (the operator chose A). The old cycle published
the amoeba's points; #580 retired it and the body vanished, so the "Blurp" and
"Blurp's eyes" cameras had nothing to follow. Each exchange with Blurp's process
sends the body. Since #732 Blurp is where `self` is: at the entity in `self.at`
(its own move), looking at `self.current` (where the current is), and at the seat
before any move. What its firings watched never places it. Asleep or off, no
body is sent."""
import os
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
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from tests.test_blurp_overlay_576 import _toy, _turn  # noqa: E402


@pytest.fixture(autouse=True)
def _commands_world(monkeypatch):
    """#727 gates the LLM's command branches off (toys first); this file tests that kept code."""
    monkeypatch.setattr(L, "WORLD", "commands")


class _Field:
    def __init__(self):
        self.vecs = {"seat:self": [1.0, 0.0, 0.0]}

    def get(self, fid):
        v = self.vecs.get(fid)
        return types.SimpleNamespace(state=v) if v is not None else None


class _Pulse:
    def __init__(self):
        self.sent = []

    def self_cycle(self, points, stats):
        self.sent.append((points, stats))


@pytest.fixture
def seat(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=_Field(), membrane=None, paused=False)
    srv._field_pulse = _Pulse()
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    B.attach(srv)
    yield srv
    srv.ch_vm.detach()


def test_before_any_firing_it_rests_at_the_seat(seat):
    B.exchange(seat, {"awake": True, "mode": "on"})
    pts, _ = seat._field_pulse.sent[-1]
    assert pts["at"] == [1.0, 0.0, 0.0] and pts.get("aim") is None


def _self_at(seat, **props):
    from membrane.entities import QuantumEntity
    est = seat.entity_store
    me = est.docs.get(CH.SELF) or QuantumEntity(eid=CH.SELF, cls="Component", properties={})
    me.properties.update(props)
    est.upsert(me, embedder=None)


def test_it_sits_where_self_is_and_looks_at_the_current(seat):
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    x, y = _toy(est, "x"), _toy(est, "y")
    seat.efield.vecs[field_id(x.eid)] = [0.0, 1.0, 0.0]
    seat.efield.vecs[field_id(y.eid)] = [0.0, 0.0, 1.0]
    _self_at(seat, at=x.eid, current=y.eid)
    B.exchange(seat, {"awake": True, "mode": "on"})
    pts, stats = seat._field_pulse.sent[-1]
    assert pts["at"] == [0.0, 1.0, 0.0] and pts["aim"] == [0.0, 0.0, 1.0]
    assert pts["from"] == [1.0, 0.0, 0.0]                       # it glides from the seat
    assert stats["rung"] == 0                                  # the tag's level: no branch granted


def test_a_firing_never_places_it(seat):
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})          # its purse opens on the first report
    for n in "abc":
        e = _toy(est, n)
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    trig = next(r["trig"] for r in seat.ch_vm.recent if seat.ch_vm._is_selfs(est.docs[r["trig"]]))
    x, y = _toy(est, "x"), _toy(est, "y")
    seat.efield.vecs[field_id(x.eid)] = [0.0, 1.0, 0.0]
    seat.efield.vecs[field_id(y.eid)] = [0.0, 0.0, 1.0]
    seat.ch_vm.recent.append({"trig": trig, "from": x.eid, "to": y.eid, "ts": 1e12, "mode": "shadow"})
    B.exchange(seat, {"awake": True, "mode": "on"})
    pts, _ = seat._field_pulse.sent[-1]
    assert pts["at"] == [1.0, 0.0, 0.0] and pts.get("aim") is None   # still at the seat: it never moved


def test_asleep_or_off_no_body(seat):
    B.exchange(seat, {"awake": False, "asleep": True, "mode": "on"})
    B.exchange(seat, {"awake": True, "mode": "off"})
    assert seat._field_pulse.sent == []
