"""#588: a composite method's receipt lists the steps that ran (each with its rc) and
a halt names the failing step with its output. #589: entity_remove offers `force`;
removing a class that owns methods is refused unless forced, and forced it takes the
methods (and their step nodes) with it -- nothing is orphaned."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _class_with_methods(est):
    c = QuantumEntity(eid=MEMBRANE_NS + "Component:scratch", cls="Component", properties={"name": "scratch"})
    est.upsert(c)
    N.method_define(None, est, None, None, c.eid, "hi", "echo hi")
    N.method_define(None, est, None, None, c.eid, "two", "", steps=[{"name": "s1", "command": "echo 1"},
                                                                  {"name": "s2", "command": "echo 2"}])
    return c


def test_the_composite_receipt_lists_its_steps_and_names_the_halt(monkeypatch):
    out = {"object": "membrane::Component:scratch", "method": "go", "via": "own", "mro": ["scratch"],
           "status": "partial", "rc": 3, "outcome": None, "fed": "", "head": "oops",
           "error": "step failed: membrane::shell:scratch.go:fail -- rc=3 error",
           "steps": {"first": {"rc": 0, "status": "ok", "head": "ok"}, "fail": {"rc": 3, "status": "error", "head": "oops"}}}
    monkeypatch.setattr(M, "_http", lambda *a, **k: out)
    text = M.tool_method_call({"object": "scratch", "method": "go"}, 0)
    assert "ran: first (rc 0) > fail (rc 3)" in text
    assert "halted at fail: rc 3" in text and "oops" in text and "None" not in text.split("\n")[0]


def test_entity_remove_offers_force():
    tool = next(t for t in M.TOOLS if t["name"] == "entity_remove")
    assert "force" in tool["inputSchema"]["properties"]


def test_a_class_with_methods_is_refused_then_removed_with_them():
    est = _est()
    c = _class_with_methods(est)
    owned = [e for e, d in est.docs.items() if c.eid in ((d.properties or {}).get("method_of") or [])]
    nodes = [e for e in est.docs if any(e.startswith(m + ":") for m in owned)]
    assert len(owned) == 2 and nodes
    r = N.remove_entity(est, c.eid, force=False)
    assert r["removed"] == 0 and "2 methods" in r["refused"]
    r = N.remove_entity(est, c.eid, force=True)
    assert r["removed"] == 1 + len(owned) + len(nodes)
    assert not any(e in est.docs for e in [c.eid] + owned + nodes)


def test_a_plain_entity_still_removes_without_force():
    est = _est()
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:plain", cls="Component", properties={"name": "plain"})
    est.upsert(e)
    assert N.remove_entity(est, e.eid)["removed"] == 1 and e.eid not in est.docs
