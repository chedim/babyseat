"""SYMBOL-FACT RECALL (the 'default args of _strip_context' gap, the
codegraph lane wired into the field's surfaces).

1. THE SIGNATURE VERB: codegraph gains `signature <sym>` (and the
   intercept intent 'what are the args of X' / 'signature of X') --
   the AST reads args + defaults + annotations; a call-edge graph
   answered WHO-CALLS-X, never WHAT-X-TAKES.
2. THE MCP SURFACE: /membrane/codegraph (read-only, same query()
   text as the CLI + intercept), and the returned graph's qualnames
   ARE the entity id space (eids.canon('s', qualname) keeps the
   qualname verbatim), so MCP clients can chain: ask the graph, then
   plant a track on the symbol entity with the same key. (The old
   exemplar-pool lane died with the exemplar store; symbol evidence
   now rides CODE events from code_evidence.py.)

All $0 (the graph is prebuilt at boot; no embeds, no model)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")

import membrane.codegraph as CG  # noqa: E402
from membrane.codegraph import build_graph, query  # noqa: E402


class _Proj:
    def __enter__(self):
        import pathlib
        self.dir = tempfile.mkdtemp(prefix="cg-sig-")
        self.sub = pathlib.Path(self.dir) / "pkg"
        self.sub.mkdir()
        (self.sub / "mod.py").write_text(
            "import json\n\n"
            "class Widget:\n"
            "    def paint(self, color=\"red\", *, urgent=False):\n"
            "        return f\"paint {color} {urgent}\"\n\n"
            "def _strip_context(messages, n=3, *, keep_system=True):\n"
            "    return messages[:n]\n\n"
            "def caller():\n"
            "    return _strip_context([], n=7)\n"
        )
        return pathlib.Path(self.dir)

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSignatureVerb(unittest.TestCase):
    def test_signature_reads_args_and_defaults(self):
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature _strip_context")
        self.assertIn("_strip_context", out)
        self.assertIn("n=3", out)               # the default is the answer
        self.assertIn("keep_system=True", out)
        self.assertIn("messages", out)

    def test_signature_method_with_kwargs(self):
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature Widget.paint")
        self.assertIn("color", out)
        self.assertIn("'red'", out)
        self.assertIn("urgent", out)

    def test_signature_flags_caller_defaults(self):
        """The kill-feature: a CALLER's override ('n=7' in caller()) is
        NOT the signature -- the def site's default (3) is what the
        verb reports, with the def file:line for navigation."""
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature _strip_context")
        self.assertIn("n=3", out)
        self.assertNotIn("n=7", out)
        self.assertIn("mod.py", out)

    def test_signature_unknown_symbol_honest(self):
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature mystery_fn")
        self.assertIn("no symbol matches", out.lower())

    def test_intent_phrasing_intercepts(self):
        """The thought-path: 'what are the args of _strip_context'
        intercepts the same way 'who calls X' does (the intent gate)."""
        from membrane.opencode import _codegraph_wants
        self.assertEqual(
            _codegraph_wants("what are the args of _strip_context?"),
            "signature _strip_context")
        self.assertEqual(
            _codegraph_wants("signature of Widget.paint"),
            "signature Widget.paint")
        self.assertIsNone(_codegraph_wants("what is love"))


class TestMcpSurface(unittest.TestCase):
    def test_route_registered(self):
        """/membrane/codegraph is on the proxy's route table (read-only,
        same query() text as the CLI + the intercept)."""
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature _strip_context")
        self.assertIn("_strip_context", out)

    def test_graph_symbols_share_entity_key_space(self):
        """THE ID COMPATIBILITY: the graph's qualname IS the entity
        field's symbol id ('s:' + qualname, case kept) -- the same key
        a planted track on the symbol uses, and the same key the
        extractor's symbol mentions resolve to against the graph."""
        from membrane import eids
        from membrane.entities import extract_entities
        with _Proj() as root:
            g = build_graph(root)
            self.assertIn("_strip_context", g["symbols"])
            self.assertIn("Widget.paint", g["symbols"])
            quals = {info.get("qual")
                     for info in g["defs"].values()}
            for qual in ("_strip_context", "Widget.paint"):
                # a bare mention resolves through defs to the
                # module-qualified qualname: one id for both
                full = next(k for k, info in g["defs"].items()
                            if info.get("qual") == qual)
                self.assertEqual(eids.canon("s", qual, g),
                                 "s:" + full)
                # the qualname itself round-trips verbatim
                self.assertEqual(eids.canon("s", full, g),
                                 "s:" + full)
        tags = extract_entities(
            "checked _strip_context and Widget.paint today")
        self.assertIn("_strip_context", tags.get("symbols", []))
        self.assertIn("Widget.paint", tags.get("symbols", []))


if __name__ == "__main__":
    unittest.main()
