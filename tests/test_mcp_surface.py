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
import json
import unittest
from unittest import mock

from membrane import mcp_server as M

ADVERTISED = {"query", "state", "entities_related", "entity_add", "entity_edit", "entity_remove", "graph",
              "entity_inherit"}
# the operator, 2026-11-12: what is not field manipulation or querying is a method, reached through `call`
METHODS = {"entity_watch", "entity_unwatch", "entity_tracks", "junk_cut"}
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
        self.assertTrue(METHODS <= set(M.TOOL_IMPLS) and METHODS <= set(M.METHOD_TOOLS))    # their bridge


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
        for word in ("publish", "merged_into", ".c1f", "help('packs')", "chat:<person>.say"):   # #1043
            self.assertIn(word, d)

    def test_every_property_is_still_advertised(self):
        props = self._tool()["inputSchema"]["properties"]
        for k in ("spec", "summary", "links", "class", "merged_into", "publish", "unpublish",
                  "watch_ops", "watch_fields", "properties"):
            self.assertIn(k, props)
        assert not any(k.endswith("_script") for k in props)       # #1043: behaviour is a pack, never a script


class TestEntityEditPropertiesAreShort(unittest.TestCase):
    """2026-10-29: entity_edit's whole schema (4.7K chars, the biggest of the
    28 tools Claude carries every call) was mostly per-property essays; each
    property keeps one line and the long form moves to help()."""

    def test_the_whole_schema_is_short(self):
        import json
        t = next(t for t in M.TOOLS if t["name"] == "entity_edit")
        self.assertLess(len(json.dumps(t)), 2600)

    def test_what_the_properties_dropped_lives_in_help(self):
        # #1043: behaviour is a .c1f pack; the watch contract stays in help('watches')
        from membrane.help_pack import packaged
        p, w = packaged("packs"), packaged("watches")
        for needle in ("getter", "on-setter", "clock", "IMPORT", "refused", "receipt", "sql("):
            self.assertIn(needle, p, needle)
        for needle in ("cap 8", "cap 3", "re-arm", "watch-attached"):
            self.assertIn(needle, w, needle)


class TestScriptsHelpKeepsTheContract(unittest.TestCase):
    """#569 / #1043: the long contract lives in help (field entities), which costs tokens only when read; the
    tool description teaches packs, not Python scripts."""

    def test_scripts_help_points_at_packs(self):
        from membrane.help_pack import packaged
        h = packaged("scripts")
        for needle in ("help('packs')", "retired", "script_port.pending", "clock"):
            self.assertIn(needle, h, needle)

    def test_the_description_teaches_packs(self):
        t = next(t for t in M.TOOLS if t["name"] == "entity_edit")
        self.assertIn("help('packs')", t["description"])
        self.assertNotIn("python", json.dumps(t).lower())


class TestBashSteersToCommands(unittest.TestCase):
    """2026-10-29: since the `bash` alias (#619) ~80% of the model's calls
    were one-shot shell; the doctrine says program the field first. The tool's
    own description says so (#1101: what you run again is a Command entity)."""

    def test_bash_says_one_shot_and_points_at_commands(self):
        d = next(t for t in M.TOOLS if t["name"] == "bash")["description"]
        for needle in ("once", "Command:<name>.run"):
            self.assertIn(needle, d, needle)
