"""A toy write -- by the model or by another writer -- is appended, in order,
to the toy's write log with its chain, writer, toy, property and value, and the
log survives a reload (the files are the toy). It reuses writes.py's chain and
writer convention; there is no second log."""
import json
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

KEYS = {"chain", "writer", "toy", "property", "value"}


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    TB.bind(srv)
    yield srv
    TB._SEAT["srv"] = None


def test_model_and_other_writes_are_logged_in_order_and_survive_reload(seat):
    assert TB.add(seat, "ball")["ok"]
    TB.nudge("toy:ball")
    assert TB.poke("toy:ball", "roll", by="model")["ok"]
    assert TB.poke("toy:ball", "roll", by="Dima")["ok"]

    def check(recs):
        recs = [r for r in recs if r.get("toy") == "toy:ball"]
        assert recs, "no toy write was logged"
        assert all(KEYS <= set(r) for r in recs)
        assert all((r["chain"] or {}).get("id") for r in recs)
        writers = [r["writer"] for r in recs]
        assert {"model", "person:Dima"} <= set(writers)
        assert writers.index("model") < writers.index("person:Dima")
        assert all(r["property"] and "value" in r for r in recs)

    check(TB.write_log("toy:ball"))
    TB._forget()                                   # what a restart does: only the files remain
    check(TB.write_log("toy:ball"))
    lines = [json.loads(l) for l in TB._log_path("toy:ball").read_text().splitlines()]
    check(lines)                                   # the same per-toy .log.jsonl, appended


def _recs(name, what):
    return [r for r in TB.write_log(name) if r["property"] == what]


def test_tower_topple_and_lift_carry_their_payload_in_value(seat):
    assert TB.add(seat, "tower", params={"n": 3})["ok"]
    assert TB.poke("toy:tower", "knock", 2, by="model")["ok"]
    assert TB.poke("toy:tower", "rebuild", 2, by="model")["ok"]
    topple = _recs("toy:tower", "topple")[-1]
    lift = _recs("toy:tower", "lift")[-1]
    assert topple["value"] == {"block": 2, "blocks": [2, 3]}
    assert lift["value"] == {"block": 2}


def test_ball_roll_carries_its_payload_in_value(seat):
    assert TB.add(seat, "ball")["ok"]
    assert TB.poke("toy:ball", "roll", by="Dima")["ok"]
    assert _recs("toy:ball", "roll")[-1]["value"] == {"side": "self"}


def test_single_value_writes_keep_their_plain_value(seat):
    assert TB.add(seat, "die")["ok"]
    TB.nudge("toy:die")
    assert isinstance(_recs("toy:die", "change")[-1]["value"], int)


def test_record_outside_a_write_chain_is_flagged_synthetic(seat):
    from membrane import writes as W
    assert TB.add(seat, "ball")["ok"]
    assert W.current() is None
    assert TB.poke("toy:ball", "roll", by="Dima")["ok"]
    assert _recs("toy:ball", "roll")[-1].get("synthetic") is True


def test_record_inside_a_write_chain_is_not_synthetic(seat):
    from membrane import writes as W
    assert TB.add(seat, "ball")["ok"]
    with W.source("test", "chain"):
        assert TB.poke("toy:ball", "roll", by="Dima")["ok"]
    assert not _recs("toy:ball", "roll")[-1].get("synthetic")
