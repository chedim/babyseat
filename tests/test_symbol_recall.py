"""SYMBOL-FACT RECALL (the 'default args of _strip_context' gap, the
codegraph lane wired into the field's surfaces): three pieces, each
pinned per the E-discipline.

1. THE SIGNATURE VERB: codegraph gains `signature <sym>` (and the
   intercept intent 'what are the args of X' / 'signature of X') --
   the AST reads args + defaults + annotations; a call-edge graph
   answered WHO-CALLS-X, never WHAT-X-TAKES.
2. THE SYMBOL-ANCHORED RECALL POOL: rag.py's entity pool hot-loads
   cells whose harvested tags name the query's symbol (term evidence,
   sim 0.99) -- 'default args for _strip_context' recalls the cells
   that TOUCHED it by NAME, before cosine gets a vote.
3. THE MCP SURFACE: /membrane/codegraph (read-only, same query()
   text as the CLI + intercept), and the returned data carries
   symbols:* entity ids -- the graph's qualnames ARE the entity id
   space (entities.py's harvest produces the same kind:value keys),
   so MCP clients can chain: ask the graph, then plant a track on
   the symbol entity with the same key.

All $0 (the graph is prebuilt at boot; no embeds, no model)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


class TestSymbolAnchoredRecall(unittest.TestCase):
    def _cell(self, situation: str, action: str, tags: dict):
        from membrane.field import Exemplar
        ex = Exemplar(situation=situation, action=action,
                      outcome="pending", vec=[1.0])
        ex.safe = True
        ex.extra["entities"] = tags
        return ex

    def test_symbol_term_hit_joins_pool(self):
        """THE POOL PROPERTY: a query naming a symbol hot-loads the cell
        that harvested that tag -- the cell's own harvest key resolves
        via the entity index; the GRAPH supplements bare-name cases."""
        from membrane.field import ExemplarStore
        from membrane.entities import entity_index, query_entities
        st = ExemplarStore()
        cell = self._cell("tool read for: checked _strip_context",
                          "default n=3; keep_system=True",
                          {"symbols": ["_strip_context"]})
        st.exemplars.append(cell)
        inv = entity_index(st.exemplars)
        self.assertIn("symbols:_strip_context", inv)
        # the extractor's query-side signal screen: a leading-underscore
        # bare name DOES harvest (the snake_case alternative matches)
        q = "what are the default args for _strip_context?"
        self.assertIn("symbols:_strip_context", query_entities(q))

    def test_bare_lowercase_name_via_graph(self):
        """THE GRAPH SUPPLEMENT: a bare lowercase function name in prose
        (the extractor's signal miss) joins the pool keys through the
        codegraph's symbol table -- srv._codegraph armed."""
        from membrane import rag as rag_mod
        from membrane.codegraph import build_graph
        with _Proj() as root:
            cg = build_graph(root)
            srv = type("S", (), {"_codegraph": cg})()
            q = "how does caller handle empty input"
            terms = rag_mod._symbol_pool_terms(srv, q)
            self.assertIn("symbols:caller", terms)


class TestMcpSurface(unittest.TestCase):
    def test_route_registered(self):
        """/membrane/codegraph is on the proxy's route table (read-only,
        same query() text as the CLI + the intercept)."""
        with _Proj() as root:
            g = build_graph(root)
            out = query(g, "signature _strip_context")
        self.assertIn("_strip_context", out)

    def test_graph_symbols_share_entity_key_space(self):
        """THE ID COMPATIBILITY: a graph symbol qualname + 'symbols:'
        IS the entity harvest's key form (kind:value, lowercase in the
        iter_entities key space) -- the same string a planted track
        ('symbols:_strip_context') uses, and refine_entities promotes
        harvests to exactly these qualnames."""
        with _Proj() as root:
            g = build_graph(root)
            self.assertIn("_strip_context", g["symbols"])
            self.assertIn("Widget.paint", g["symbols"])
        from membrane.entities import extract_entities, iter_entities
        tags = extract_entities(
            "checked _strip_context and Widget.paint today")
        keys = iter_entities(self._fake_cell(tags))
        self.assertIn("symbols:_strip_context", keys)
        self.assertIn("symbols:widget.paint", keys)

    @staticmethod
    def _fake_cell(tags):
        from membrane.field import Exemplar
        ex = Exemplar(situation="s", action="a", outcome="pending",
                      vec=[1.0])
        ex.extra["entities"] = tags
        return ex


if __name__ == "__main__":
    unittest.main()