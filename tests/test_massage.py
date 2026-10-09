"""THE MASSAGE (2026-10-08, the operator: "the idea of Membrane's RAG should
be that it is trying to 'massage' model's input by picking words/phrases/
sentences that align it the most with the state of the field, not giving
the state verbatim", red first).

The injection was a dashboard: track stories with said/echo/observed
counts, ids, diffs -- ~700 tokens of bookkeeping. Pinned: the target is
where the field is now (its 'now' state, with the incoming message); the
candidates are the field's own sentences; a greedy pick under a token
budget maximizes the alignment of the picked set with the target (each
pick must add, near-copies add nothing); the render is the words alone --
no ids, no counts; the message's own sentences are never fed back to it;
the alignment is reported (the A/B's ruler).
"""

import os
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import massage as MS  # noqa: E402

D = 16


def _u(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


class _Back:
    def __init__(self, sents):
        self.sents = sents          # sid -> {"text", "vec"}

    def vector_top_k(self, coll, field, vec, k):
        q = _u(vec)
        sims = [(sid, float(_u(d["vec"]) @ q)) for sid, d in self.sents.items()]
        return sorted(sims, key=lambda t: -t[1])[:k]

    def get_multi(self, coll, ids):
        return {i: self.sents[i] for i in ids if i in self.sents}


class _Field:
    def __init__(self, sents, now=None):
        self.b = _Back(sents)
        self._now = {"s": now} if now is not None else {}


def _world():
    rng = np.random.default_rng(0)
    bat, sol, cab = (_u(rng.normal(size=D)) for _ in range(3))
    sents = {}
    for i in range(8):
        sents[f"b{i}"] = {"text": f"the battery sits near ninety percent, round {i}",
                          "vec": list(_u(bat + 0.15 * rng.normal(size=D)))}
        sents[f"s{i}"] = {"text": f"solar charge peaks by noon, day {i}", "vec": list(_u(sol + 0.15 * rng.normal(size=D)))}
        sents[f"c{i}"] = {"text": f"the aft cabin light was switched off {i}", "vec": list(_u(cab + 0.15 * rng.normal(size=D)))}
    return bat, sol, cab, sents


class TestMassage(unittest.TestCase):
    def test_the_pick_aligns_with_the_field_state(self):
        bat, sol, cab, sents = _world()
        fld = _Field(sents, now=_u(bat + sol))
        out = MS.build(fld, query_vec=_u(bat), budget_tokens=60)
        texts = " ".join(out["picked"])
        self.assertIn("battery", texts)
        self.assertIn("solar", texts)                      # the field's state, not just the ask
        self.assertNotIn("cabin", texts)
        self.assertGreater(out["alignment"], 0.85)

    def test_near_copies_add_nothing(self):
        bat, sol, cab, sents = _world()
        fld = _Field(sents, now=_u(bat))
        out = MS.build(fld, query_vec=_u(bat), budget_tokens=400)
        self.assertLessEqual(sum("battery" in t for t in out["picked"]), 3)

    def test_the_budget_holds(self):
        bat, sol, cab, sents = _world()
        fld = _Field(sents, now=_u(bat + sol + cab))
        out = MS.build(fld, query_vec=_u(bat), budget_tokens=25)
        self.assertLessEqual(out["tokens"], 25)

    def test_the_render_is_words_not_bookkeeping(self):
        bat, sol, cab, sents = _world()
        out = MS.build(_Field(sents, now=_u(bat)), query_vec=_u(bat), budget_tokens=80)
        block = out["block"]
        self.assertTrue(block)
        for junk in ("said ", "echo ~", "observed", "track:", "b0", "eid"):
            self.assertNotIn(junk, block)

    def test_the_message_is_never_fed_back_to_itself(self):
        bat, sol, cab, sents = _world()
        own = sents["b0"]["text"]
        out = MS.build(_Field(sents, now=_u(bat)), query_vec=_u(bat), budget_tokens=200,
                       exclude_texts=[f"hi -- {own} -- thanks"])
        self.assertNotIn(own, out["picked"])

    def test_an_empty_field_says_nothing(self):
        out = MS.build(_Field({}), query_vec=np.ones(D), budget_tokens=100)
        self.assertEqual(out["block"], "")
        self.assertEqual(out["picked"], [])


if __name__ == "__main__":
    unittest.main()
