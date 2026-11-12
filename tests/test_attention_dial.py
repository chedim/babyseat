"""THE ALERT DIAL is an entity (the operator, 2026-11-12: everything moves to cliffhanger, no fallback, no new VM
primitive). `Attention:seat` {budget, window_min, mode}; one write sets it -- `Attention:seat.dial = {budget: 3}` --
and the pack (packs/attention.c1f) validates it with the ranges the old tool had and leaves a `dial_result` receipt.
The Python AlertGate (the gate itself moves with the event dispatch) reads its dial from the entity. The `attention`
tool, its route and attention.json are gone."""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import attention as AT  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

EID = "Attention:seat"


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    FL.boot_started(est)
    live.settle()
    yield est, live
    FL.stop(srv)


def _write(est, live, prop, value):
    d = est.docs[EID]
    d.properties = {**d.properties, prop: value}
    est.upsert(d, embedder=None)
    live.settle()
    return est.docs[EID].properties


def test_the_seat_starts_with_the_default_dial(seat):
    est, _ = seat
    p = est.docs[EID].properties
    assert (p["budget"], p["window_min"], p["mode"]) == (6, 10, "interrupt")


def test_a_boot_keeps_a_dial_the_model_set(seat):
    est, live = seat
    _write(est, live, "dial", {"budget": 2})
    FL.boot_started(est); live.settle()
    assert est.docs[EID].properties["budget"] == 2


def test_one_write_sets_the_given_fields_only(seat):
    est, live = seat
    p = _write(est, live, "dial", {"budget": 3, "mode": "digest"})
    assert (p["budget"], p["window_min"], p["mode"]) == (3, 10, "digest")
    assert p["dial_result"]["ok"] is True


def test_out_of_range_values_are_refused_with_the_old_words(seat):
    est, live = seat
    p = _write(est, live, "dial", {"budget": 51})
    assert p["budget"] == 6 and p["dial_result"]["ok"] is False and "0..50" in json.dumps(p["dial_result"])
    p = _write(est, live, "dial", {"window_min": 0})
    assert p["window_min"] == 10 and "1..1440" in json.dumps(p["dial_result"])
    p = _write(est, live, "dial", {"mode": "loud"})
    assert p["mode"] == "interrupt" and "interrupt" in json.dumps(p["dial_result"])


def test_the_alert_gate_reads_its_dial_from_the_entity(seat):
    est, live = seat
    gate = AT.AlertGate(est)
    _write(est, live, "dial", {"budget": 1})
    assert gate.allow({"kind": "watch", "entity": "a"}) is True
    assert gate.allow({"kind": "watch", "entity": "b"}) is False      # past the budget: into the digest
    assert gate.settings() == {"budget": 1, "window_min": 10, "mode": "interrupt"}
    _write(est, live, "dial", {"mode": "digest"})
    assert gate.mode == "digest"


def test_the_attention_tool_the_route_and_the_file_are_gone():
    assert "attention" not in M.METHOD_TOOLS and "attention" not in M.TOOL_IMPLS
    assert not [t for t in M.METHOD_TOOL_DEFS if t["name"] == "attention"]
    root = Path(__file__).resolve().parent.parent / "membrane"
    assert '"/membrane/attention"' not in (root / "lanes.py").read_text()
    assert "_attention_path" not in (root / "opencode.py").read_text()


def test_an_old_attention_json_comes_over_once(seat, tmp_path):
    est, live = seat
    est.docs.pop(EID, None)
    f = tmp_path / "attention.json"
    f.write_text(json.dumps({"budget": 4, "window_min": 30, "mode": "digest"}))
    assert AT.migrate_dial(est, str(f)) is True
    assert (est.docs[EID].properties["budget"], est.docs[EID].properties["window_min"],
            est.docs[EID].properties["mode"]) == (4, 30, "digest")
    assert not f.exists() and (tmp_path / "attention.json.migrated").exists()
    assert AT.migrate_dial(est, str(f)) is False


def test_the_seat_wires_the_gate_to_the_store_after_the_store_exists():
    """Live in a throwaway boot (2026-11-12): the gate was built before srv.entity_store existed and the dial went off."""
    src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
    made = src.index("srv.entity_store = EntityStore(")
    wired = src.index("srv._alert_gate.est = srv.entity_store")
    assert made < wired
    assert "AlertGate(srv.entity_store)" not in src
