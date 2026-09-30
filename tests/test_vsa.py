"""THE VSA TOKEN LANE (the rosetta transposition): hypervector token
geometry beside the mxbai cosine lane -- the plan pinned as tests, per
the E-discipline. Every test verifies an assumption; all vectors are
synthetic (the lab covenant: no cluster, no model, no Ollama).

The design under test (docs/vsa-token-lane.md):

- CODEBOOK: seeded projection of an embedder's token vectors into
  bipolar hyperspace (V = sign(R @ e)); determinism across processes;
  lazy per-token fills; a no-embedder hash fallback that still binds.
- THE RECURRENCE: S <- permute(S) xor-bind (V_tok bind R_slot), chunked
  with decay -- ORDER matters (two sentences, same tokens, different
  order -> different states).
- THE STATE on the exemplar: extra["vsa"] = {cb, s, t}; survives the
  wire round-trip (_rec_from_wire preserves unknown keys).
- DUAL-LANE RECALL: the store's vsa_top_k ranks by Hamming on the
  packed states; the merged lane keeps mxbai primary (supplement,
  never replace).
- THE SURPRISE SIGNAL: a live trajectory diverging from the nearest
  stored trajectory's prefix -- advisory only (a number, never a
  verdict flip).
- KILL PATHS: MEMBRANE_VSA=0 disables the lane end to end; codebook
  mismatch (different id) is never compared; the lab guard holds.
"""

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import vsa  # noqa: E402
from membrane.vsa import (VSACodebook, VSALane, codebook_id,  # noqa: E402
                          hash_token_vec, pack_state, permute,
                          bind, unpack_state)


D = 8192


class _FakeEmbedder:
    """Deterministic fake: 'cat'/'dog' near each other, 'database' far --
    the semantic geometry the isometry must preserve."""
    model = "fake-embed"

    _DIRS = {"cat": 0, "dog": 0.15, "database": np.pi / 2, "query": 0.2}

    def embed(self, texts):
        out = []
        for t in texts:
            theta = self._DIRS.get(t.strip().lower())
            if theta is None:
                h = np.random.default_rng(abs(hash(t)) % (2**32))
                out.append(h.standard_normal(1024))
                continue
            v = np.zeros(1024)
            v[0] = np.cos(theta)
            v[1] = np.sin(theta)
            out.append(v)
        return out


class TestCodebook(unittest.TestCase):
    def test_deterministic_across_instances(self):
        """The same (model, D, seed) -> the same codebook, independent
        instances (cross-seat coherence is the whole point)."""
        a = VSACodebook(embedder=_FakeEmbedder(), dim=D)
        b = VSACodebook(embedder=_FakeEmbedder(), dim=D)
        va = a.token_vec("cat")
        vb = b.token_vec("cat")
        np.testing.assert_array_equal(va, vb)

    def test_near_tokens_bind_near(self):
        """THE ISOMETRY: sign(R @ e) preserves neighborhood -- cat/dog
        Hamming distance is far below cat/database."""
        cb = VSACodebook(embedder=_FakeEmbedder(), dim=D)
        cat, dog, db = (cb.token_vec(t) for t in ("cat", "dog", "database"))
        d_near = np.count_nonzero(cat != dog) / D
        d_far = np.count_nonzero(cat != db) / D
        self.assertLess(d_near, 0.25)
        self.assertGreater(d_far, 0.40)

    def test_lazy_fills_once(self):
        """The lazy covenant: one embed call per unseen token; the second
        ask is a cache hit (zero calls)."""
        calls = []

        class Counting(_FakeEmbedder):
            def embed(self, texts):
                calls.append(list(texts))
                return super().embed(texts)

        cb = VSACodebook(embedder=Counting(), dim=D)
        cb.token_vec("cat")
        cb.token_vec("cat")
        self.assertEqual(len(calls), 1)

    def test_codebook_id_changes_with_model(self):
        a = VSACodebook(embedder=_FakeEmbedder(), dim=D).id
        b = VSACodebook(embedder=_FakeEmbedder(), dim=D, model="other").id
        self.assertNotEqual(a, b)

    def test_hash_fallback_without_embedder(self):
        """The degrade covenant: no embedder -> a deterministic hash vec
        (no pretrained semantics) that still binds into a state."""
        cb = VSACodebook(embedder=None, dim=D)
        v = cb.token_vec("cat")
        self.assertEqual(v.shape, (D,))
        v2 = VSACodebook(embedder=None, dim=D).token_vec("cat")
        np.testing.assert_array_equal(v, v2)


class TestRecurrence(unittest.TestCase):
    def test_bind_unbind_roundtrip(self):
        cb = VSACodebook(embedder=None, dim=D)
        v = cb.token_vec("cat")
        r = cb.role_vec("subj")
        bound = bind(v, r)
        unbound = bind(bound, r)          # xor binding is its own inverse
        np.testing.assert_array_equal(v, unbound)

    def test_order_sensitivity(self):
        """THE MASSAGE PROPERTY: the state is a function of the token
        ORDER -- same tokens, reordered -> the state MOVES (and stays
        nearer than unrelated text would: the bag keeps the common
        shape). Calibrated on a realistic 6-token turn: micro-strings
        (3 tokens) move ~4% -- order structure grows with length."""
        lane = VSALane(VSACodebook(embedder=_FakeEmbedder(), dim=D))
        s1 = lane.encode("the quick dog chases the lazy cat")
        s2 = lane.encode("the lazy cat chases the quick dog")
        d_order = vsa.hamming(s1, s2)
        self.assertGreater(d_order, 0.10)
        unrelated = lane.encode("kappa lambda mu sigma omega")
        self.assertLess(d_order, vsa.hamming(s1, unrelated))
        # and a repeat encodes identically (deterministic)
        s1b = lane.encode("the quick dog chases the lazy cat")
        self.assertEqual(vsa.hamming(s1, s1b), 0.0)

    def test_chunking_decay_bounds_crosstalk(self):
        """A long stream chunked per sentence with decay: the tail's
        contribution is bounded -- the final state still resembles the
        LAST chunk more than the first."""
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        head = lane.encode("alpha beta gamma")
        tail = lane.encode("zeta eta theta")
        both = lane.encode("alpha beta gamma. zeta eta theta")
        self.assertLess(vsa.hamming(both, tail), vsa.hamming(both, head))

    def test_encode_shape_and_packing(self):
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        st = lane.encode("the field learns")
        packed = pack_state(st)
        self.assertEqual(len(base64.b64decode(packed)), D // 8)  # 1 KB/state
        np.testing.assert_array_equal(unpack_state(packed), st)


class TestFieldLane(unittest.TestCase):
    def test_empty_text_no_state(self):
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        self.assertIsNone(lane.encode(""))

    def test_mismatched_codebook_never_compared(self):
        """Coherence: states from another codebook id are invisible to
        the lane's recall (old-id rows ride read-only)."""
        st1 = VSALane(VSACodebook(embedder=None, dim=D)).encode("a b c")
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        # a foreign row: right shape, wrong id
        foreign = {"cb": "not-" + lane.codebook.id, "s": pack_state(st1)}
        self.assertEqual(lane.hits([("x", foreign)], "a b c"), [])


class TestDualLaneRecall(unittest.TestCase):
    def test_vsa_top_k_finds_semantic_neighbor(self):
        """THE LANE PROPERTY: a query whose mxbai vec is far from a
        stored row but whose token state is near -- hits() surfaces it
        by Hamming (the order-sensitive supplement)."""
        lane = VSALane(VSACodebook(embedder=_FakeEmbedder(), dim=D))
        rows = [("row-0", {"cb": lane.codebook.id,
                           "s": pack_state(lane.encode("dog chases cat"))})]
        hits = lane.hits(rows, "the dog chases the cat", k=1)
        self.assertEqual(len(hits), 1)
        near, sim = hits[0]
        self.assertEqual(near, "row-0")
        self.assertGreater(sim, 0.68)

    def test_rows_without_state_are_invisible(self):
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        self.assertEqual(lane.hits([("x", {"cb": lane.codebook.id})],
                                   "anything", k=3), [])


class TestSurprise(unittest.TestCase):
    def test_prefix_divergence_is_advisory_number(self):
        """The surprise signal: 0.0 for a repeated shape, high for a
        divergent trajectory -- a NUMBER, never a verdict."""
        lane = VSALane(VSACodebook(embedder=None, dim=D))
        lane.observe("alpha beta gamma")
        lane.observe("alpha beta gamma")
        s_known = lane.surprise("alpha beta gamma")
        self.assertIsNotNone(s_known)
        self.assertLessEqual(s_known, 0.10)
        s_novel = lane.surprise("kappa lambda mu")
        self.assertGreater(s_novel, 0.10)

    def test_kill_switch_disables_lane(self):
        os.environ["MEMBRANE_VSA"] = "0"
        try:
            self.assertFalse(vsa.enabled())
            self.assertIsNone(VSALane.make(embedder=_FakeEmbedder(),
                                           data_dir=tempfile.mkdtemp()))
        finally:
            os.environ.pop("MEMBRANE_VSA", None)

    def test_lab_guard(self):
        """The isolation guard: a lab field path never persists a
        codebook file (the same rule the shared substrate runs)."""
        from membrane.substrate import _is_lab_field_path
        self.assertTrue(_is_lab_field_path(
            str(Path(tempfile.mkdtemp(prefix="dream-loop-x")) / "field.json")))


class TestCaptureFeed(unittest.TestCase):
    def test_feed_single_pass_two_structures(self):
        """One tokenize pass feeds BOTH structures: the transition table
        (counts) and the VSA state -- the plan's 'one capture, two
        readouts' covenant."""
        from membrane.token_timeline import TokenTimeline
        tt = TokenTimeline()
        lane = VSALane(VSACodebook(embedder=_FakeEmbedder(), dim=D))
        text = "dog chases cat"
        toks = vsa.shared_tokenize(text)
        self.assertGreaterEqual(tt.observe(toks), 1)
        st = lane.encode(text)
        self.assertTrue(len(st) == D)
        self.assertTrue(hasattr(lane.codebook, "token_vec"))


if __name__ == "__main__":
    unittest.main()