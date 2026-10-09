"""THE COMMANDS NAMED BY THEIR FIRST WORD (2026-10-08, the review, red first).

An ad-hoc command's entity was named after its first shell token, so every
`set -a; . hass.env; curl ...` was ONE entity, membrane::shell:set (158
mentions), each run overwriting the last; `token_t=$(grep` became a name;
a model's label fused into the command ('shell:shell:mosq-call-tap::').
The link reply showed 80 chars of output, so the model re-read the entity.

Pinned: a distinct command line gets a distinct, STABLE eid (same line ->
same entity); a `label :: command` (or {name, command}) names it; the
reply carries the head.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import night as N  # noqa: E402


class TestCommandNames(unittest.TestCase):
    def test_distinct_lines_distinct_stable_names(self):
        a = N.command_name("set -a; . ~/hass.env; curl -s $HASS_URL/api/states/x")
        b = N.command_name("set -a; . ~/hass.env; curl -s $HASS_URL/api/states/y")
        self.assertNotEqual(a, b)
        self.assertEqual(a, N.command_name("set -a; . ~/hass.env; curl -s $HASS_URL/api/states/x"))
        self.assertTrue(a.startswith("curl-") and "states" in a, a)   # named by its action (2026-10-09)

    def test_a_single_word_keeps_its_name(self):
        self.assertEqual(N.command_name("uptime"), "uptime")

    def test_garbage_heads_are_cleaned(self):
        n = N.command_name("token_t=$(grep TOKEN f) && echo ok")
        self.assertRegex(n, r"^[a-z0-9._-]+$")
        self.assertNotIn("$", n)

    def test_labels_name_the_command(self):
        self.assertEqual(N.split_label("mosq-call-tap :: mosquitto_sub -t x"),
                         ("mosq-call-tap", "mosquitto_sub -t x"))
        self.assertEqual(N.split_label("shell:mosq-call-tap:: mosquitto_sub -t x"),
                         ("mosq-call-tap", "mosquitto_sub -t x"))
        self.assertEqual(N.split_label("[ -f x ] && echo y"), ("", "[ -f x ] && echo y"))
        specs = N.command_specs([{"name": "soc", "command": "curl -s x"}])
        self.assertEqual(N.split_label(specs[0]), ("soc", "curl -s x"))


class _Http:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        self.orig = M._http
        M._http = lambda port, method, path, body=None: dict(self.reply)
        return self

    def __exit__(self, *a):
        M._http = self.orig


class TestLinkReply(unittest.TestCase):
    def test_the_head_rides_long_enough(self):
        head = "x" * 500 + "END"
        with _Http({"ok": True, "executor": "tool:executor", "linked": [{}],
                    "runs": [{"ok": True, "eid": "membrane::shell:a", "rc": 0,
                              "outcome": "ok", "head": head}]}):
            out = M.tool_entity_link({"commands": "a"}, 0)
        self.assertIn("END", out)


if __name__ == "__main__":
    unittest.main()
