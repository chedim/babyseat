"""#576: Blurp's body on the map (the operator chose A). The old cycle published
the amoeba's points; #580 retired it and the body vanished, so the "Blurp" and
"Blurp's eyes" cameras had nothing to follow. Now each exchange with Blurp's
process puts the body where its newest firing happened (the watched entity),
looking at what that firing wrote; before any firing it rests at the seat.
Asleep or off, no body is sent."""
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from tests.test_blurp_overlay_576 import _toy, _turn  # noqa: E402


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


def test_it_sits_where_it_last_fired_and_looks_at_what_it_wrote(seat):
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    for i, n in enumerate("abc"):
        e = _toy(est, n)
        seat.efield.vecs[field_id(e.eid)] = [0.0, float(i + 1), 0.0]
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    f = [r for r in seat.ch_vm.recent if seat.ch_vm._is_selfs(est.docs[r["trig"]])][-1]
    B.exchange(seat, {"awake": True, "mode": "on"})
    pts, _ = seat._field_pulse.sent[-1]
    assert pts["at"] == seat.efield.vecs[field_id(f["from"])]
    assert pts["from"] == [1.0, 0.0, 0.0]                       # it glides from the seat


def test_asleep_or_off_no_body(seat):
    B.exchange(seat, {"awake": False, "asleep": True, "mode": "on"})
    B.exchange(seat, {"awake": True, "mode": "off"})
    assert seat._field_pulse.sent == []
