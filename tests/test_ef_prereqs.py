"""Stage-1 prerequisites of the entity field: codegraph defs + docstrings,
the VSA lane's non-remembering encode, the ANN client's field/filter and
index definition, and the graph tool reading the real shape."""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import ann, codegraph, vsa  # noqa: E402
from membrane import mcp_server  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def _repo(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="ef-cg-")
    for rel, src in files.items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    return d


class TestCodegraphDefs(unittest.TestCase):
    def setUp(self):
        self.root = _repo({
            "pkg/a.py": '''
                def main():
                    """Run a."""
                    helper()
                def helper():
                    """The probe walks up from the port."""
                ''',
            "pkg/b.py": '''
                class Seat:
                    """A seat."""
                    def __init__(self):
                        pass
                def main():
                    """Run b."""
                ''',
        })
        self.g = codegraph.build_graph(self.root)

    def test_colliding_qualnames_stay_distinct_in_defs(self):
        # `symbols` keeps one 'main' (the later file wins, unchanged);
        # `defs` keeps both, module-qualified
        self.assertIn("main", self.g["symbols"])
        self.assertIn("pkg.a.main", self.g["defs"])
        self.assertIn("pkg.b.main", self.g["defs"])
        self.assertEqual(sorted(self.g["short_index"]["main"]),
                         ["pkg.a.main", "pkg.b.main"])

    def test_docstrings_and_module_are_recorded(self):
        d = self.g["defs"]["pkg.a.helper"]
        self.assertEqual(d["doc"], "The probe walks up from the port.")
        self.assertEqual(d["mod"], "pkg.a")
        self.assertEqual(d["qual"], "helper")
        self.assertEqual(self.g["defs"]["pkg.b.Seat"]["doc"], "A seat.")
        self.assertIn("pkg.b.Seat.__init__", self.g["defs"])

    def test_module_of(self):
        self.assertEqual(codegraph.module_of("membrane/opencode.py"),
                         "membrane.opencode")
        self.assertEqual(codegraph.module_of("pkg/__init__.py"), "pkg")

    def test_rebuild_paths_keeps_defs_in_sync(self):
        p = Path(self.root) / "pkg/a.py"
        p.write_text('def renamed():\n    """New."""\n')
        codegraph.rebuild_paths(self.g, ["pkg/a.py"])
        self.assertIn("pkg.a.renamed", self.g["defs"])
        self.assertNotIn("pkg.a.helper", self.g["defs"])
        self.assertIn("pkg.b.main", self.g["defs"])
        os.remove(p)
        codegraph.rebuild_paths(self.g, ["pkg/a.py"])
        self.assertFalse(any(d.startswith("pkg.a.") for d in self.g["defs"]))
        self.assertEqual(self.g["short_index"].get("main"), ["pkg.b.main"])


class TestVSARemember(unittest.TestCase):
    def test_encode_without_remember_keeps_history(self):
        lane = vsa.VSALane(vsa.VSACodebook(embedder=FakeEmbedder(), dim=512))
        lane.encode("the router calls the gate")
        n = len(lane._history)
        s = lane.encode("the gate calls the router", remember=False)
        self.assertIsNotNone(s)
        self.assertEqual(len(lane._history), n)


class TestAnnClient(unittest.TestCase):
    def test_field_and_filter_ride_the_knn_body(self):
        seen = {}

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"hits": [{"id": "e1", "score": 0.9}]}'

        def fake_urlopen(req, timeout=0):
            seen["body"] = json.loads(req.data)
            return _R()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            got = ann.ann_search("idx", [0.1, 0.2], k=3, field="state",
                                 filter={"term": "live", "field": "tier"})
        self.assertEqual(got, [("e1", 0.9)])
        knn = seen["body"]["knn"][0]
        self.assertEqual(knn["field"], "state")
        self.assertEqual(knn["filter"], {"term": "live", "field": "tier"})

    def test_vector_index_definition(self):
        d = ann.vector_index_def("ix", "b", "membrane", "entity_states",
                                 "state", 1024, ("origin", "tier"))
        props = d["params"]["mapping"]["types"]["membrane.entity_states"]["properties"]
        self.assertEqual(props["state"]["fields"][0]["dims"], 1024)
        self.assertEqual(props["tier"]["fields"][0]["analyzer"], "keyword")

    def test_ensure_vector_index_degrades(self):
        class _Broken:
            def search_indexes(self):
                raise RuntimeError("no search service")
        self.assertFalse(ann.ensure_vector_index(
            _Broken(), "ix", "b", "membrane", "c", "state", 8))


class TestGraphTool(unittest.TestCase):
    def test_reads_the_related_shape(self):
        out = {"query": "router", "related": [
            {"key": "files:router.py", "cells": 7, "how": "named",
             "neighbors": [{"key": "symbols:route", "shared": 3}]}]}
        with mock.patch.object(mcp_server, "_http", return_value=out):
            text = mcp_server.tool_graph({"query": "router"}, 11500)
        self.assertIn("files:router.py", text)
        self.assertIn("symbols:routex3", text)
        self.assertNotIn("no entities matched", text)


if __name__ == "__main__":
    unittest.main()
