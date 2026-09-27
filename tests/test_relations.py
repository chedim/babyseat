"""membrane/relations.py: role-bound VSA composites keep 'A calls B' apart
from 'B calls A' only with RANDOM roles (the E25 lesson, pinned); fillers
come back by unbinding; triples are stored, merged and superseded."""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import code_evidence as CE  # noqa: E402
from membrane import codegraph, vsa  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.capture import Capturer  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

DIM = 2048
PAIRS = [(f"s:mod.f{i}", "calls", f"s:mod.g{i}") for i in range(60)]
OTHER = PAIRS[1:] + PAIRS[:1]


class TestStructure(unittest.TestCase):
    def test_random_roles_keep_the_order(self):
        aw = R.structure_awareness(PAIRS, OTHER, lambda s, r, o: R.compose(s, r, o, DIM))
        self.assertGreater(aw, 0.5)

    def test_semantic_codebook_roles_do_not(self):
        cb = vsa.VSACodebook(embedder=FakeEmbedder(), dim=DIM)
        roles = {r: cb.token_vec(f"role {r}") for r in ("subj", "rel", "obj")}

        def enc(s, r, o):                  # the E25 mistake: roles from the codebook
            return vsa.weighted_majority(
                [vsa.bind(roles["subj"], cb.token_vec(s)), vsa.bind(roles["rel"], cb.token_vec(r)),
                 vsa.bind(roles["obj"], cb.token_vec(o))], [1.0, 1.0, 1.0])
        semantic = R.structure_awareness(PAIRS, OTHER, enc)
        random_roles = R.structure_awareness(PAIRS, OTHER, lambda s, r, o: R.compose(s, r, o, DIM))
        # E25 (mxbai): 0.28 semantic vs 0.58 random; the lesson is the gap
        self.assertGreater(random_roles - semantic, 0.2)

    def test_unbinding_recovers_the_subject(self):
        cands = [p[0] for p in PAIRS] + [p[2] for p in PAIRS] + [f"w:x{i}" for i in range(400)]
        hits = 0
        for s, r, o in PAIRS[:20]:
            est = R.unbind_role(R.compose(s, r, o, DIM), "subj", DIM)
            hits += R.cleanup(est, cands, DIM, k=1)[0][0] == s
        self.assertEqual(hits, 20)


class TestStorage(unittest.TestCase):
    def test_upsert_merges_and_of_finds_both_sides(self):
        b = MemBackend()
        R.upsert(b, "o", "s:a", "calls", "s:b", "code", "v:1", 1.0)
        R.upsert(b, "o", "s:a", "calls", "s:b", "code", "v:2", 2.0)
        (d,) = R.of(b, "o", "s:a")
        self.assertEqual((d["n"], d["evidence"]), (2, ["v:1", "v:2"]))
        self.assertEqual(R.of(b, "o", "s:b")[0]["subj"], "s:a")

    def test_observations_record_what_a_tool_did(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events(ST.split_turn("x", results=[("c", "edit", '{"filePath": "pkg/a.py"}',
                                                     "Edit applied successfully.")])[1:])
        f.process()
        rels = f.relations_of("f:pkg/a.py")
        self.assertEqual([(d["subj"], d["rel"]) for d in rels], [("tool:edit", "edited")])

    def test_code_relations_are_superseded_when_the_code_changes(self):
        d = tempfile.mkdtemp(prefix="ef-rel-")
        p = Path(d) / "pkg/a.py"
        p.parent.mkdir(parents=True)
        p.write_text(textwrap.dedent('''
            def outer():
                inner_one()
            def inner_one():
                pass
            def inner_two():
                pass
            '''))
        g = codegraph.build_graph(d)
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host", graph_fn=lambda: g)
        cap = Capturer(f, mode="on", start=False)
        cap.on_code(g, ["pkg/a.py"])
        cap.flush()
        calls = lambda: {x["obj"] for x in f.relations_of("s:pkg.a.outer", "calls")}  # noqa: E731
        self.assertEqual(calls(), {"s:pkg.a.inner_one"})
        p.write_text(p.read_text().replace("    inner_one()", "    inner_two()"))
        cap.on_code(g, codegraph.rebuild_paths(g, ["pkg/a.py"])["touched"])
        cap.flush()
        self.assertEqual(calls(), {"s:pkg.a.inner_two"})


if __name__ == "__main__":
    unittest.main()
