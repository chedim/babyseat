"""#576 C: a learned call acts through a write -- `<method>.invoke = {object, args}`
on a method entity runs that method (the `methods` adapter branch), once,
consumed, in the write's chain; the call records `last_call` as any call does."""
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as A  # noqa: E402
from membrane import night  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def test_an_invoke_write_runs_the_method_for_real(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None)
    obj = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(obj)
    assert night.method_define(None, est, None, None, obj.eid, "hello", "echo hi-{{who}}")["ok"]
    m = next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == "hello")
    A.start(srv)
    try:
        with W.source("person", "op"), W.as_writer("trigger:t"):
            m.properties["invoke"] = {"object": obj.eid, "args": {"who": "bob"}}
            est.upsert(m)
        A.drain()
    finally:
        A.stop()
    assert m.properties["invoke"] == "" and "hi-bob" in m.properties.get("invoke_result", "")
    assert m.properties["last_call"]["args"] == {"who": "bob"}
