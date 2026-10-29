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


class TestEntityEditSchemaSize(unittest.TestCase):
    """#569: entity_edit's description rides every Claude first step
    (~1.5K tokens); keep it short and point at help('scripts')."""

    def _tool(self):
        return next(t for t in M.TOOLS if t["name"] == "entity_edit")

    def test_description_is_short(self):
        self.assertLess(len(self._tool()["description"]), 2000)

    def test_description_keeps_essentials(self):
        d = self._tool()["description"]
        for word in ("nudge_script", "tick_script", "getter_script",
                     "publish", "merged_into", "scripts"):
            self.assertIn(word, d)

    def test_every_property_is_still_advertised(self):
        props = self._tool()["inputSchema"]["properties"]
        for k in ("spec", "summary", "links", "class", "merged_into",
                  "nudge_script", "delete_script", "mention_script",
                  "thought_script", "dreamed_script", "tick_script",
                  "getter_script", "watch_script", "publish", "unpublish"):
            self.assertIn(k, props)


class TestScriptsHelpKeepsTheContract(unittest.TestCase):
    """#569: what entity_edit's description dropped lives in help('scripts'),
    which costs tokens only when the model asks for it."""

    def test_the_dropped_contract_is_in_help(self):
        from membrane.toolset import HELP
        h = HELP["scripts"]
        for needle in ("intent", "Goal", "chat", "voice", "30s", "never the same line twice",
                       "delete_script", "replaces the track's", "10s", "0.25",
                       "publish", "keeps its edit", "membrane::presence"):
            self.assertIn(needle, h, needle)

    def test_the_description_says_which_budget_is_which(self):
        d = next(t for t in M.TOOLS if t["name"] == "entity_edit")["description"]
        self.assertIn("getter 10s", d)


class TestBashSteersToMethods(unittest.TestCase):
    """2026-10-29: since the `bash` alias (#619) ~80% of the model's calls
    were one-shot shell and it defined no methods in two days; the doctrine
    says program the field first. The tool's own description says so."""

    def test_bash_says_one_shot_and_points_at_methods(self):
        d = next(t for t in M.TOOLS if t["name"] == "bash")["description"]
        for needle in ("once", "method_define", "method_call"):
            self.assertIn(needle, d, needle)
