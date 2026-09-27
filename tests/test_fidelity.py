"""The fidelity search (membrane/fidelity.py): edits are kept only when
they move embed(text) toward the target; every operator is proposed;
the search stops when nothing gains."""

import unittest

import numpy as np

from membrane.fidelity import OPS, candidates, fit, region_words
from membrane.slash import SlashVocab
from tests.test_slash import _CORPUS, _Emb, _AXES


def _vec(*words):
    v = np.zeros(64)
    for w in words:
        v[_AXES[w]] += 1.0
    return v / np.linalg.norm(v)


class TestFidelity(unittest.TestCase):
    def setUp(self):
        self.v = SlashVocab(_Emb(), _CORPUS, min_freq=3)

    def test_every_operator_is_proposed(self):
        c = candidates("undo the commit now quickly", self.v, region=["memory"],
                       confusers=["field"])
        ops = {lab.split(" ", 1)[0] for lab, _ in c}
        self.assertEqual(ops, set(OPS))

    def test_round_robin_keeps_every_operator_under_a_cap(self):
        c = candidates("undo the commit now quickly", self.v, region=["memory"],
                       confusers=["field"])[:len(OPS)]
        self.assertEqual({lab.split(" ", 1)[0] for lab, _ in c}, set(OPS))

    def test_fit_climbs_toward_the_target(self):
        target = _vec("revert", "memory")
        text, trace = fit("undo the commit", target, _Emb(), self.v,
                          region=["memory"], confusers=[])
        self.assertGreater(trace[-1]["cos"], trace[0]["cos"])
        self.assertTrue(all(b["cos"] > a["cos"]
                            for a, b in zip(trace, trace[1:])))
        self.assertIn("memory", text)

    def test_fit_stops_when_nothing_gains(self):
        target = _vec("commit")
        text, trace = fit("commit", target, _Emb(), self.v, region=[],
                          confusers=[])
        self.assertEqual(text, "commit")
        self.assertEqual(len(trace), 1)

    def test_region_words_are_shared_vocabulary(self):
        got = region_words(["revert the commit", "commit it now",
                            "the commit landed"], exclude={"landed"})
        self.assertEqual(got[0], "commit")
        self.assertNotIn("the", got)


if __name__ == "__main__":
    unittest.main()
