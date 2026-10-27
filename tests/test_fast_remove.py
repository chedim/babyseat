"""Removing an entity took over a minute on the live seat: remove() saved the whole
store (138 MB) in the request thread. It now marks the store dirty like every other
write (one coalesced background save) -- gone from memory at once, on disk soon."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _store(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    for n in ("a", "b", "c"):
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + f"Component:{n}", cls="Component", properties={"name": n}))
    est.flush()
    return est


def test_remove_does_not_save_in_the_callers_thread(tmp_path, monkeypatch):
    est = _store(tmp_path)
    saves = []
    monkeypatch.setattr(est, "save", lambda: saves.append(1))
    assert est.remove(MEMBRANE_NS + "Component:a") == 1
    assert est.remove_many([MEMBRANE_NS + "Component:b"]) == 1
    assert saves == [] and est._dirty                      # coalesced, not now


def test_a_removal_reaches_disk_on_the_next_save(tmp_path):
    est = _store(tmp_path)
    est.remove(MEMBRANE_NS + "Component:a")
    est.flush()
    doc = json.loads((tmp_path / "e.json").read_text())
    assert MEMBRANE_NS + "Component:a" not in doc["docs"] and MEMBRANE_NS + "Component:c" in doc["docs"]
