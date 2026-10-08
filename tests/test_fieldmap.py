"""THE FIELD MAP (2026-10-08, red first): the HA field dashboard's data --
the field's live entities as nodes, the `relations` triples between them
as edges, and EVERY node's value on EVERY axis (PC1..PCk of the states,
the per-kind centroid directions, mass/recency/age/activity/coherence/
degree) so the client re-plots on any X/Y pair without a refetch.

(The old /membrane/map/entities drew the QuantumEntity store -- 2 nodes
with a 1024-d position on the live seat, 0 edges -- while the field held
5426 live entities: the graph lived in the field's relations all along.)
"""
import os
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import fieldmap as FM  # noqa: E402


def _rows(n_per=30, dim=64, seed=1):
    rng = np.random.default_rng(seed)
    dirs = np.eye(dim)[:3] * 6.0
    rows = []
    for c, kind in enumerate(("file", "term", "tool")):
        for i in range(n_per):
            v = dirs[c] + rng.normal(0, 0.3, dim)
            rows.append({"eid": f"{kind[0]}:{c}-{i}", "kind": kind,
                         "surface": f"{kind} {i}", "state": v.tolist(),
                         "accr": (i + 1) * (c + 1), "coherence": 0.5,
                         "first_ts": 1000 + i, "last_ts": 2000 + i,
                         "activity": float(i), "conflicts": ["x"] if i == 0 else []})
    return rows


class TestFieldMap(unittest.TestCase):
    def test_nodes_axes_and_values(self):
        m = FM.field_map(_rows(), [], k=4)
        ids = [a["id"] for a in m["axes"]]
        for want in ("pc1", "pc2", "pc3", "pc4", "kind:file", "kind:term",
                     "kind:tool", "mass", "recency", "age", "activity",
                     "coherence", "degree"):
            self.assertIn(want, ids)
        self.assertEqual(len(m["nodes"]), 90)
        n0 = m["nodes"][0]
        for k in ("id", "kind", "label", "mass", "contest", "deg", "v"):
            self.assertIn(k, n0)
        self.assertEqual(len(n0["v"]), len(ids))
        self.assertEqual(m["kinds"][:3], sorted(m["kinds"][:3], key=lambda k: -m["kind_counts"][k]))

    def test_kind_axis_separates_its_kind(self):
        m = FM.field_map(_rows(), [], k=3)
        ix = [a["id"] for a in m["axes"]].index("kind:term")
        own = [n["v"][ix] for n in m["nodes"] if n["kind"] == "term"]
        other = [n["v"][ix] for n in m["nodes"] if n["kind"] != "term"]
        self.assertGreater(min(own), max(other))

    def test_pcs_spread_the_clusters(self):
        m = FM.field_map(_rows(), [], k=3)
        ax = [a["id"] for a in m["axes"]]
        p1, p2 = ax.index("pc1"), ax.index("pc2")
        cents = {}
        for kind in ("file", "term", "tool"):
            pts = np.array([[n["v"][p1], n["v"][p2]] for n in m["nodes"] if n["kind"] == kind])
            cents[kind] = pts.mean(0)
        d = [np.linalg.norm(cents[a] - cents[b]) for a, b in
             (("file", "term"), ("file", "tool"), ("term", "tool"))]
        self.assertGreater(min(d), 3.0)          # three distinct groups, not one clump

    def test_deterministic_signs(self):
        a = FM.field_map(_rows(), [], k=3)
        b = FM.field_map(list(reversed(_rows())), [], k=3)
        va = {n["id"]: n["v"][0] for n in a["nodes"]}
        vb = {n["id"]: n["v"][0] for n in b["nodes"]}
        for k in va:
            self.assertAlmostEqual(va[k], vb[k], places=2)

    def test_edges_inside_the_node_set_dedupe_and_degree(self):
        rels = [{"subj": "f:0-1", "rel": "calls", "obj": "t:1-2", "n": 3},
                {"subj": "f:0-1", "rel": "calls", "obj": "t:1-2", "n": 2},
                {"subj": "f:0-1", "rel": "uses", "obj": "o:outside", "n": 9},
                {"subj": "t:1-2", "rel": "near", "obj": "t:1-3", "n": 1},
                {"subj": "t:1-3", "rel": "x", "obj": "t:1-3", "n": 1},
                {"subj": "f:0-2", "rel": "old", "obj": "t:1-3", "n": 5,
                 "superseded": True}]
        m = FM.field_map(_rows(), rels, k=3)
        es = {(e["s"], e["rel"], e["t"]): e["n"] for e in m["edges"]}
        self.assertEqual(es, {("f:0-1", "calls", "t:1-2"): 5,
                              ("t:1-2", "near", "t:1-3"): 1})
        deg = {n["id"]: n["deg"] for n in m["nodes"]}
        self.assertEqual(deg["t:1-2"], 2)
        self.assertEqual(deg["f:0-1"], 1)

    def test_edge_cap_keeps_the_strongest(self):
        rows = _rows()
        rels = [{"subj": rows[i]["eid"], "rel": "r", "obj": rows[i + 1]["eid"], "n": i}
                for i in range(60)]
        m = FM.field_map(rows, rels, k=3, max_edges=10)
        self.assertEqual(len(m["edges"]), 10)
        self.assertEqual(min(e["n"] for e in m["edges"]), 50)

    def test_co_mention_edges_from_shared_sentences(self):
        """THE FIELD'S OWN GRAPH (the live seat had 0 relation triples:
        they only come from file tools): entities sharing sentences in
        their `recent` ring are co-mentioned -- weight = shared count;
        a sentence touching more than clique_max shown entities (a
        stop-word sentence) wires nothing."""
        rows = _rows(n_per=10)
        by = {r["eid"]: r for r in rows}
        by["f:0-1"]["recent"] = ["s1", "s2", "s3"]
        by["t:1-2"]["recent"] = ["s1", "s2"]
        by["u:2-3"] = None
        by["t:1-3"]["recent"] = ["s3"]
        for r in rows[:9]:                       # one huge sentence
            r.setdefault("recent", [])
            r["recent"] = list(r["recent"]) + ["big"]
        m = FM.field_map(rows, [], k=3, clique_max=6)
        es = {(e["s"], e["t"]): (e["rel"], e["n"]) for e in m["edges"]}
        self.assertEqual(es[("f:0-1", "t:1-2")], ("co-mentioned", 2))
        self.assertEqual(es[("f:0-1", "t:1-3")], ("co-mentioned", 1))
        self.assertFalse(any(k[1] == "big" for k in es))
        self.assertEqual(len(es), 2)              # "big" (9 nodes) wired nothing

    def test_triples_and_co_mentions_coexist(self):
        rows = _rows(n_per=4)
        by = {r["eid"]: r for r in rows}
        by["f:0-1"]["recent"] = ["s1"]
        by["t:1-2"]["recent"] = ["s1"]
        rels = [{"subj": "f:0-1", "rel": "calls", "obj": "t:1-2", "n": 3}]
        m = FM.field_map(rows, rels, k=2)
        kinds = sorted(e["rel"] for e in m["edges"])
        self.assertEqual(kinds, ["calls", "co-mentioned"])
        self.assertEqual({n["id"]: n["deg"] for n in m["nodes"]}["f:0-1"], 2)

    def test_tiny_and_empty(self):
        self.assertEqual(FM.field_map([], [])["nodes"], [])
        m = FM.field_map(_rows(n_per=1), [], k=8)       # 3 nodes: k shrinks
        self.assertEqual(len(m["nodes"]), 3)
        self.assertTrue(all(len(n["v"]) == len(m["axes"]) for n in m["nodes"]))

    def test_labels_are_bounded(self):
        rows = _rows(n_per=2)
        rows[0]["surface"] = "x" * 500
        m = FM.field_map(rows, [], k=2)
        self.assertLessEqual(max(len(n["label"]) for n in m["nodes"]), 80)


class TestEntityCard(unittest.TestCase):
    """THE FIELD ENTITY'S CARD (2026-10-08, red first): the map's drawer
    asked /membrane/entity/read -- the QuantumEntity store -- for a FIELD
    eid ('w:agatha') and got a 404 + "nearest: component:agatha". The
    field's own read: readings, the evidence sentences, the nearest field
    neighbors, the relations -- never the raw vectors."""

    DOC = {"eid": "w:agatha", "kind": "w", "surface": "agatha", "tier": "live",
           "n": 41, "accr": 12, "coherence": 0.61, "first_ts": 10.0,
           "last_ts": 99.0, "activity": 3.5, "conflicts": [],
           "n_by_stream": {"discourse": 30, "observation": 11},
           "state": "PACKED", "seed": "PACKED", "mean": "PACKED", "rsum": "PACKED",
           "recent": ["s1", "s2"], "last_pos": [1, 2]}

    def test_card(self):
        ev = [{"sid": "s1", "text": "agatha is the boat " * 40, "stream": "discourse",
               "role": "user", "ts": 98.0, "score": 0.8, "event": "e1", "seq": 3}]
        near = [("w:agatha", 1.0), ("w:boat", 0.83), ("c:catalina", 0.71)]
        rels = [{"subj": "w:agatha", "rel": "calls", "obj": "w:boat", "n": 2,
                 "vsa": "PACKED", "evidence": ["e1"]}]
        c = FM.entity_card(self.DOC, ev, near, rels)
        self.assertEqual(c["eid"], "w:agatha")
        self.assertEqual(c["surface"], "agatha")
        for k in ("n", "accr", "coherence", "first_ts", "last_ts", "activity",
                  "n_by_stream", "tier", "kind"):
            self.assertIn(k, c)
        blob = repr(c)
        self.assertNotIn("PACKED", blob)                   # no vectors leak
        self.assertEqual([x["eid"] for x in c["near"]], ["w:boat", "c:catalina"])
        self.assertEqual(c["near"][0]["sim"], 0.83)
        self.assertLessEqual(len(c["evidence"][0]["text"]), 400)
        self.assertEqual(c["evidence"][0]["role"], "user")
        self.assertEqual(c["relations"], [{"subj": "w:agatha", "rel": "calls",
                                            "obj": "w:boat", "n": 2}])

    def test_missing_entity(self):
        self.assertIsNone(FM.entity_card(None, [], [], []))


class TestNearest(unittest.TestCase):
    """The card's neighbors when the store's vector index answers nothing
    (the live seat: recall() -> [] for w:agatha): cosine over live states."""

    def test_ranks_by_cosine_and_skips_self(self):
        rows = [{"eid": "self", "state": [1, 0, 0]}, {"eid": "a", "state": [0.9, 0.1, 0]},
                {"eid": "b", "state": [0, 1, 0]}, {"eid": "c", "state": [0.7, 0.7, 0]},
                {"eid": "z", "state": [0, 0, 0]}]
        got = FM.nearest([1, 0, 0], rows, k=2, exclude="self")
        self.assertEqual([e for e, _ in got], ["a", "c"])
        self.assertGreater(got[0][1], got[1][1])

    def test_distances_axis(self):
        """THE ENTITY AXIS (2026-10-08, the operator's ask): an entity as
        an axis's 0-point -- every shown entity's cosine distance (1 - cos)
        from it, the reference itself at 0."""
        rows = [{"eid": "ref", "state": [1, 0, 0]}, {"eid": "a", "state": [1, 1, 0]},
                {"eid": "b", "state": [0, 1, 0]}, {"eid": "z", "state": [0, 0, 0]}]
        d = FM.distances([1, 0, 0], rows)
        self.assertAlmostEqual(d["ref"], 0.0, places=6)
        self.assertAlmostEqual(d["a"], 1 - 1 / np.sqrt(2), places=4)
        self.assertAlmostEqual(d["b"], 1.0, places=6)
        self.assertNotIn("z", d)                              # no direction, no distance
        self.assertEqual(FM.distances(None, rows), {})

    def test_toward_axis_is_signed_around_the_field_mean(self):
        """THE SIGNED ENTITY AXIS: the projection onto (ref - field mean)
        -- 0 = the typical memory, + = shares what makes ref distinctive,
        - = its opposite; ref itself lands far positive (not at 0)."""
        rows = [{"eid": "ref", "state": [3, 0]}, {"eid": "like", "state": [2, 0]},
                {"eid": "anti", "state": [-1, 0]}, {"eid": "side", "state": [0, 3]},
                {"eid": "avg", "state": [1, 1]}]
        t = FM.toward([3, 0], rows)
        self.assertGreater(t["ref"], t["like"])
        self.assertGreater(t["like"], 0)
        self.assertLess(t["anti"], 0)
        self.assertEqual(max(t, key=t.get), "ref")
        self.assertAlmostEqual(sum(t.values()) / len(t), 0.0, places=6)  # centred on the mean
        self.assertEqual(FM.toward(None, rows), {})

    def test_empty(self):
        self.assertEqual(FM.nearest([1, 0], [], k=3), [])
        self.assertEqual(FM.nearest(None, [{"eid": "a", "state": [1, 0]}], k=3), [])


if __name__ == "__main__":
    unittest.main()
