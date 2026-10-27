"""The MCP surface: unprefixed tool names + field-aware file tools.

The owner's 2026-10-02 call: every tool IS a membrane tool now -- the
membrane_ prefix on the wire is noise; and the seat gains opencode-shaped
read/write/edit tools whose file effects mint field entities (f:<path>)
through the running proxy, the way the run lane mints c:<head>. RED-first:
this file was written against the prefixed surface and FAILED before the
rename patch landed.
"""
import os
import tempfile
import unittest
from unittest import mock

from membrane import mcp_server as M

ADVERTISED = {"query", "state", "learn", "forget", "entity_watch",
              "entity_unwatch", "entity_tracks", "entities_related",
              "entity_add", "entity_edit", "entity_remove", "junk_cut",
              "peer_post", "graph", "entity_inherit"}
FILE_TOOLS = {"read", "write", "edit"}
# opencode built-in names that must stay dead (the ablation) -- an MCP
# surface name colliding with one of these would re-surface a builtin.
OC_BUILTINS = {"bash", "grep", "glob", "lsp", "apply_patch", "skill",
               "todowrite", "webfetch", "websearch", "question"}


class TestSurfaceNames(unittest.TestCase):
    def test_names_have_no_membrane_prefix(self):
        names = {t["name"] for t in M.TOOLS}
        self.assertEqual(
            [n for n in names if n.startswith("membrane_")], [],
            f"prefixed wire names remain: {sorted(names)[:8]}...")

    def test_core_names_present(self):
        names = {t["name"] for t in M.TOOLS}
        self.assertTrue(ADVERTISED <= names, ADVERTISED - names)
        # THE COVENANT (the reads/writes lane): the file tools are
        # REMOVED from the surface -- everything is entity scripts.
        self.assertEqual(names & FILE_TOOLS, set())

    def test_no_collision_with_opencode_builtins(self):
        # bash is the one deliberate exception (the operator, 2026-10-27): an alias
        # of code_run under the name models know; opencode names it membrane_bash
        names = {t["name"] for t in M.TOOLS}
        self.assertEqual(names & OC_BUILTINS, {"bash"})

    def test_impls_cover_every_advertised_tool(self):
        names = {t["name"] for t in M.TOOLS}
        self.assertTrue(ADVERTISED <= names, ADVERTISED - names)
        self.assertTrue(ADVERTISED <= set(M.TOOL_IMPLS),
                        ADVERTISED - set(M.TOOL_IMPLS))


if __name__ == "__main__":
    unittest.main()
