"""#963 (the operator 2026-11-10): MCP servers and their tools as field entities, a .c1f library (membrane/packs/
mcp.c1f) over a thin I/O adapter (membrane/mcpfield.py). Runs a real stdio server (tests/fixtures/mcp_echo_server.py)."""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")
pytest.importorskip("mcp", reason="the MCP client library")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import mcpfield as MF  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

ECHO = str(Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py")
S = "McpServer:echo"


def _wait(cond, timeout=30.0):
    t = time.time() + timeout
    while time.time() < t:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None)
    est._srv = srv
    AD.start(srv)
    live = FL.start(srv)
    MF.start(srv)
    srv.live = live
    yield srv
    MF.stop()
    FL.stop(srv)
    AD.stop()


def _p(srv, eid):
    d = srv.entity_store.docs.get(eid)
    return (d.properties or {}) if d is not None else {}


def _set(srv, eid, cls, writer="person:owner", **props):
    est = srv.entity_store
    d = est.docs.get(eid)
    with W.as_writer(writer):
        if d is None:
            est.upsert(QuantumEntity(eid=eid, cls=cls, properties=props), embedder=None)
        else:
            d.properties = {**(d.properties or {}), **props}
            est.upsert(d, embedder=None)


def _server(srv, **extra):
    _set(srv, S, "McpServer", name="echo", transport="stdio", command=sys.executable, args=[ECHO], **extra)
    _set(srv, S, "McpServer", enabled=True)
    assert _wait(lambda: _p(srv, S).get("state") == "up"), _p(srv, S)


def test_an_enabled_server_lists_its_tools_as_entities(seat):
    _server(seat)
    assert _p(seat, S)["tool_count"] == 4
    add = _p(seat, "McpTool:echo:add")
    assert add["description"] == "Add two numbers." and add["server"] == S and add["listed"] is True
    assert add["input_schema"]["properties"]["a"]["type"] == "integer"
    assert _wait(lambda: _p(seat, "McpTool:echo:add").get("callable") is True)
    assert _p(seat, "McpResource:echo:memo://greeting")["resource_of"] == S


def test_a_written_call_runs_the_tool_and_leaves_a_receipt(seat):
    _server(seat)
    _set(seat, "McpTool:echo:add", "McpTool", **{"call:run": {"id": "r1", "args": {"a": 2, "b": 3}}})
    assert _wait(lambda: _p(seat, "McpTool:echo:add").get("call:run_result", {}).get("id") == "r1")
    r = _p(seat, "McpTool:echo:add")["call:run_result"]
    assert r["status"] == "ok" and r["rc"] == 0 and r["head"] == "5", r


def test_secret_args_scripts_and_a_down_server_are_refused(seat):
    _server(seat)
    _set(seat, "McpTool:echo:echo", "McpTool", **{"call:run": {"id": "r2", "args": {"text": "x", "password": "p"}}})
    assert _wait(lambda: _p(seat, "McpTool:echo:echo").get("call:run_result", {}).get("id") == "r2")
    r = _p(seat, "McpTool:echo:echo")["call:run_result"]
    assert r["rc"] == 2 and "secret" in r["head"] and "password" not in json.dumps(_p(seat, "McpTool:echo:echo").get("call:run"))
    _set(seat, "McpTool:echo:echo", "McpTool", writer="script:x", **{"call:run": {"id": "r3", "args": {"text": "x"}}})
    assert _wait(lambda: _p(seat, "McpTool:echo:echo").get("call:run_result", {}).get("id") == "r3")
    assert _p(seat, "McpTool:echo:echo")["call:run_result"]["rc"] == 2
    _set(seat, S, "McpServer", enabled=False)
    assert _wait(lambda: _p(seat, S).get("state") == "off")
    assert _wait(lambda: _p(seat, "McpTool:echo:echo").get("callable") is False)
    _set(seat, "McpTool:echo:echo", "McpTool", **{"call:run": {"id": "r4", "args": {"text": "x"}}})
    assert _wait(lambda: _p(seat, "McpTool:echo:echo").get("call:run_result", {}).get("id") == "r4")
    assert "not up" in _p(seat, "McpTool:echo:echo")["call:run_result"]["head"]


def test_secrets_come_from_the_environment_and_are_never_stored(seat, monkeypatch):
    monkeypatch.setenv("MCP_TEST_TOKEN", "s3cr3t-value")
    _server(seat, env_from={"TOKEN": "MCP_TEST_TOKEN"})
    _set(seat, "McpTool:echo:env", "McpTool", **{"call:run": {"id": "e1", "args": {"name": "TOKEN"}}})
    assert _wait(lambda: _p(seat, "McpTool:echo:env").get("call:run_result", {}).get("id") == "e1")
    assert _p(seat, "McpTool:echo:env")["call:run_result"]["head"] == "s3cr3t-value"     # the server got it
    stored = json.dumps({e: d.properties for e, d in seat.entity_store.docs.items() if e != "McpTool:echo:env"})
    assert "s3cr3t-value" not in stored


def test_a_server_that_can_not_start_is_failed_with_its_error(seat):
    _set(seat, "McpServer:broken", "McpServer", name="broken", transport="stdio", command="/nonexistent/mcp-server")
    _set(seat, "McpServer:broken", "McpServer", enabled=True)
    assert _wait(lambda: _p(seat, "McpServer:broken").get("state") == "failed"), _p(seat, "McpServer:broken")
    assert _p(seat, "McpServer:broken")["link_error"]


def test_a_tool_the_server_stops_listing_stays_marked_unlisted(seat):
    _server(seat)
    tools = [t for t in _p(seat, S)["listed_tools"] if t["name"] != "slow"]
    _set(seat, S, "McpServer", writer=MF.WRITER, listed_tools=tools)
    assert _wait(lambda: _p(seat, "McpTool:echo:slow").get("listed") is False)
    assert _p(seat, "McpTool:echo:add")["listed"] is True


def test_the_claude_rung_mounts_enabled_servers_with_their_tools(seat, monkeypatch):
    monkeypatch.setenv("MCP_TEST_TOKEN", "t")
    _server(seat, env_from={"TOKEN": "MCP_TEST_TOKEN"})
    servers = MF.sdk_servers(seat.entity_store)
    assert servers["echo"] == {"type": "stdio", "command": sys.executable, "args": [ECHO], "env": {"TOKEN": "t"}}
    assert "mcp__echo__add" in MF.sdk_tool_names(seat.entity_store)


def test_other_clients_configs_import_disabled_without_their_secrets(seat, tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "gh": {"command": "npx", "args": ["-y", "gh-mcp"], "env": {"GITHUB_TOKEN": "ghp_literal"}},
        "docs": {"type": "http", "url": "https://example.test/mcp", "headers": {"Authorization": "Bearer x"}}}}))
    made = MF.import_configs(seat.entity_store, [cfg])
    assert sorted(made) == ["McpServer:docs", "McpServer:gh"]
    gh = _p(seat, "McpServer:gh")
    assert gh["enabled"] is False and gh["command"] == "npx" and gh["env_from"] == {"GITHUB_TOKEN": "GITHUB_TOKEN"}
    assert "ghp_literal" not in json.dumps(gh) and "Bearer x" not in json.dumps(_p(seat, "McpServer:docs"))
    assert MF.import_configs(seat.entity_store, [cfg]) == []                       # already there: left as it is


def test_an_import_is_a_write_and_its_receipt_lists_what_was_made(seat, tmp_path, monkeypatch):
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "fs-mcp"]}}}))
    monkeypatch.setattr(MF, "config_paths", lambda cwd=None: [cfg])
    _set(seat, MF.IMPORT_EID, "Component", name="mcp")              # a birth carrying a branch is no effect
    _set(seat, MF.IMPORT_EID, "Component", **{"import": "all"})
    assert _wait(lambda: _p(seat, MF.IMPORT_EID).get("import_result", {}).get("made") == ["McpServer:fs"])
    assert _p(seat, "McpServer:fs")["enabled"] is False
