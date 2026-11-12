"""learn is a WRITE (the operator, 2026-11-12: everything moves to cliffhanger, no Python behind it): `Memory:seat.learn =
{lesson}`. The pack (packs/memory.c1f) holds the policy -- the agent-writes switch, an empty lesson, fenced code, the
length of a note -- and answers a refusal in `learn_result`; a fine lesson goes to the node's driver (`Memory:seat.note`),
which writes the note event through the memory store and leaves `note_result`. The tool, its route and its bridge are gone."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

EID = "Memory:seat"


def _seat(monkeypatch, tmp_path, agent_writes=True):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    learned = []
    monkeypatch.setattr(L, "_efield_learn", lambda srv, payload, src: (learned.append((payload, src)), " Written as note #7.")[1])
    monkeypatch.setattr(L, "_write_audit", lambda *a, **k: None)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=types.SimpleNamespace(),
                                agent_writes=agent_writes, _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, facts={"agent_writes": agent_writes})
    return srv, est, learned


def _write(est, prop, value):
    d = est.docs[EID]
    d.properties = {**d.properties, prop: value}
    est.upsert(d, embedder=None)


def _until(fn, timeout=6.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


@pytest.fixture
def seat(monkeypatch, tmp_path):
    srv, est, learned = _seat(monkeypatch, tmp_path)
    yield srv, est, learned
    AD.stop()
    FL.stop(srv)


def test_a_fine_lesson_is_written_as_a_note(seat):
    srv, est, learned = seat
    assert _until(lambda: EID in est.docs)
    _write(est, "learn", {"lesson": "the bilge pump runs on the 12 V bus"})
    r = _until(lambda: est.docs[EID].properties.get("note_result"))
    assert "note #7" in r and learned == [("the bilge pump runs on the 12 V bus", "model")]
    assert est.docs[EID].properties["learn_result"]["written"] is True


@pytest.mark.parametrize("lesson,why", [
    ("", "nothing to learn"), ("   ", "nothing to learn"),
    ("see ```code``` here", "fenced code blocks"), ("x" * 2001, "too long")])
def test_a_lesson_that_is_not_memory_material_is_refused_and_nothing_is_written(seat, lesson, why):
    srv, est, learned = seat
    assert _until(lambda: EID in est.docs)
    _write(est, "learn", {"lesson": lesson})
    r = _until(lambda: est.docs[EID].properties.get("learn_result"))
    assert r["written"] is False and why in r["reason"]
    time.sleep(0.3)
    assert learned == [] and "note_result" not in est.docs[EID].properties


def test_a_lesson_of_exactly_the_cap_is_fine(seat):
    srv, est, learned = seat
    assert _until(lambda: EID in est.docs)
    _write(est, "learn", {"lesson": "x" * 2000})
    assert _until(lambda: est.docs[EID].properties.get("note_result"))
    assert len(learned[0][0]) == 2000


def test_the_agent_writes_switch_refuses_everything(monkeypatch, tmp_path):
    srv, est, learned = _seat(monkeypatch, tmp_path, agent_writes=False)
    try:
        assert _until(lambda: EID in est.docs)
        _write(est, "learn", {"lesson": "a fine lesson"})
        r = _until(lambda: est.docs[EID].properties.get("learn_result"))
        assert r["written"] is False and "agent writes are off" in r["reason"] and learned == []
    finally:
        AD.stop()
        FL.stop(srv)


def test_a_note_the_field_declines_says_so(seat, monkeypatch):
    srv, est, learned = seat
    monkeypatch.setattr(L, "_efield_learn", lambda srv, payload, src: "")
    assert _until(lambda: EID in est.docs)
    _write(est, "learn", {"lesson": "a duplicate of what the field already has"})
    r = _until(lambda: est.docs[EID].properties.get("note_result"))
    assert "guards" in r and est.docs[EID].properties.get("note_rc") not in (None, 0)


def test_the_tool_the_route_and_the_bridge_are_gone():
    assert "learn" not in M.METHOD_TOOLS and "learn" not in M.TOOL_IMPLS
    assert not [t for t in M.METHOD_TOOL_DEFS if t["name"] == "learn"]
    assert "def tool_learn" not in Path(M.__file__).read_text()
    lanes = Path(L.__file__).read_text()
    assert '"/membrane/learn"' not in lanes and "def _membrane_learn" not in lanes
