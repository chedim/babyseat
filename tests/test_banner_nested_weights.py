"""A Banner getter passes recall:dials' nested weights {default, legacy, rerank, ...} to rerank(): the scorer must
use one policy sub-dict, not the nest (which scored every row 0.50)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from membrane import recall_banner as RB  # noqa: E402
from membrane import recall_policy as RP  # noqa: E402
from membrane import recall_rerank as RR  # noqa: E402


class NestedWeights(unittest.TestCase):
    def test_nested_dials_weights_score_rows_non_uniformly(self):
        rows = [{"sim": 0.9, "answer": ["Caroline adopted a dog named Max."], "a_n": [1], "q_n": 1},
                {"sim": 0.2, "answer": ["Melanie likes pottery."], "a_n": [2], "q_n": 2},
                {"sim": 0.6, "answer": ["Caroline walks Max daily."], "a_n": [3], "q_n": 3}]
        ctx = {"field": None, "rows": {}, "timing": {}}
        light = [RB._light(ctx, r) for r in rows]
        nested = RP.dials()["weights"]
        self.assertIsInstance(nested["default"], dict)
        old = RR.scores
        RR.scores = lambda *a, **k: None          # the reranker is down
        try:
            out = RB.c1f_call(ctx, "rerank", [light, "Caroline dog", nested])
        finally:
            RR.scores = old
        scores = [o["score"] for o in out]
        self.assertGreater(len(set(round(s, 6) for s in scores)), 1, scores)
        self.assertFalse(all(abs(s - 0.5) < 1e-9 for s in scores), scores)


if __name__ == "__main__":
    unittest.main()
