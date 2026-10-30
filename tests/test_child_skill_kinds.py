"""The child skill tree on the toys: a skill is a kind of guess that keeps coming
true on any toy (Dima, 2026-10-30). Each guess carries its kind, read from who
made its cause: Blurp's own move is first reach, someone else's is meet an other."""
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
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    TB.bind(srv)
    B.attach(srv)
    srv.ch_purse.awake = True
    srv.ch_vm.learner.toy_open = lambda name: True
    yield srv
    srv.ch_vm.detach()
    TB._SEAT["srv"] = None


def _kinds(srv):
    return {(t.properties or {}).get("kind") for t in srv.ch_vm.triggers()
            if (t.properties or {}).get("owner") == CH.SELF}


def test_a_guess_from_blurps_own_nudge_is_first_reach(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    assert _kinds(seat) == {"nudge"}


def test_a_guess_from_a_persons_poke_is_meet_an_other(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    before = {t.eid for t in seat.ch_vm.triggers()}
    TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    new = [t for t in seat.ch_vm.triggers() if t.eid not in before
           and (t.properties or {}).get("owner") == CH.SELF]
    assert new and {(t.properties or {}).get("kind") for t in new} == {"meet"}


def test_the_models_poke_is_an_other_too(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    before = {t.eid for t in seat.ch_vm.triggers()}
    TB.poke("toy:ball", "roll", by="model"); seat.ch_vm.drain()
    new = [t for t in seat.ch_vm.triggers() if t.eid not in before
           and (t.properties or {}).get("owner") == CH.SELF]
    assert new and {(t.properties or {}).get("kind") for t in new} == {"meet"}
