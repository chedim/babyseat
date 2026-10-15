"""THE ENTITY QUERY LANGUAGE (the lightweight graph query, red first):
one tool -- `entity_query` -- answering enumerate/list/count over the
entity graph via a line grammar ('kind: f:*' / 'parent: <spec>' /
'children: <spec>' / 'near: <text>' / 'name~: <prefix>' composed with
'|' as OR, '&' as AND, 'count'):

    entity_query {q: "kind:f:* & parent:membrane::web"}     enumerate
    entity_query {q: "children:membrane::tool:radios"}       the ISA walk
    entity_query {q: "name~: router.py", count: true}        list/count
    entity_query {q: "near: heading pipeline", k: 5}         semantic

The grammar's ops (each term = one filter):
  kind:<k|*>            the entity kind (f: file, s: symbol, m: model,
                        c/cmd, tool, Component/Decision/..., _default
                        by cls first letter)
  name~:<prefix>        the eid/name's case-insensitive prefix
  parent:<spec>         entities whose parent chain includes spec
  children:<spec>       the inverse (the ISA walk reversed)
  near:<free text>      the semantic nearness (the related-lane's
                        search: named hits, then the centroid)
  status:<s>            the properties.status filter (open/done/quarantined)
  count                 renders 'N entities' only (the list suppressed)

The result: rows (eid/class/name/summary head/count/parents) + the total.
$0: scans est.docs (the RAM graph); no embedder when no 'near:' term.

The answers to the operator's inventory questions, pinned as tests:
- 'does the model have SEARCH?': entities_related (free text) -- yes,
  and now the STRUCTURED enumerate too (the grammar above).
- 'can it enumerate children?': children:<spec> (entities.py's
  children_of ISA walk, EXPOSED -- the old gap).
- 'should we add some lightweight graph query language?': this file.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import entity_query as EQ  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


def _est():
    est = EntityStore(bucket="t-entq", shared=False)
    seed = [
        ("membrane::f:membrane", {"name": "membrane"}),
        ("membrane::f:membrane/server.py", {"name": "server.py"}),
        ("membrane::f:membrane/router.py", {"name": "router.py",
                                            "parent": "membrane::f:membrane"}),
        ("membrane::f:membrane/watch.py", {"name": "watch.py",
                                           "parent": "membrane::f:membrane"}),
        ("membrane::tool:clock", {"name": "clock", "status": "open"}),
        ("membrane::tool:radios", {"name": "radios"}),
    ]
    for eid, props in seed:
        est.upsert(QuantumEntity(eid=eid, cls="Component",
                                 properties=props), embedder=None)
    return est


class TestGrammar(unittest.TestCase):
    def test_parse_terms(self):
        ast = EQ.parse("kind:f:* & parent:membrane::f:membrane | "
                       "name~: router.py")
        self.assertEqual(len(ast), 2)          # the OR of 2 blocks
        self.assertEqual(len(ast[0]), 2)       # the first block: 2 ANDs

    def test_kind_filter(self):
        est = _est()
        rows = EQ.run(est, "kind:tool:*")
        self.assertEqual({r["eid"] for r in rows},
                         {"membrane::tool:clock", "membrane::tool:radios"})

    def test_kind_file_sees_the_class_embedded_pool(self):
        """issue #99: a file entity minted via entity_add lands as
        'membrane::Component:files:x.py' (class-embedded); 'kind:file'
        must see the files: pool inside the class, not read the class
        itself as the kind (the query could not see the files: pool)."""
        est = _est()
        est.upsert(QuantumEntity(
            eid="membrane::Component:files:membrane/router.py",
            cls="Component", properties={"name": "router.py"}), embedder=None)
        rows = EQ.run(est, "kind:file:*")
        self.assertIn("membrane::Component:files:membrane/router.py",
                      {r["eid"] for r in rows})
        # it is still also a component (its class) -- the pool and the
        # class are both true of it
        rows = EQ.run(est, "kind:component:*")
        self.assertIn("membrane::Component:files:membrane/router.py",
                      {r["eid"] for r in rows})

    def test_name_prefix(self):
        est = _est()
        rows = EQ.run(est, "name~: router.py")
        self.assertEqual([r["eid"] for r in rows],
                         ["membrane::f:membrane/router.py"])

    def test_parent_chain_term(self):
        est = _est()
        rows = EQ.run(est, "parent:membrane::f:membrane")
        # 'parent: X' = X is an ANCESTOR of the row (the walk):
        self.assertEqual({r["eid"] for r in rows},
                         {"membrane::f:membrane/router.py",
                          "membrane::f:membrane/watch.py"})

    def test_children_term(self):
        est = _est()
        rows = EQ.run(est, "children:membrane::f:membrane")
        self.assertEqual({r["eid"] for r in rows},
                         {"membrane::f:membrane/router.py",
                          "membrane::f:membrane/watch.py"})

    def test_status_filter(self):
        """The status term: a MISSING status reads as 'open' (implicit);
        a set status filters."""
        est = _est()
        rows = EQ.run(est, "kind:tool:* & status:open")
        self.assertEqual({r["eid"] for r in rows},
                         {"membrane::tool:clock", "membrane::tool:radios"})
        rows = EQ.run(est, "status:maintenance")
        self.assertEqual([r["eid"] for r in rows], [])

    def test_count_form(self):
        est = _est()
        out = EQ.run(est, "kind:f:*", count=True)
        self.assertEqual(out, "4 entity(ies)")

    def test_unknown_term_honest(self):
        est = _est()
        with self.assertRaises(ValueError):
            EQ.run(est, "frobnicate:x")

    def test_empty_query_matches_all(self):
        """Match-all: EVERY seeded doc rides (the store's own seeds —
        junk anchors etc. — included; the match is honest)."""
        est = _est()
        rows = EQ.run(est, "")
        seeded = {"membrane::f:membrane", "membrane::f:membrane/server.py",
                  "membrane::f:membrane/router.py",
                  "membrane::f:membrane/watch.py",
                  "membrane::tool:clock", "membrane::tool:radios"}
        self.assertTrue(seeded.issubset({r["eid"] for r in rows}))


class TestToolSurface(unittest.TestCase):
    def test_entity_query_tool_registered(self):
        """The MCP tool surface: 'entity_query' visible + wired."""
        import membrane.mcp_server as M
        names = {t["name"] for t in M.TOOLS}
        self.assertIn("entity_query", names)
        self.assertIn("entity_query", M.TOOL_IMPLS)


if __name__ == "__main__":
    unittest.main()