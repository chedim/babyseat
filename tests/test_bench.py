"""The held-out recall benchmark -- the field scored like a memory system.

bench.py replays a seat's history into a FRESH field, holds out a fraction of
the mentions, and scores recall() (worldline/mean/state) against ground truth
with the standard MRR + hit@k every memory framework reports. These tests pin
the pure scorer exactly and the end-to-end harness on a synthetic field with a
deterministic embedder (the same _ef_fakes.FakeEmbedder the field tests use)."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import bench  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

A = ["bilge", "pump", "float", "switch", "relay", "wiring", "breaker", "hose"]
B = ["chart", "legend", "colors", "palette", "axis", "tooltip", "gridline", "series"]
C = ["weather", "forecast", "wind", "rain", "tide", "fog", "gust", "swell"]
OLD, YOUNG = "membrane/old.py", "membrane/young.py"


def _lines(path, words, n, tag, seed):
    rng = np.random.default_rng(seed)
    return [f"{path} " + " ".join(rng.choice(words, 4, replace=False))
            + f" {tag}{i}" for i in range(n)]


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _events(texts, t0=1000.0):
    return [ST.Event(stream="discourse", role="user", text=t, ts=t0 + i)
            for i, t in enumerate(texts)]


class TestScoreRankings(unittest.TestCase):
    def test_empty_gold_is_all_zero(self):
        self.assertEqual(bench.score_rankings([], []),
                         {"mrr": 0.0, "hit1": 0.0, "hit5": 0.0,
                          "hit10": 0.0, "n": 0})

    def test_exact_mrr_and_hits(self):
        rankings = [["a", "b", "c"],   # gold at rank 1
                    ["x", "a", "y"],   # gold at rank 2
                    ["z", "w"]]        # gold absent
        gold = ["a", "a", "a"]
        s = bench.score_rankings(rankings, gold)
        self.assertEqual(s["n"], 3)
        # RR = 1 + 1/2 + 0 -> MRR = 0.5
        self.assertAlmostEqual(s["mrr"], 0.5)
        self.assertAlmostEqual(s["hit1"], 1 / 3)
        self.assertAlmostEqual(s["hit5"], 2 / 3)
        self.assertAlmostEqual(s["hit10"], 2 / 3)

    def test_hit_buckets_are_rank_based(self):
        rankings = [["x", "y", "z", "q", "p", "g"],   # gold at rank 6
                    ["g"]]                              # gold at rank 1
        s = bench.score_rankings(rankings, ["g", "g"])
        self.assertAlmostEqual(s["hit1"], 0.5)   # only the second
        self.assertAlmostEqual(s["hit5"], 0.5)   # rank 6 is not <= 5
        self.assertAlmostEqual(s["hit10"], 1.0)  # both within 10


class TestBenchmarkRecall(unittest.TestCase):
    def _world_holdout(self):
        """A mature entity (50 mentions: an A phase then a B phase) beside a
        young one (4 A + 6 C). Held out: a fresh A mention of OLD and a fresh
        C mention of YOUNG -- neither was ever seen."""
        train = (_lines(OLD, A, 25, "pa", 1) + _lines(OLD, B, 25, "pb", 2)
                 + _lines(YOUNG, A, 4, "ya", 3) + _lines(YOUNG, C, 6, "yc", 4))
        holdout = [(_lines(OLD, A, 1, "hold", 7)[0], "f:" + OLD),
                   (_lines(YOUNG, C, 1, "hold", 8)[0], "f:" + YOUNG)]
        return _events(train), holdout

    def test_scores_every_mode(self):
        train, holdout = self._world_holdout()
        scores = bench.benchmark_recall(_field(), train, holdout, FakeEmbedder())
        self.assertEqual(set(scores), set(bench.MODES))
        for s in scores.values():
            self.assertEqual(s["n"], 2)
            for k in ("mrr", "hit1", "hit5", "hit10"):
                self.assertIn(k, s)

    def test_held_out_mentions_are_attributed(self):
        train, holdout = self._world_holdout()
        scores = bench.benchmark_recall(_field(), train, holdout, FakeEmbedder())
        # both held-out mentions -- the mature entity's A-phase mention and the
        # young entity's C-topic mention -- are ranked first by the worldline read
        self.assertAlmostEqual(scores["worldline"]["hit1"], 1.0, places=6)
        self.assertAlmostEqual(scores["worldline"]["mrr"], 1.0, places=6)

    def test_an_empty_field_with_no_train_still_scores(self):
        holdout = [(_lines(OLD, A, 1, "hold", 7)[0], "f:" + OLD)]
        scores = bench.benchmark_recall(_field(), [], holdout, FakeEmbedder())
        self.assertEqual(scores["worldline"]["n"], 1)
        self.assertEqual(scores["worldline"]["mrr"], 0.0)   # nothing to hit


class TestDiscoursePairs(unittest.TestCase):
    def test_extracts_text_eid_from_its_own_deltas(self):
        f = _field()
        texts = ["the relay and wiring diagram one",
                 "the relay and wiring diagram two",
                 "the relay and wiring diagram three"]
        evs = [ST.Event(stream="discourse", role="user", text=t,
                        subject="f:alice.py", ts=1000.0 + i)
               for i, t in enumerate(texts)]
        f.write_events(evs, session="s1")
        f.process()
        pairs = bench.discourse_pairs(f)
        # three mentions of the one named subject; the speaker (person:) and
        # the event echo (e:) deltas are skipped
        self.assertEqual(sorted(pairs),
                         sorted([(t, "f:alice.py") for t in texts]))


if __name__ == "__main__":
    unittest.main()
