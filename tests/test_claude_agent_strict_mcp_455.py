"""THE CLAUDE-AGENT RUNG SEES ONLY MEMBRANE'S MCP SERVER (issue #455).

A claude-agent-sdk query with tools=[] and setting_sources=[] but without
strict MCP config still offered the model the person's claude.ai
connectors (e.g. mcp__claude_ai_Claude_Docs__*) through the CLI login.
The rung's options carry strict_mcp_config, which the SDK turns into the
CLI's --strict-mcp-config: the CLI then loads only the --mcp-config the
SDK hands it, which is membrane's in-process server and nothing else.

These tests run the rung's real options through the installed SDK's own
command builder (no CLI is spawned, no login is needed), so an SDK that
dropped or renamed the flag would fail here. They skip when the optional
claude-agent extra is not installed.
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import mcp_server as MCP  # noqa: E402

HAVE_SDK = importlib.util.find_spec("claude_agent_sdk") is not None


@unittest.skipUnless(HAVE_SDK, "claude-agent-sdk (optional extra) not installed")
class TestStrictMcpArgv(unittest.TestCase):
    def _argv(self):
        import claude_agent_sdk as sdk
        from claude_agent_sdk._internal.transport.subprocess_cli import (
            SubprocessCLITransport)
        opts = CA.build_options(sdk, "sys", "claude-agent:haiku", stream=False,
                                port=9, env={})
        t = SubprocessCLITransport(prompt="hi", options=opts)
        t._cli_path = "claude"                 # never looked up, never spawned
        return t._build_command()

    def test_the_cli_runs_with_strict_mcp_config(self):
        self.assertIn("--strict-mcp-config", self._argv())

    def test_the_only_mcp_server_is_membranes(self):
        argv = self._argv()
        self.assertEqual(argv.count("--mcp-config"), 1)
        cfg = json.loads(argv[argv.index("--mcp-config") + 1])
        self.assertEqual(set(cfg["mcpServers"]), {CA.MCP_NAME})

    def test_the_allowed_tools_are_exactly_membranes(self):
        argv = self._argv()
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        want = {CA.TOOL_PREFIX + t["name"] for t in MCP.TOOLS}
        self.assertEqual(set(allowed), want)


if __name__ == "__main__":
    unittest.main()
