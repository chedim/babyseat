"""k5: ONE call for an entity's history -- decision_map.entity_history(srv, spec)."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def test_entity_history_one_call():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    G = MEMBRANE_NS + "g"
    est.upsert(QuantumEntity(eid=G, cls="Goal", properties={
        "name": "ship", "status": "open", "receipt": "r1", "consequences": "c1"}))
    ef = SimpleNamespace(b=None, origin="o", deltas_of=lambda eid: [{"seq": 3, "eid": eid, "stream": "obs", "text": "moved"}])
    srv = SimpleNamespace(entity_store=est, efield=ef)
    h = DM.entity_history(srv, G)
    assert h["eid"] == G and h["cls"] == "Goal"
    assert h["properties"]["receipt"] == "r1"
    assert h["changes"][0]["seq"] == 3
    assert h["relations"] == []
    assert DM.entity_history(srv, "nope")["ok"] is False
