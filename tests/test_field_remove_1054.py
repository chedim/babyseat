"""#1054 (Memo, 2026-11-10): a removed entity leaves the field engine -- on the seat (what read or referenced it
recalculates) and in every page following it (a `field.remove` event; the page calls remove(eid))."""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _Hub:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    hub = _Hub()
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=hub))
    est._srv = srv
    live = FL.start(srv, thread=False)
    est.upsert(QuantumEntity(eid=FL.PACK_NS + "probe", cls=FL.PACK_CLASS,
                             properties={"name": "probe", "c1f": "PACK probe 1\nProbe.twice getter: (this.n or 0) * 2\n"}),
               embedder=None)
    live.settle()
    yield srv, live, hub
    FL.stop(srv)


def test_a_removed_entity_leaves_the_seat_engine_and_the_pages(seat):
    srv, live, hub = seat
    srv.entity_store.upsert(QuantumEntity(eid="Probe:a", cls="Probe", properties={"n": 2}), embedder=None)
    live.settle()
    assert live.f.has("Probe:a")
    assert srv.entity_store.remove("Probe:a") == 1
    live.settle()
    assert not live.f.has("Probe:a"), "the seat's engine still holds the removed entity"
    assert {"type": "field.remove", "eid": "Probe:a", "cls": "Probe"} in hub.events


def test_the_page_applies_field_remove():
    js = (ROOT / "membrane" / "ui" / "fieldvm.js").read_text()
    assert "e.type === 'field.remove'" in js and "host.drop(e.eid)" in js and "this.f.remove(eid, this.now())" in js
