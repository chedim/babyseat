"""#576 C: a learned call acts through a write -- `<method>.call = {object, args}`
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


def test_a_call_write_runs_the_method_once(monkeypatch):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None)
    m = QuantumEntity(eid=MEMBRANE_NS + "tool:toy.ping", cls="tool",
                      properties={"name": "toy.ping", "method_name": "ping", "command": "echo hi"})
    est.upsert(m)
    calls = []
    monkeypatch.setattr(night, "method_call",
                        lambda srv_, est_, fld, mem, obj, meth, args=None, **k: calls.append(
                            (obj, meth, args, (W.current() or {}).get("id"))) or {"ok": True, "status": "ok", "head": "hi"})
    A.start(srv)
    try:
        with W.source("person", "op") as ch, W.as_writer("trigger:t"):
            est.docs[m.eid].properties["call"] = {"object": "membrane::toy:x", "args": {"n": 1}}
            est.upsert(est.docs[m.eid])
        A.drain()
    finally:
        A.stop()
    assert calls == [("membrane::toy:x", "ping", {"n": 1}, ch["id"])]
    assert est.docs[m.eid].properties["call"] == "" and "call_result" in est.docs[m.eid].properties
