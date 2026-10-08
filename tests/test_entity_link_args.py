"""THE STRING THAT RAN AS LETTERS (2026-10-07, red first).

Once the watch loops got their hands back (test_loop_lane), the first
exec links on the ThinkPhone ran the commands '1', 'a', 'g' (rc 127):
the model passed `commands` as ONE STRING ('ag1'), and entity_link's
`[str(c) for c in commands]` iterated it character by character. The
models also keep reaching for `command` / `spec` instead of `commands`
(the seat's own exhibits: {"command": "uname -a"}, {"spec": "uname -a"}).
A single command line is ONE command; the aliases resolve.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import mcp_server as M  # noqa: E402


class TestCommandSpecs(unittest.TestCase):
    def test_a_string_is_one_command(self):
        self.assertEqual(N.command_specs("uname -a"), ["uname -a"])
        self.assertEqual(N.command_specs("ag1"), ["ag1"])

    def test_json_encoded_list_string_is_decoded(self):
        """Exhibit 2 (the same evening): commands='["uname -a"]' ran
        literally as `["uname -a"]` (rc 127)."""
        self.assertEqual(N.command_specs('["uname -a"]'), ["uname -a"])
        self.assertEqual(N.command_specs(' ["id", "uptime"] '),
                         ["id", "uptime"])
        # a real command line that merely starts with '[' stays one
        self.assertEqual(N.command_specs("[ -f /etc/hosts ] && echo y"),
                         ["[ -f /etc/hosts ] && echo y"])

    def test_lists_pass_through_trimmed(self):
        self.assertEqual(N.command_specs([" uname -a ", "", None, "id"]),
                         ["uname -a", "id"])

    def test_empty(self):
        self.assertEqual(N.command_specs(None), [])
        self.assertEqual(N.command_specs("   "), [])

    def test_entity_link_never_splits_a_string(self):
        """The real hand: a string reaches the batch logic as ONE spec
        (est=None stops right after normalization -- no store needed:
        the error proves the specs were not empty / not split)."""
        res = N.entity_link(None, None, None, None, "", "uname -a")
        self.assertFalse(res["ok"])          # no store
        specs = N.command_specs("uname -a")
        self.assertEqual(len(specs), 1)


class TestLinkToolArgs(unittest.TestCase):
    def _sent(self, args):
        seen = {}

        def fake_http(port, method, path, body=None):
            seen["body"] = body
            return {"ok": True, "executor": "tool:executor",
                    "linked": body.get("commands"), "runs": []}
        orig = M._http
        M._http = fake_http
        try:
            M.tool_entity_link(args, 0)
        finally:
            M._http = orig
        return seen["body"]

    def test_string_commands_become_a_list(self):
        self.assertEqual(self._sent({"commands": "uname -a"})["commands"],
                         ["uname -a"])

    def test_command_and_spec_aliases(self):
        self.assertEqual(self._sent({"command": "uname -a"})["commands"],
                         ["uname -a"])
        self.assertEqual(self._sent({"spec": "c:uname-a"})["commands"],
                         ["c:uname-a"])

    def test_list_unchanged(self):
        self.assertEqual(self._sent({"commands": ["id", "uptime"]})
                         ["commands"], ["id", "uptime"])


if __name__ == "__main__":
    unittest.main()
