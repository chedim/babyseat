"""Claude (the claude-agent rung) could not call a method on an object: its
tools are membrane's MCP TOOLS, and the method tools had implementations
(TOOL_IMPLS) but no schema there. Asked to run JS in the operator's page it
fell back to entity_link with 'presence.exec{...}', which the executor ran as
a shell line: rc=127 'presence.exec' not found (2026-10-23). method_call and
method_inspect now have schemas, so every MCP client, the rung included,
gets them; defining methods (code) stays off the MCP surface."""
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


def test_defining_code_stays_off_the_mcp_surface():
    assert _schema("method_define") is None
    assert _schema("method_each") is None


def test_the_claude_rung_gets_them():
    names = CA.tool_names()
    assert CA.TOOL_PREFIX + "method_call" in names
    assert CA.TOOL_PREFIX + "method_inspect" in names


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
