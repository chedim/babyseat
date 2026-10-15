"""ISSUE #60 (Memo, from the seat): on a headless seat's watch pulse, a mounted
tool was dispatched as '/exec' while the registry advertises 'exec' -- a route
string reused as a tool name carried its leading slash into the match, and the
call was refused or misrouted though the tool supports it. A leading slash is
normalized before matching, at both doors: the seat's own dispatcher
(toolset.resolve) and the MCP server's tools/call."""

import unittest
from unittest import mock

from membrane import mcp_server as M
from membrane import toolset as T


class TestTheSlashIsNoPartOfTheName(unittest.TestCase):
    def test_the_seats_dispatcher_reads_slash_exec_as_exec(self):
        args = {"action": "call", "object": "o", "method": "m", "args": {"a": 1}}
        self.assertEqual(T.resolve("/exec", dict(args)), T.resolve("exec", dict(args)))
        self.assertEqual(T.resolve("//entity", {"action": "read", "name": "x"}),
                         T.resolve("entity", {"action": "read", "name": "x"}))

    def test_the_mcp_door_finds_a_slashed_name(self):
        seen = {}
        with mock.patch.dict(M.TOOL_IMPLS, {"query": lambda a, p: seen.setdefault("ran", "query") or "ok"}):
            out = M._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "/query", "arguments": {}}}, 1)
        self.assertNotIn("error", out, out)
        self.assertEqual(seen.get("ran"), "query")

    def test_an_unknown_name_still_says_so(self):
        out = M._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "/nope", "arguments": {}}}, 1)
        self.assertIn("unknown tool", out["error"]["message"])


if __name__ == "__main__":
    unittest.main()
