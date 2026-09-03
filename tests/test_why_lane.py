"""The WHY lane (peer suggestion #2) + the fix-provenance stamp (#4)."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from membrane import runtime as rt
from membrane.field import Exemplar, ExemplarStore


class AnchorEmb:
    """Keyword-separable: 'because/rationale/by design/why/how come' ->
    dim 0; 'shipped/fixed/commit' -> dim 1; else dim 2. Anchors carry
    their keywords, so anchor sims stay high for on-topic probes."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 3
            tl = str(t).lower()
            if any(k in tl for k in ("because", "rationale", "by design",
                                     "the reason", "why", "how come",
                                     "so that", "cause")):
                v[0] = 1.0
            elif any(k in tl for k in ("shipped", "fixed", "commit",
                                       "released", "landed", "tag")):
                v[1] = 1.0
            else:
                v[2] = 1.0
            out.append(v)
        return out


def _reset_anchors():
    rt._WHY_ANCHOR_VECS = None


class TestWhyStamp(unittest.TestCase):
    """#2a+#2b: the learn-time WHY stamp via the anchor classifier."""

    def setUp(self):
        _reset_anchors()

    def tearDown(self):
        _reset_anchors()

    def _mem_with_store(self, tmp):
        m = object.__new__(rt.Membrane)
        m.embedder = AnchorEmb()
        m.store = ExemplarStore()
        m.field_path = str(Path(tmp) / "field.json")
        m.audit_path = Path(tmp) / "audit.jsonl"
        m.gate_notes = []
        m.variety_gate = False
        m.ephemeral_tier = False
        m.vague_filter = False
        m.vague_min_tokens = 4
        m.self_author = False
        m.dedup_sim = 0.99          # dedup off for isolated learns
        m.sim_min, m.reask_sim = 0.7, 0.0
        m.reask_window_s = 120.0
        m.demote_threshold = 2
        m.judge = None
        m._rng = np.random.default_rng(0)
        m.nudge_sigma = 0.0
        import threading as _th
        m.mut_lock = _th.RLock()
        m.cast = None                # no cast writes in these tests
        return m

    def test_reason_situation_gets_why_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self._mem_with_store(tmp)
            idx = m._learn(
                "the operator explains why the gate uses consensus: because "
                "a single vote misfired on tool captures, so that demotions "
                "need two independent signals by design",
                np.array([1.0, 0.0, 0.0]),
                {"verdict": "teed-pair", "sim": None, "nov": None,
                 "ex_idx": None},
                rt._chat_response("t", "the rationale recorded"),
                unsafe=False, would_absorb=False, src="user")
            self.assertIsNotNone(idx)
            ex = m.store.exemplars[idx]
            self.assertTrue((ex.extra or {}).get("why"),
                            f"why stamp missing: {ex.extra}")

    def test_plain_what_cell_unstamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self._mem_with_store(tmp)
            idx = m._learn(
                "run the test suite and paste the output",
                np.array([0.0, 0.0, 1.0]),
                {"verdict": "teed-pair", "sim": None, "nov": None,
                 "ex_idx": None},
                rt._chat_response("t", "ok"),
                unsafe=False, would_absorb=False, src="user")
            self.assertIsNotNone(idx)
            ex = m.store.exemplars[idx]
            self.assertNotIn("why", ex.extra or {})

    def test_fix_stamp_rides_commit_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self._mem_with_store(tmp)
            idx = m._learn(
                "shipped the fix in commit a430c76; the ladder walks",
                np.array([0.0, 1.0, 0.0]),
                {"verdict": "teed-pair", "sim": None, "nov": None,
                 "ex_idx": None},
                rt._chat_response("t", "ok"),
                unsafe=False, would_absorb=False, src="user")
            ex = m.store.exemplars[idx]
            fx = (ex.extra or {}).get("fix")
            self.assertIsNotNone(fx, "fix stamp missing")
            self.assertEqual(fx.get("commit"), "a430c76")


class TestWhyBoost(unittest.TestCase):
    """#2c: the recall-side boost -- why-cells outrank what-neighbors at
    a near-tie when the query is why-shaped; never the reverse."""

    def setUp(self):
        _reset_anchors()

    def tearDown(self):
        _reset_anchors()

    def test_why_cell_wins_tie_with_boost(self):
        st = ExemplarStore()
        why = Exemplar("the reason: because the constraint held",
                       "the rationale", "pending",
                       vec=[0.98, 0.199, 0.0])       # sim ~0.98 to q
        what = Exemplar("a plain what happened note", "the what", "pending",
                        vec=[1.0, 0.0, 0.0])         # sim 1.0 to q
        why.extra["why"] = 0.9
        for e in (why, what):
            e.safe = True
        st.exemplars = [why, what]
        # why-shaped query (dim-0 aligned): without boost the what cell
        # (sim 1.0) wins; with the boost the why cell must lead
        q = np.array([1.0, 0.0, 0.0])
        plain = st.recall_merged(q, k=2)
        self.assertEqual(plain[0][0], what)
        boosted = st.recall_merged(q, k=2, why_boost=rt._WHY_BOOST)
        self.assertEqual(boosted[0][0], why)
        self.assertGreaterEqual(
            boosted[0][1] - boosted[1][1], 0.0)

    def test_strong_what_beats_weak_why(self):
        st = ExemplarStore()
        why = Exemplar("the reason: because X", "the rationale", "pending",
                       vec=[1.0, 0.0, 0.0])
        what = Exemplar("exact what note", "the what", "pending",
                        vec=[0.90, 0.4358, 0.0])
        why.extra["why"] = 0.9
        for e in (why, what):
            e.safe = True
        st.exemplars = [why, what]
        q = np.array([0.0, 1.0, 0.0])   # the what cell's direction
        boosted = st.recall_merged(q, k=2, why_boost=rt._WHY_BOOST)
        self.assertEqual(boosted[0][0], what,
                         "a strong what-match lost to a weak why-match")

    def test_unstamped_field_unaffected(self):
        st = ExemplarStore()
        a = Exemplar("s1", "a1", "pending", vec=[1.0, 0.0, 0.0])
        b = Exemplar("s2", "a2", "pending", vec=[0.0, 1.0, 0.0])
        for e in (a, b):
            e.safe = True
        st.exemplars = [a, b]
        q = np.array([1.0, 0.0, 0.0])
        self.assertEqual(st.recall_merged(q, k=2, why_boost=rt._WHY_BOOST)[0][0], a)
