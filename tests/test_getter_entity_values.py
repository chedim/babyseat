"""A getter (or setter) may store entity values: `Box.open getter: [t for t in instances('Thing') if t.on]` used to
fail with "cannot write a EntityView" and keep the old value (live 2026-11-12: Memo's banner could not keep a list of
its open goals). An entity is stored as a reference {"$ref": eid} and reads back as the entity."""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

PACK = """PACK ents 1
Thing.label getter: this.name
Box.open getter: [t for t in instances('Thing') if t.on]
Box.names getter: [t.name for t in this.open]
Box.first getter: head([t for t in instances('Thing') if t.on], 1)
"""


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    yield est, live
    FL.stop(srv)


def _thing(est, key, on):
    est.upsert(QuantumEntity(eid=f"Thing:{key}", cls="Thing", properties={"name": key, "on": on}), embedder=None)


def test_a_getter_keeps_a_list_of_entities_and_another_reads_it(seat):
    est, live = seat
    assert live._pack("ents", PACK, None)
    live._mirror()
    for k, on in (("a", True), ("b", False), ("c", True)):
        _thing(est, k, on)
    est.upsert(QuantumEntity(eid="Box:x", cls="Box", properties={"name": "box"}), embedder=None)
    live.settle()
    p = est.docs["Box:x"].properties
    assert p["open"] == [{"$ref": "Thing:a"}, {"$ref": "Thing:c"}]
    assert p["names"] == ["a", "c"]
    assert p["first"] == [{"$ref": "Thing:a"}]
    assert "refused" not in p
