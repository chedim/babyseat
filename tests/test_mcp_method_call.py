"""Claude (the claude-agent rung) could not call a method on an object: its
tools are membrane's MCP TOOLS, and the method tools had implementations
(TOOL_IMPLS) but no schema there. Asked to run JS in the operator's page it
fell back to entity_link with 'presence.exec{...}', which the executor ran as
a shell line: rc=127 'presence.exec' not found (2026-10-23). method_call and
method_inspect now have schemas, so every MCP client, the rung included,
gets them. 2026-10-25: Memo "just runs commands instead of programming the
field" -- it could call methods but not make them, so method_define and
method_each have schemas too (the routes stay owner-only, #253)."""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import mcp_server as M  # noqa: E402


def _schema(name):
    return next((t for t in M.TOOLS if t["name"] == name), None)


def test_call_and_inspect_are_mcp_tools():
    for name in ("method_call", "method_inspect"):
        t = _schema(name)
        assert t is not None, name
        props = t["inputSchema"]["properties"]
        assert {"object", "method", "args"} <= set(props), name
        assert t["inputSchema"]["required"] == ["object", "method"]


def test_define_and_each_are_mcp_tools():
    d = _schema("method_define")
    assert d is not None
    assert {"on", "method", "command", "read_only", "guard", "steps",
            "abstract"} <= set(d["inputSchema"]["properties"])
    assert d["inputSchema"]["required"] == ["on", "method"]
    e = _schema("method_each")
    assert e is not None
    assert {"over", "method", "args", "max", "dry_run",
            "on_fail"} <= set(e["inputSchema"]["properties"])
    assert e["inputSchema"]["required"] == ["over", "method"]


def test_a_define_reaches_the_method_route():
    seen = {}

    def fake(port, method, path, body=None):
        seen.update(path=path, body=body)
        return {"ok": True, "method": "light.dim", "created": True, "params": ["level"]}
    with mock.patch.object(M, "_http", fake):
        out = M.TOOL_IMPLS["method_define"]({"on": "light", "method": "dim",
                                              "command": "echo dim {{level}}",
                                              "read_only": False}, 11500)
    assert seen["path"] == "/membrane/method/define"
    assert seen["body"]["on"] == "light" and seen["body"]["command"] == "echo dim {{level}}"
    assert "defined" in out


def test_an_each_reaches_the_method_route():
    seen = {}

    def fake(port, method, path, body=None):
        seen.update(path=path, body=body)
        return {"dry_run": True, "method": "dim", "over": "light", "n": 2, "max": 20,
                "members": ["light:a", "light:b"]}
    with mock.patch.object(M, "_http", fake):
        out = M.TOOL_IMPLS["method_each"]({"over": "light", "method": "dim", "dry_run": True}, 11500)
    assert seen["path"] == "/membrane/method/each"
    assert seen["body"]["over"] == "light" and seen["body"]["dry_run"] is True
    assert "light:a" in out


def test_the_claude_rung_gets_them():
    names = CA.tool_names()
    assert CA.TOOL_PREFIX + "method_call" in names
    assert CA.TOOL_PREFIX + "method_inspect" in names
    assert CA.TOOL_PREFIX + "method_define" in names
    assert CA.TOOL_PREFIX + "method_each" in names


def test_a_call_reaches_the_method_route():
    seen = {}

    def fake(port, method, path, body=None):
        seen.update(path=path, body=body)
        return {"object": "membrane::presence", "method": "presence.exec", "via": "membrane::presence",
                "status": "ok", "rc": 0, "outcome": "ok", "fed": "", "head": "Babyseat | ?view=decisions"}
    with mock.patch.object(M, "_http", fake):
        out = M.TOOL_IMPLS["method_call"]({"object": "presence", "method": "exec",
                                            "args": {"code": "return document.title"}}, 11500)
    assert seen["path"] == "/membrane/method/call"
    assert seen["body"]["object"] == "presence" and seen["body"]["method"] == "exec"
    assert seen["body"]["args"]["code"] == "return document.title"
    assert "Babyseat" in out
