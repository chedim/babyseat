"""W2b: recall freshness weighting (stale-cell defense, part two).

Canon rows carry ``confirmed_ts`` (stamped at promotion -- the moment
evidence made them durable). ``recall_merged`` weights a small
confirmation-freshness bonus over a week scale so that at a NEAR-TIE the
just-confirmed answer outranks ageless stale canon -- snapshot recall
self-corrects instead of relying on the reader's vigilance. Evidence beats
recency: the bonus (0.05) is deliberately gentler than the candidate
recency bonus (0.15), so a stale confirmed cell must NOT lose to a mere
claim. Legacy rows without the stamp are ageless (no bonus, no penalty).
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.field import Exemplar, ExemplarStore

DAY = 86400.0


def _vec(seed: float) -> np.ndarray:
    v = np.asarray([math.cos(seed), math.sin(seed), 0.3], dtype=float)
    return v / np.linalg.norm(v)


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class TestConfirmationFreshness(unittest.TestCase):
    def _pair(self):
        stale = Exemplar(situation="q?", action="stale canon answer",
                         outcome="confirmed", vec=list(_vec(1.0)), safe=True)
        fresh = Exemplar(situation="q?", action="fresh confirmed answer",
                         outcome="confirmed", vec=list(_vec(1.0)), safe=True)
        return stale, fresh

    def test_confirmed_ts_stamped_on_promotion(self):
        s = _Store()
        cand = Exemplar(situation="q?", action="a", outcome="pending",
                        vec=list(_vec(1.0)), safe=True)
        cand.ephemeral = True
        cand.extra = {"eph_born": 1.0, "eph_ttl": DAY}
        s.append(cand)
        s.promote_ephemeral(0, safe=True, how="use")
        self.assertFalse(cand.ephemeral)
        self.assertGreater(s._confirmed_ts(cand), 0.0)
        # a REFUTE (durable-unsafe) does not invent freshness
        cand2 = Exemplar(situation="r?", action="b", outcome="pending",
                         vec=list(_vec(2.0)), safe=True)
        cand2.ephemeral = True
        cand2.extra = {"eph_born": 1.0}
        s.append(cand2)
        s.promote_ephemeral(1, safe=False, how="refute")
        self.assertEqual(s._confirmed_ts(cand2), 0.0)

    def test_near_tie_fresh_confirm_outranks_stale(self):
        stale, fresh = self._pair()
        s = _Store()
        s.append(stale)
        s.append(fresh)
        fresh.extra = {"confirmed_ts": 0.5 * DAY}   # confirmed half a day ago
        now = 10.0 * DAY
        rows = s.recall_merged(_vec(1.0), k=2, now=now)
        self.assertEqual(rows[0][0].action, "fresh confirmed answer")

    def test_big_gap_not_overturned_by_bonus(self):
        """Evidence beats recency: at a REAL cosine gap the bonus never
        flips the order (w_conf max 0.05 << typical gap)."""
        strong = Exemplar(situation="q?", action="the true answer",
                          outcome="confirmed", vec=list(_vec(1.0)), safe=True)
        weak = Exemplar(situation="q?", action="a weaker match",
                        outcome="confirmed", vec=list(_vec(1.4)), safe=True)
        weak.extra = {"confirmed_ts": 0.5 * DAY}
        s = _Store()
        s.append(strong)
        s.append(weak)
        rows = s.recall_merged(_vec(1.0), k=2, now=10.0 * DAY)
        self.assertEqual(rows[0][0].action, "the true answer")

    def test_legacy_ageless_rows_unweighted(self):
        ex = Exemplar(situation="q?", action="legacy answer",
                      outcome="confirmed", vec=list(_vec(1.0)), safe=True)
        s = _Store()
        s.append(ex)
        rows = s.recall_merged(_vec(1.0), k=2, now=10.0 * DAY)
        self.assertEqual(len(rows), 1)
        # no invented freshness: the score is the plain cosine
        self.assertAlmostEqual(rows[0][1], 1.0, places=5)

    def test_candidate_and_confirm_bands_coexist(self):
        """Both bonuses at a tie, 12h in: the freshly CONFIRMED canon
        (0.047, week scale) outweighs a 12h-old candidate (0.02, 6h scale)
        -- evidence beats recency, the W2b doctrine. Fresh from the oven
        (minutes old) the candidate still wins its band: E3's behaviour
        where it was validated (a fresh claim outranks stale canon)."""
        cand = Exemplar(situation="q?", action="candidate answer",
                        outcome="pending", vec=list(_vec(1.0)), safe=True)
        cand.ephemeral = True
        cand.extra = {"eph_born": 0.5 * DAY, "eph_ttl": 3 * DAY}
        canon = Exemplar(situation="q?", action="canon answer",
                         outcome="confirmed", vec=list(_vec(1.0)), safe=True)
        canon.extra = {"confirmed_ts": 0.5 * DAY}
        s = _Store()
        s.append(canon)
        s.append(cand)
        # 12h in: the just-confirmed canon leads
        rows = s.recall_merged(_vec(1.0), k=2, now=1.0 * DAY)
        self.assertEqual(rows[0][0].action, "canon answer")
        # minutes old: the candidate leads (E3 preserved at short range)
        cand.extra["eph_born"] = 1.0 * DAY
        rows2 = s.recall_merged(_vec(1.0), k=2, now=1.0 * DAY + 300.0)
        self.assertEqual(rows2[0][0].action, "candidate answer")


if __name__ == "__main__":
    unittest.main()