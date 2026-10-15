"""THE TYPED EDGES (2026-10-15, graph feature 1: edges typed by resemblance).

A sentence naming two real things carries their relation: subtract both
things' settled meaning and the relation is left (experiments/delta_lane rung
10: recognized at 0.976 on pairs never seen; the pair's own offset A-B reads
at chance -- the relation lives in the SENTENCE). On a real field the clusters
read as relation kinds once only specific entities count and repeats are
dropped (rung 11). Embeddings cannot tell direction ('A calls B' vs 'B calls A'
at cos 0.975, relations.py's E25) -- so the edge is UNORDERED: the coded
triples keep direction, this edge keeps the KIND, by resemblance.

Per sentence naming 2+ specific entities: relation bits = sign(R @ (x -
mean(A's other mentions) - mean(B's other mentions))); the 'co' edge keeps its
count (one per event, newest seq only -- a rebuild never double-counts), its
evidence, the last 8 relation bits and their MAJORITY signature, and the
sentence it was last said in. field.edges_like(edge) -> the edges whose
signatures are nearest."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

F = {c: f"membrane/{c}.py" for c in "abcdefgh"}
IMPORTS = [("a", "b"), ("c", "d"), ("e", "f"), ("g", "h")]
REPLACED = [("a", "c"), ("b", "d"), ("e", "g"), ("f", "h")]


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _say(f, texts, t0=1000.0):
    evs = [ST.Event(stream="discourse", role="user", text=t, ts=t0 + i) for i, t in enumerate(texts)]
    f.write_events(evs, session="s1")
    f.process()


def _world():
    f = _field()
    texts = []
    for c in "abcdefgh":
        texts += [f"{F[c]} is open in the editor", f"someone reviewed {F[c]} yesterday"]
    for x, y in IMPORTS:
        texts += [f"{F[x]} imports the helpers from {F[y]}"]
    for x, y in REPLACED:
        texts += [f"{F[x]} replaced the old {F[y]} entirely"]
    _say(f, texts)
    return f


def _co(f, x, y):
    a, b = sorted(("f:" + F[x], "f:" + F[y]))
    return f.b.get("relations", R.relation_key(f.origin, a, "co", b))


class TestTheEdge(unittest.TestCase):
    def test_a_sentence_naming_two_things_writes_their_edge(self):
        f = _world()
        e = _co(f, "e", "f")
        self.assertIsNotNone(e)
        self.assertEqual(e["n"], 1)
        self.assertEqual(len(e["evidence"]), 1)
        self.assertIn("imports", e["said"])
        self.assertTrue(e["sig"])

    def test_the_edge_is_unordered(self):
        f = _world()
        _say(f, [f"{F['f']} imports the helpers from {F['e']} too"], t0=2000.0)
        self.assertEqual(_co(f, "e", "f")["n"], 2)

    def test_one_thing_or_a_speaker_writes_no_edge(self):
        f = _world()
        cos = [d for _, d in f.b.scan("relations") if d.get("rel") == "co"]
        self.assertTrue(cos)
        for d in cos:
            for side in (d["subj"], d["obj"]):
                self.assertTrue(side.startswith("f:"), d)


class TestEdgesLikeThisOne(unittest.TestCase):
    def test_the_same_relation_is_found_on_other_pairs(self):
        f = _world()
        got = f.edges_like(_co(f, "g", "h"), k=3)
        self.assertEqual(len(got), 3)
        for g in got:
            self.assertIn("imports", g["said"])
            self.assertNotEqual({g["subj"], g["obj"]}, {"f:" + F["g"], "f:" + F["h"]})

    def test_results_say_what_was_said_never_the_bits(self):
        f = _world()
        g = f.edges_like(_co(f, "e", "g"), k=1)[0]
        self.assertIn("replaced", g["said"])
        for key in ("subj", "obj", "n", "sim", "said"):
            self.assertIn(key, g)
        self.assertFalse({"sig", "rbits"} & set(g))


class TestTheHistory(unittest.TestCase):
    def test_a_rebuild_never_double_counts(self):
        f = _world()
        n = _co(f, "a", "b")["n"]
        f.rebuild()
        self.assertEqual(_co(f, "a", "b")["n"], n)

    def test_the_kill_switch_writes_no_edges(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_VSA": "0"}):
            f = _world()
        self.assertFalse([d for _, d in f.b.scan("relations") if d.get("rel") == "co"])

    def test_the_card_shows_what_was_said(self):
        f = _world()
        st = f.get("f:" + F["e"])
        card = FM.entity_card(st.to_doc(f.origin), [], [], f.relations_of("f:" + F["e"]))
        co = [r for r in card["relations"] if r["rel"] == "co"]
        self.assertTrue(co)
        self.assertTrue(all(r.get("said") for r in co))
        self.assertFalse(any({"sig", "rbits"} & set(r) for r in card["relations"]))


if __name__ == "__main__":
    unittest.main()
