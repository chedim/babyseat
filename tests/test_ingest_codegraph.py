"""The cold-start ingest lane + the codegraph lane (the Tencent close-the-gap
slice).

ingest: candidates respect the skip set + extension rules; chunks are
situation/action pairs; a second run dedups through learn_pair; --max
truncates; binary/NUL files are skipped.

codegraph: symbol extraction with qualnames, name-resolved callers (the
impact BFS), file impact, and the opencode query matcher (_is_codegraph_query:
canon frame + intent phrasing, with coding-turn non-matches).
"""

import tempfile
import unittest
from pathlib import Path

from membrane import opencode as OC
from membrane import codegraph as CG
from membrane.ingest import _candidate_files, _chunk_text, ingest


class _Store:
    def __init__(self):
        self.exemplars = []


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.md").write_text(
            "# Alpha\n" + "alpha body text " * 40)
        (self.root / "README.md").write_text("readme body " * 60)
        (self.root / ".membrane").mkdir()
        (self.root / ".membrane" / "field.json").write_text("[]")
        (self.root / "state.jsonl").write_text("{\"n\": 1}\n")
        (self.root / "big.md").write_text("x" * (600 * 1024))

    def _mem(self):
        from membrane.field import Exemplar

        class FakeEmb:
            def embed(self, texts):
                return [[0.1, 0.2, 0.3] for _ in texts]

        class Mem:
            store = _Store()
            embedder = FakeEmb()

            def learn_pair(self, s, a, unsafe=False, src="user"):
                for e in self.store.exemplars:
                    if e.situation == s:
                        return None
                self.store.exemplars.append(
                    Exemplar(situation=s, action=a, outcome="ingested",
                             vec=[0.1, 0.2, 0.3]))
                return len(self.store.exemplars) - 1

        return Mem()

    def test_default_sweep_picks_docs_and_readme_only(self):
        files = _candidate_files([], self.root)
        names = [f.name for f in files]
        self.assertIn("a.md", names)
        self.assertIn("README.md", names)
        self.assertNotIn("field.json", names)      # skip dir
        self.assertNotIn("state.jsonl", names)     # skip ext
        self.assertNotIn("big.md", names)          # size cap

    def test_chunks_are_pairs_with_source_headings(self):
        chunks = _chunk_text(self.root.joinpath("docs", "a.md").read_text(),
                             "docs/a.md")
        self.assertTrue(chunks)
        sit, act = chunks[0]
        self.assertTrue(sit.startswith("docs/a.md :: "))
        self.assertIn("Alpha", sit)
        self.assertLessEqual(len(act), 1200)

    def test_ingest_absorbs_then_dedups(self):
        mem = self._mem()
        res = ingest(mem, [], root=self.root)
        self.assertEqual(res["absorbed"], res["chunks"])
        self.assertGreater(res["absorbed"], 0)
        res2 = ingest(mem, [], root=self.root)
        self.assertEqual(res2["absorbed"], 0)
        self.assertEqual(res2["dedup"], res2["chunks"])

    def test_max_truncates(self):
        res = ingest(self._mem(), [], root=self.root, max_chunks=1)
        self.assertGreater(res["truncated"], 0)


class CodeGraphTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text(
            "def helper():\n    return 1\n\n\n"
            "def caller():\n    return helper()\n\n\n"
            "class Thing:\n    def method(self):\n"
            "        return caller()\n")
        (self.root / "build").mkdir()
        (self.root / "build" / "junk.py").write_text("x = 1\n")
        self.g = CG.build_graph(self.root)

    def test_symbols_and_skip_dirs(self):
        self.assertIn("helper", self.g["symbols"])
        self.assertIn("Thing.method", self.g["symbols"])
        self.assertNotIn("pkg/mod.py.junk", str(self.g["files"]))
        self.assertFalse(any(f.startswith("build/") for f in self.g["files"]))

    def test_callers_resolve_by_name(self):
        r = CG.callers_of(self.g, "helper")
        self.assertIn("caller", [e["caller"] for e in r["levels"][0]])
        self.assertIn("helper", r["matches"])

    def test_callers_transitive(self):
        r = CG.callers_of(self.g, "helper", depth=4)
        all_callers = {e["caller"] for lvl in r["levels"] for e in lvl}
        self.assertIn("Thing.method", all_callers)

    def test_file_impact(self):
        r = CG.impact_of_file(self.g, "pkg/mod.py")
        self.assertIn("caller", r["symbols"])
        self.assertTrue(any("caller" in e["caller"]
                            for lvl in r["levels"] for e in lvl))

    def test_query_text_forms(self):
        self.assertIn("symbols in", CG.query(self.g, "stats"))
        self.assertIn("impact of helper", CG.query(self.g, "impact helper"))
        self.assertIn("no symbol", CG.query(self.g, "impact nope_xyz"))


class CodegraphBridgeTest(unittest.TestCase):
    """The incremental refresh: rebuild_paths re-parses only changed files,
    stale_paths finds external edits by mtime, deletes/renames drop out."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text(
            "def helper():\n    return 1\n\n\n"
            "def caller():\n    return helper()\n")
        (self.root / "pkg" / "b.py").write_text(
            "from pkg.a import helper\n\n\ndef other():\n    return helper()\n")
        self.g = CG.build_graph(self.root)

    def _write_a(self, text):
        (self.root / "pkg" / "a.py").write_text(text)
        # guarantee a different stored mtime even on coarse filesystems
        import os
        st = (self.root / "pkg" / "a.py").stat()
        os.utime((self.root / "pkg" / "a.py"), (st.st_atime, st.st_mtime + 2))

    def test_rebuild_renames_symbol(self):
        self._write_a("def helper_v2():\n    return 2\n\n\n"
                      "def caller():\n    return helper_v2()\n")
        r = CG.rebuild_paths(self.g, ["pkg/a.py"])
        self.assertEqual(r["touched"], ["pkg/a.py"])
        self.assertNotIn("helper", self.g["symbols"])
        self.assertIn("helper_v2", self.g["symbols"])
        self.assertIn("caller", self.g["symbols"])
        # b.py's symbol set is untouched
        self.assertIn("other", self.g["symbols"])

    def test_rebuild_picks_up_new_callers(self):
        self._write_a("def helper():\n    return 1\n\n\n"
                      "def caller():\n    return helper()\n\n\n"
                      "def extra():\n    return caller()\n")
        CG.rebuild_paths(self.g, ["pkg/a.py"])
        r = CG.callers_of(self.g, "helper", depth=2)
        all_callers = {e["caller"] for lvl in r["levels"] for e in lvl}
        self.assertIn("extra", all_callers)

    def test_rebuild_drops_deleted_file(self):
        (self.root / "pkg" / "b.py").unlink()
        r = CG.rebuild_paths(self.g, ["pkg/b.py"])
        self.assertEqual(r["dropped"], ["pkg/b.py"])
        self.assertNotIn("other", self.g["symbols"])
        # the callers index rebuilt without b.py's edges
        r2 = CG.callers_of(self.g, "helper", depth=1)
        self.assertNotIn("other", [e["caller"] for e in r2["levels"][0]])

    def test_rebuild_keeps_shared_symbol_from_other_owner(self):
        # helper exists in both files; rebuilding a.py must not drop b.py's
        # claim on it
        self._write_a("def helper():\n    return 2\n")
        CG.rebuild_paths(self.g, ["pkg/a.py"])
        self.assertIn("helper", self.g["symbols"])
        self.assertEqual(self.g["symbols"]["helper"]["file"], "pkg/a.py")

    def test_stale_paths_finds_external_edit(self):
        self.assertEqual(CG.stale_paths(self.g), [])
        self._write_a("def helper():\n    return 9\n")
        self.assertEqual(CG.stale_paths(self.g), ["pkg/a.py"])

    def test_stale_paths_finds_added_and_deleted(self):
        (self.root / "pkg" / "c.py").write_text("def fresh():\n    pass\n")
        (self.root / "pkg" / "b.py").unlink()
        stale = CG.stale_paths(self.g)
        self.assertIn("pkg/c.py", stale)
        self.assertIn("pkg/b.py", stale)


class CodegraphDirtyMarkTest(unittest.TestCase):
    """_tool_arg_path parses edit/write args; _codegraph_note_dirty maps them
    to repo-relative dirty entries that _codegraph_refresh consumes."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("def helper():\n    return 1\n")
        self.g = CG.build_graph(self.root)

    def test_tool_arg_path(self):
        self.assertEqual(OC._tool_arg_path(
            {"arguments": '{"filePath": "/x/y.py"}'}), "/x/y.py")
        self.assertEqual(OC._tool_arg_path(
            {"arguments": '{"file_path": "/x/z.py"}'}), "/x/z.py")
        self.assertEqual(OC._tool_arg_path({"arguments": "not json"}), "")
        self.assertEqual(OC._tool_arg_path({}), "")

    def test_note_dirty_and_refresh(self):
        class Srv:
            _codegraph = self.g
            _cg_dirty = []
            _cg_swept = 0.0

        srv = Srv()
        OC._codegraph_note_dirty(srv, [str(self.root / "pkg" / "a.py"),
                                       str(self.root / "notes.txt")])
        self.assertEqual(srv._cg_dirty, ["pkg/a.py"])
        # refresh via the real path: sweep interval already elapsed (0.0)
        self._w_a()
        OC._codegraph_refresh(srv, self.g)
        self.assertIn("helper_v2", self.g["symbols"])
        self.assertEqual(srv._cg_dirty, [])

    def _w_a(self):
        (self.root / "pkg" / "a.py").write_text("def helper_v2():\n    return 1\n")

    def test_capture_lane_marks_dirty(self):
        class Mem:
            _pending_tools = {}
            store = _Store()

        orig_capture = OC._capture
        OC._capture = lambda *a, **k: None  # silence learning for this test
        try:
            mem = Mem()
            mem.server_ref = type("S", (), {})()
            mem.server_ref._codegraph = self.g
            mem.server_ref._cg_dirty = []
            calls = {0: {"id": "t1", "function": {
                "name": "edit",
                "arguments": '{"filePath": "%s"}' % (self.root / "pkg" / "a.py")}}}
            OC._capture_plan_and_calls(mem, "fix helper", "editing", calls)
            self.assertEqual(mem.server_ref._cg_dirty, ["pkg/a.py"])
        finally:
            OC._capture = orig_capture


class CodegraphMatcherTest(unittest.TestCase):
    def test_canon_and_intent(self):
        self.assertTrue(OC._is_codegraph_query("membrane: codegraph stats"))
        self.assertTrue(OC._is_codegraph_query(
            "membrane: codegraph impact learn_pair"))
        self.assertTrue(OC._is_codegraph_query("who calls learn_pair?"))
        self.assertTrue(OC._is_codegraph_query(
            "impact of touching membrane/watch.py"))
        for q in ("refactor the codegraph module",
                  "write a unit test for helpers",
                  "what calls the api",
                  "fix the bug in caller() and callees"):
            self.assertFalse(OC._is_codegraph_query(q), q)


if __name__ == "__main__":
    unittest.main()