"""#1043 (the operator 2026-11-10, in 0.7.5): two MCP tools for the field engine -- `pack` (list / read / write a .c1f
pack, its load error in the same reply) and `entity_write` (entity.prop = value: acting by writing, the receipt lands as
<prop>_result) -- so a model programs the field without method_define or a Pack entity by hand."""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    lv = FL.start(srv, thread=False)
    yield lv
    FL.stop(srv)


def test_write_a_pack_and_get_its_error_in_the_reply(live):
    ok = live.pack_op("write", "battery", "PACK battery 1\nBattery.low getter: this.soc < 20\n")
    assert ok["ok"] and ok["error"] == "" and "Battery" in ok["classes"]
    d = live.est.docs[FL.PACK_NS + "battery"]
    assert d.cls == FL.PACK_CLASS and "Battery.low" in d.properties["c1f"]
    bad = live.pack_op("write", "battery", "PACK battery 1\nBattery.low getter: this.soc <\n")
    assert not bad["ok"] and bad["error"]
    r = live.pack_op("show", "battery")                  # the text as written, with why it was refused
    assert r["c1f"].endswith("this.soc <\n") and r["error"]
    names = {p["name"]: p for p in live.pack_op("list")["packs"]}
    assert names["battery"]["source"] == "entity" and names["banner"]["source"] == "shipped"


def test_a_shipped_pack_is_not_overwritten(live):
    r = live.pack_op("write", "banner", "PACK banner 1\n")
    assert not r["ok"] and "IMPORT banner" in r["error"]


def test_the_tools_are_on_the_surface_before_method_define():
    names = [t["name"] for t in M.TOOLS]
    assert "pack" in names and "entity_write" in names and "write" not in names      # opencode owns `write` (files)
    assert names.index("pack") < names.index("method_define") and names.index("entity_write") < names.index("method_define")


def test_write_is_an_entity_edit_of_one_property(monkeypatch):
    sent = []
    monkeypatch.setattr(M, "_http", lambda port, m, path, body=None, **k: sent.append((path, body)) or {"ok": True, "eid": "chat:owner"})
    out = M.TOOL_IMPLS["entity_write"]({"spec": "chat:owner", "prop": "say", "value": "hello"}, 1)
    assert sent == [("/membrane/entity/edit", {"spec": "chat:owner", "properties": {"say": "hello"}})]
    assert "say_result" in out


def test_pack_tool_calls_the_seat(monkeypatch):
    sent = []
    monkeypatch.setattr(M, "_http", lambda port, m, path, body=None, **k: sent.append((path, body)) or {"ok": False, "error": "line 2: boom"})
    out = M.TOOL_IMPLS["pack"]({"action": "load", "name": "x", "c1f": "PACK x 1\n"}, 1)
    assert sent == [("/membrane/pack", {"action": "load", "name": "x", "c1f": "PACK x 1\n"})]
    assert "line 2: boom" in out
