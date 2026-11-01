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
