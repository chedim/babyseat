"""#150: entity_edit with a properties patch answered rc 0 'updated: class,
summary, links' -- a refused key (an engine-owned script_* key, a key past
the 16-key cap) vanished with no word, and even an applied patch was never
named. The edit now SAYS which keys it set and refuses the rest loudly."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est_with(name="t"):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    e = QuantumEntity(eid=MEMBRANE_NS + f"Component:{name}", cls="Component",
                      properties={"name": name})
    est.upsert(e)
    return est, e


def test_applied_properties_are_named():
    est, e = _est_with()
    res = est.edit(e.eid, properties={"host": "boat", "port": 8022})
    assert res["ok"]
    assert sorted(res["properties_set"]) == ["host", "port"]
    assert est.docs[e.eid].properties["host"] == "boat"


def test_an_engine_key_is_refused_loudly():
    est, e = _est_with()
    res = est.edit(e.eid, properties={"host": "boat", "script_failures": 0})
    assert res["ok"]                                  # the rest still lands
    assert res["properties_set"] == ["host"]
    assert "script_failures" in res["properties_refused"]


def test_only_refused_keys_is_an_error():
    est, e = _est_with()
    res = est.edit(e.eid, properties={"script_failures": 0})
    assert not res["ok"]
    assert "script_failures" in res["error"]


def test_keys_past_the_cap_are_refused_not_dropped():
    est, e = _est_with()
    res = est.edit(e.eid, properties={f"k{i}": i for i in range(18)})
    assert len(res["properties_set"]) == 16
    assert set(res["properties_refused"]) == {"k16", "k17"}


def test_the_tool_reply_names_the_patch():
    out = {"ok": True, "eid": "membrane::Component:t", "class": "Component",
           "summary": "", "links": [], "properties_set": ["host"],
           "properties_refused": {"script_failures": "engine-owned"}}
    with mock.patch.object(M, "_http", return_value=out):
        msg = M.tool_entity_edit({"spec": "t", "properties": {"host": "boat",
                                                              "script_failures": 0}}, 1)
    assert "host" in msg
    assert "script_failures" in msg and "refused" in msg.lower()
