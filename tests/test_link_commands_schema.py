"""THE STRING THE SDK REFUSED (2026-10-25, red first).

A Claude-rung call to entity_link with `commands` as ONE string was
refused by the SDK ("No such tool available: entity_link") before it
reached the seat: the MCP schema said array-of-strings only, while the
implementation (_link_commands) takes a single string as one command and
the local toolset's exec family says {"type": ["string", "array"]}.
The MCP schema now accepts a string or an array of strings, for
entity_link and entity_unlink alike.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402


def _commands_schema(name: str) -> dict:
    tool = next(t for t in M.TOOLS if t["name"] == name)
    return tool["inputSchema"]["properties"]["commands"]


def _accepts(schema: dict, value) -> bool:
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if isinstance(value, str):
        return "string" in types
    if isinstance(value, list):
        item_t = (schema.get("items") or {}).get("type")
        return "array" in types and all(
            item_t is None or (item_t == "string" and isinstance(v, str)) for v in value)
    return False


class TestLinkCommandsSchema(unittest.TestCase):
    def test_entity_link_schema_accepts_a_string(self):
        s = _commands_schema("entity_link")
        self.assertTrue(_accepts(s, "uname -a"))
        self.assertTrue(_accepts(s, ["id", "uptime"]))
        self.assertEqual(s.get("items"), {"type": "string"})

    def test_entity_unlink_schema_accepts_a_string(self):
        s = _commands_schema("entity_unlink")
        self.assertTrue(_accepts(s, "uname -a"))
        self.assertTrue(_accepts(s, ["id", "uptime"]))


class TestStringCallReachesTheSeat(unittest.TestCase):
    def _call(self, name: str):
        seen = {}

        def fake_http(port, method, path, body=None):
            seen["path"], seen["body"] = path, body
            return {"ok": True, "executor": "tool:executor",
                    "linked": body.get("commands"), "runs": [],
                    "removed": body.get("commands"), "misses": []}
        orig = M._http
        M._http = fake_http
        try:
            out = M._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name,
                                        "arguments": {"commands": "uname -a"}}}, 0)
        finally:
            M._http = orig
        self.assertNotIn("error", out)
        return seen

    def test_link_string_is_a_one_command_list(self):
        seen = self._call("entity_link")
        self.assertEqual(seen["path"], "/membrane/entity/link")
        self.assertEqual(seen["body"]["commands"], ["uname -a"])

    def test_unlink_string_is_a_one_command_list(self):
        seen = self._call("entity_unlink")
        self.assertEqual(seen["path"], "/membrane/entity/unlink")
        self.assertEqual(seen["body"]["commands"], ["uname -a"])


if __name__ == "__main__":
    unittest.main()
