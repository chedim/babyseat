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

ADVERTISED = {"run", "query", "state", "learn", "forget", "entity_watch",
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
        self.assertTrue(FILE_TOOLS <= names, FILE_TOOLS - names)

    def test_no_collision_with_opencode_builtins(self):
        names = {t["name"] for t in M.TOOLS}
        self.assertEqual(names & OC_BUILTINS, set())

    def test_impls_cover_every_advertised_tool(self):
        names = {t["name"] for t in M.TOOLS}
        self.assertTrue(ADVERTISED <= names, ADVERTISED - names)
        self.assertTrue(ADVERTISED <= set(M.TOOL_IMPLS),
                        ADVERTISED - set(M.TOOL_IMPLS))


class TestFileTools(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m-filetools-")
        self.p = os.path.join(self.dir, "note.txt")
        self.sent = []

        def fake_http(port, method, path, body=None):
            self.sent.append((method, path, body))
            return {"ok": True, "eid": "e:1", "name": body.get("ent", ""),
                    "class": "file", "event": body.get("event", 0)}
        self.patcher = mock.patch.object(M, "_http", side_effect=fake_http)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_write_creates_and_mints(self):
        r = M.tool_write({"path": self.p, "content": "hello\n"}, 11500)
        self.assertIn("hello", open(self.p).read())
        self.assertIn(str(self.p), r)
        self.assertEqual(self.sent[0][1], "/membrane/filetool")
        self.assertEqual(self.sent[0][2]["op"], "write")
        self.assertIn("f:", self.sent[0][2].get("ent", ""))

    def test_write_creates_parents(self):
        q = os.path.join(self.dir, "a/b/c.txt")
        M.tool_write({"path": q, "content": "x"}, 11500)
        self.assertEqual(open(q).read(), "x")

    def test_read_missing_is_proxy_error(self):
        with self.assertRaises(M.ProxyError):
            M.tool_read({"path": os.path.join(self.dir, "nope")}, 11500)

    def test_read_numbered_offset_limit(self):
        M.tool_write({"path": self.p, "content": "a\nb\nc\n"}, 11500)
        self.sent.clear()
        r = M.tool_read({"path": self.p, "offset": 2, "limit": 1}, 11500)
        self.assertIn("2:", r)
        self.assertNotIn("1:", r)
        self.assertEqual(self.sent[0][2]["op"], "read")

    def test_edit_replaces_and_needs_fresh_read(self):
        M.tool_write({"path": self.p, "content": "aa bb aa"}, 11500)
        self.sent.clear()
        r = M.tool_edit({"path": self.p, "old": "aa", "new": "z",
                         "replace_all": True}, 11500)
        self.assertEqual(open(self.p).read(), "z bb z")
        self.assertEqual(self.sent[0][2]["op"], "edit")

    def test_edit_not_found_errors(self):
        M.tool_write({"path": self.p, "content": "aa"}, 11500)
        self.sent.clear()
        with self.assertRaises(M.ProxyError):
            M.tool_edit({"path": self.p, "old": "zz", "new": "q"}, 11500)

    def test_edit_ambiguous_needs_replace_all(self):
        M.tool_write({"path": self.p, "content": "aa aa"}, 11500)
        with self.assertRaises(M.ProxyError):
            M.tool_edit({"path": self.p, "old": "aa", "new": "z"}, 11500)


if __name__ == "__main__":
    unittest.main()
