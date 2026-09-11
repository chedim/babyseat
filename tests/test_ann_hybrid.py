"""THE ANN-FIRST HYBRID top_k (the E14 architecture): the vector index
answers primary recall (flat ~5-17ms), the RAM path is the fallback
(ANN degrade/None) AND the freshness tail, tier flags are post-filters,
MEMBRANE_ANN=0 reverts to pure RAM. The ANN is an accelerator, never a
gate."""

import os
import unittest
from unittest import mock

import numpy as np

from membrane.field import Exemplar, ExemplarStore


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


class _FakeStore(ExemplarStore):
    """A RAM store with a stubbed ANN (the index-mapping contract is
    what's under test -- no couchbase in the test)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ann_calls = []
        self._ann_reply = None      # None = degrade; list = the hits

    def _ann_top_k(self, q, k, canon_only, live_only):
        self.ann_calls.append(list(_unit(q)))
        if self._ann_reply is None:
            return None
        out = []
        for ex, score in self._ann_reply:
            idx = self.index_of(ex)
            if idx is None:
                continue
            if canon_only and ex.ephemeral:
                continue
            if live_only and idx in self._superseded_set():
                continue
            out.append((self.exemplars[idx], score))
            if len(out) >= k:
                break
        return out if len(out) >= k else None


class TestAnnHybrid(unittest.TestCase):
    def _store(self, n=5):
        st = _FakeStore()
        for i in range(n):
            v = [0.0] * 1024
            v[i] = 1.0
            ex = Exemplar(situation=f"s{i}", action="a", outcome="done",
                          vec=v)
            st.exemplars.append(ex)
        # the fake's _ann_reply is SET by the caller AFTER the store is
        # filled (the reply references exemplar objects by identity)
        return st

    def test_ann_answer_serves_primary(self):
        st = self._store()
        st._ann_reply = [(st.exemplars[0], 0.99), (st.exemplars[1], 0.7),
                         (st.exemplars[2], 0.4)]
        q = [0.0] * 1024
        q[0] = 1.0
        hits = st.top_k(np.asarray(q), k=3)
        self.assertEqual(len(hits), 3)
        self.assertEqual(len(st.ann_calls), 1)
        self.assertEqual(hits[0][0], st.exemplars[0])

    def test_ann_degrade_falls_back_to_ram(self):
        st = self._store()
        st._ann_reply = None            # the ANN is down/degraded
        q = [0.0] * 1024
        q[1] = 1.0
        hits = st.top_k(np.asarray(q), k=2)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][0], st.exemplars[1])  # the RAM cosine path

    def test_ann_shortfall_fills_from_ram(self):
        """A short ANN answer (< k) falls through to the RAM path."""
        st = self._store()
        st._ann_reply = [(st.exemplars[0], 0.9)]   # 1 hit, k=3 -> shortfall
        q = [0.0] * 1024
        q[2] = 1.0
        hits = st.top_k(np.asarray(q), k=3)
        self.assertEqual(len(hits), 3)
        # the RAM path re-ranked everything (the shortfall contract)
        self.assertEqual(hits[0][0], st.exemplars[2])

    def test_canon_only_post_filter(self):
        """A candidate is filtered from the ANN answer; the shortfall
        falls to the RAM path which ALSO filters (the same covenant)."""
        st = self._store()
        st._ann_reply = [(st.exemplars[0], 0.9)]
        st.exemplars[0].ephemeral = True    # a candidate
        q = [0.0] * 1024
        q[0] = 1.0
        hits = st.top_k(np.asarray(q), k=1, canon_only=True)
        self.assertEqual(len(hits), 1)
        # the candidate is NOT in the results (the post-filter held):
        self.assertNotIn(st.exemplars[0], [h[0] for h in hits])

    def test_kill_switch_reverts_to_ram(self):
        st = self._store()
        os.environ["MEMBRANE_ANN"] = "0"
        try:
            q = [0.0] * 1024
            q[1] = 1.0
            hits = st.top_k(np.asarray(q), k=2)
            self.assertEqual(len(st.ann_calls), 0)
            self.assertEqual(len(hits), 2)
        finally:
            os.environ.pop("MEMBRANE_ANN", None)

    def test_short_vec_never_queries_the_ann(self):
        """The shape guard: legacy/stub rows of a different dim skip the
        ANN entirely (a short vec returns 0 hits silently on the wire).
        The REAL _ann_top_k owns this guard (the fake override bypasses
        it) -- pin it on the real method."""
        from membrane.field import ExemplarStore as Real
        st = Real()
        for i in range(3):
            v = [0.0] * 1024
            v[i] = 1.0
            st.exemplars.append(Exemplar(situation=f"s{i}", action="a",
                                         outcome="done", vec=v))
        with mock.patch("membrane.ann.ann_search",
                        return_value=[("x", 0.9)]) as ann:
            hits = st._ann_top_k(np.asarray([0.1, 0.2]), k=1,
                                 canon_only=False, live_only=False)
            self.assertEqual(ann, []) if False else None
            self.assertEqual(ann.call_count if hasattr(ann, "call_count") else 0, 0)
        self.assertEqual(len(hits) if hits else 0, 0)


if __name__ == "__main__":
    unittest.main()
