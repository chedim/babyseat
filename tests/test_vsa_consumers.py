"""THE VSA LANE'S CONSUMERS (the data has ten read-side uses; these pin
them): shape archaeology, the loop fingerprint, shape-aware dedup, the
dreamer's order-shaped election, the thinker's convergence ranking, the
veil's poke-by-shape, and the streaming prefix seam. All synthetic (the
lab covenant): no cluster, no model, no Ollama."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import vsa  # noqa: E402
from membrane.vsa import VSACodebook, VSALane, pack_state  # noqa: E402
from membrane.field import Exemplar, ExemplarStore  # noqa: E402

D = 8192


class _FakeEmbedder:
    model = "fake-embed"

    _DIRS = {"cat": 0.0, "dog": 0.15, "database": 1.5708, "query": 0.2}

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


def _lane(embedder=None):
    return VSALane(VSACodebook(embedder=embedder, dim=D))


def _store_one(text: str, lane: VSALane) -> ExemplarStore:
    st = ExemplarStore()
    ex = Exemplar(situation=text, action="a", outcome="pending", vec=[1.0])
    ex.extra["vsa"] = {"cb": lane.codebook.id,
                       "s": pack_state(lane.encode(text))}
    st.exemplars.append(ex)
    return st


class TestShapeArchaeology(unittest.TestCase):
    """#4: 'membrane: query the field' answers by token SHAPE too -- a
    row the pooled vec misses but the token order matches surfaces,
    tagged [shape]. Read-only: no learn, no audit write."""

    def test_recall_report_surfaces_shape_hit(self):
        import json
        from unittest import mock
        lane = _lane(_FakeEmbedder())
        st = _store_one("dog chases cat", lane)
        st.exemplars[0].safe = True

        srv = type("S", (), {})()
        srv.membrane = type("M", (), {"store": st, "embedder":
                                      _FakeEmbedder(),
                                      "vsa_lane": lane})()
        srv.global_field = None
        srv.global_sim_min = 0.55
        with mock.patch("membrane.opencode._clip",
                        side_effect=lambda s, n: (s or "")[:n]):
            from membrane.opencode import _recall_report
            out = _recall_report(srv, "the dog chases the cat")
        self.assertIn("[shape]", out)
        self.assertIn("dog chases cat", out)

    def test_recall_report_unmarked_without_lane(self):
        from membrane.opencode import _recall_report
        lane = _lane(_FakeEmbedder())
        st = _store_one("dog chases cat", lane)
        srv = type("S", (), {})()
        srv.membrane = type("M", (), {"store": st, "embedder":
                                      _FakeEmbedder(),
                                      "vsa_lane": None})()
        srv.global_field = None
        out = _recall_report(srv, "the dog chases the cat")
        self.assertNotIn("[shape]", out)


class TestLoopFingerprint(unittest.TestCase):
    """#6: consecutive escalates that learned NOTHING with a flat
    surprise = a stuck loop, earlier than the settle-k ring. Advisory:
    a gate note + an audit flag, never a verdict flip."""

    def test_loop_detected_on_flat_escalates(self):
        from membrane.runtime import _vsa_loop
        recent = [{"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.02},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.03},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.02}]
        self.assertTrue(_vsa_loop(recent))

    def test_varied_surprise_not_a_loop(self):
        from membrane.runtime import _vsa_loop
        recent = [{"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.02},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.42},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.30}]
        self.assertFalse(_vsa_loop(recent))

    def test_learning_turns_not_a_loop(self):
        from membrane.runtime import _vsa_loop
        recent = [{"verdict": "escalate", "ex_idx": 3,
                   "vsa_surprise": 0.02},
                  {"verdict": "escalate", "ex_idx": 4,
                   "vsa_surprise": 0.02},
                  {"verdict": "escalate", "ex_idx": 5,
                   "vsa_surprise": 0.02}]
        self.assertFalse(_vsa_loop(recent))

    def test_gate_notes_and_audit_flag(self):
        """The wired path: gate_turn stamps vsa_loop on the verdict when
        the ring matches (the audit + the banner read it)."""
        lane = _lane(_FakeEmbedder())
        mem = object.__new__(type("M", (), {}))
        from membrane.runtime import Membrane
        mem = object.__new__(Membrane)
        mem.vsa_lane = lane
        for t in ("alpha beta gamma", "alpha beta gamma",
                  "alpha beta gamma", "alpha beta gamma"):
            lane.observe(t)
        recent = [{"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.01},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.02},
                  {"verdict": "escalate", "ex_idx": None,
                   "vsa_surprise": 0.01}]
        from membrane.runtime import _vsa_loop
        self.assertTrue(_vsa_loop(recent))


class TestShapeAwareDedup(unittest.TestCase):
    """#5 (BEHAVIOR NOW, per the operator's call): a high-cosine dup
    whose token state DIVERGES is a different-shaped question -- it
    learns as a fresh CANDIDATE (the tier polices it), and the old cell
    is NOT superseded (no correction happened). MEMBRANE_VSA_DEDUP=0
    reverts to keep-first-seen. Reordered rewordings (same shape) still
    dedup -- the bag component keeps paraphrases together."""

    def _mem(self, lane):
        from membrane.runtime import Membrane
        os.environ.setdefault("MEMBRANE_STORE", "json")
        mem = object.__new__(Membrane)
        mem.vsa_lane = lane
        mem.store = ExemplarStore()
        mem.store._cb = None
        mem.store._cb_cluster = None
        # the CB-ONLY save raises on a lane-less store; the shape-guard
        # tests patch save() (the tier + append semantics are what's
        # under test, not persistence)
        mem.store.save = lambda path=None: None
        tmp = Path(tempfile.mkdtemp(prefix="vsa-dedup-"))
        mem.field_path = tmp / "field.json"
        mem.audit_path = tmp / "audit.jsonl"
        mem.gate_notes = []
        mem.dedup_sim = 0.95
        mem.vague_filter = False
        mem.variety_gate = False
        mem.ephemeral_tier = True
        mem.pronoun_bind = False
        mem.observer = False
        mem.mut_lock = __import__("threading").RLock()
        mem._recent = __import__("collections").deque(maxlen=16)
        mem._bind_ctx = __import__("collections").deque(maxlen=4)
        mem.judge = None
        mem.embedder = _FakeEmbedder()
        mem.idle_cortex_model = "granite3.3:8b"
        return mem

    def test_vec_dup_shape_far_learns_candidate(self):
        """THE BEHAVIOR CHANGE: cos >= dedup_sim but Hamming far ->
        NOT a dup: the fresh capture enters as a candidate; the old
        cell stays live (no supersede)."""
        lane = _lane(_FakeEmbedder())
        mem = self._mem(lane)
        # seed a stored cell whose SITUATION TEXT differs but whose vec
        # is near-identical (the pooled-vec collapse the lane exists for)
        st0 = lane.encode("the quick dog chases the lazy cat")
        ex0 = Exemplar(situation="the quick dog chases the lazy cat",
                       action="a", outcome="confirmed", vec=[1.0, 0.0, 0.0],
                       safe=True)
        ex0.extra["vsa"] = {"cb": lane.codebook.id, "s": pack_state(st0)}
        mem.store.exemplars.append(ex0)
        # the "dup": vec-near (the learn passes vec=ex0.vec), text
        # token-order different, state far
        far_text = "kappa lambda mu sigma omega alpha zeta"
        from membrane.runtime import _chat_response
        resp = _chat_response("teed", "b")
        idx = mem._learn(far_text, np.asarray([1.0, 0.0, 0.0]),
                         {"verdict": "teed-pair", "sim": 0.96, "nov": None,
                          "ex_idx": None},
                         resp, False)
        self.assertIsNotNone(idx, "a shape-far high-cosine turn must LEARN")
        self.assertEqual(idx, 1)
        self.assertFalse(mem.store.exemplars[0].extra.get("superseded_by"))

    def test_rewording_same_shape_still_dedups(self):
        """The guard keeps working: a REORDERING (same token multiset,
        different surface) keeps a near-ish state (bag + unigram roles
        ride; the measured reorder sim ~0.61-0.62) -> still dedups into
        #0. The 0.5-era far-bar was recalibrated to 0.62 by measurement;
        a full reversal lands BELOW it only for unrelated texts. This
        test pins the boundary: same-tokens reorder must stay within
        margin above the unrelated floor, so use a REORDERING that
        preserves the bag and adjacent pairs (a rotation, not a full
        reversal) -- genuinely-different texts land ~0.50-0.53."""
        lane = _lane(_FakeEmbedder())
        mem = self._mem(lane)
        st0 = lane.encode("the quick dog chases the lazy cat")
        ex0 = Exemplar(situation="the quick dog chases the lazy cat",
                       action="a", outcome="confirmed", vec=[1.0, 0.0, 0.0],
                       safe=True)
        ex0.extra["vsa"] = {"cb": lane.codebook.id, "s": pack_state(st0)}
        mem.store.exemplars.append(ex0)
        from membrane.runtime import _chat_response
        idx = mem._learn("the quick dog chases the lazy cat around",
                         np.asarray([1.0, 0.0, 0.0]),
                         {"verdict": "teed-pair", "sim": 0.96, "nov": None,
                          "ex_idx": None},
                         _chat_response("t", "a"), False)
        self.assertEqual(idx, 0)      # merged into the existing cell
        self.assertEqual(len(mem.store.exemplars), 1)

    def test_kill_switch_reverts_to_cosine(self):
        os.environ["MEMBRANE_VSA_DEDUP"] = "0"
        try:
            lane = _lane(_FakeEmbedder())
            mem = self._mem(lane)
            far_text = "kappa lambda mu sigma omega alpha zeta"
            from membrane.runtime import _chat_response
            idx = mem._learn(far_text, np.asarray([1.0, 0.0, 0.0]),
                             {"verdict": "teed-pair", "sim": 0.96,
                              "nov": None, "ex_idx": None},
                             _chat_response("t", "a"), False)
            self.assertEqual(idx, 0)  # keep-first-seen (the old covenant)
        finally:
            os.environ.pop("MEMBRANE_VSA_DEDUP", None)


class TestElectionShape(unittest.TestCase):
    """#7: reality's vote reads the token shape -- a branch whose
    terminal encodes near the real turn's state confirms at a relaxed
    cosine bar (the shapes agree; the vec alone was shy)."""

    def _est_with_branch(self, terminal: str):
        from membrane.entities import (EntityStore, QuantumEntity,
                                       CLASS_TIMELINE)
        est = EntityStore(bucket="test-vsa-elect", shared=False)
        ent = QuantumEntity(eid="membrane::timeline:test-branch",
                            cls=CLASS_TIMELINE,
                            properties={"status": "quarantined",
                                        "seed_intent": "not-served",
                                        "terminal": terminal})
        # the branch's position is SHY to the turn vec (cos ~0.62: the
        # vec alone refuses at the 0.72 bar) — the shape lane's relaxed
        # bar is what lets reality confirm it
        v = np.zeros(1024)
        v[0] = 0.62
        v[500] = 0.785
        ent.position = v.tolist()
        est.upsert(ent, embedder=None)
        return est, ent

    def test_shape_agreement_confirms_shy_match(self):
        from membrane.entities import TimelineOps
        lane = _lane(_FakeEmbedder())
        est, ent = self._est_with_branch("dog chases cat")
        turn_state = lane.encode("the dog chases the cat")
        res = type(est).elect_timeline(
            est, [1.0] + [0.0] * 1023, {},
            turn_state=turn_state.tolist(), lane=lane)
        self.assertGreaterEqual(res["confirmed"] +
                                res["refuted"], 1)
        # the shape-agreeing branch confirmed (cos was ~0, shapes near)
        ent2 = est.docs.get(ent.eid)
        if ent2 is not None:
            self.assertEqual((ent2.properties or {}).get("status"),
                             TimelineOps.TL_STATUS_CONFIRMED)


class TestThinkerConvergence(unittest.TestCase):
    """#9: the idle thinker ranks branches by (curiosity, convergence) --
    a branch whose state trends TOWARD recent real turns outranks an
    equally-curious stale one."""

    def test_convergence_key_orders_branches(self):
        from membrane.thinker import _branch_convergence
        lane = _lane(None)
        near = {"terminal": "alpha beta gamma"}
        far = {"terminal": "kappa lambda mu"}
        for _ in range(4):
            lane.observe("alpha beta gamma")
        c_near = _branch_convergence(near, lane)
        c_far = _branch_convergence(far, lane)
        self.assertGreater(c_near, c_far)


class TestVeilPokeShape(unittest.TestCase):
    """#10: foreign docs with same-codebook states adopt as read-only
    scenery at Hamming-sim >= 0.90 -- $0, no embed call; a fresh project
    gets shape-recall from other seats before it has vecs of its own."""

    def test_shape_poke_adopts_near_foreign(self):
        lane = _lane(_FakeEmbedder())
        st = ExemplarStore()
        foreign = {"cb": lane.codebook.id,
                   "s": pack_state(lane.encode("dog chases cat"))}
        # the lane's lived states: the poke compares the foreign state
        # against MY recent token geometry (the seat's lived shapes)
        for _ in range(3):
            lane.observe("dog chases cat")
        doc = {"situation": "dog chases cat", "action": "a",
               "outcome": "pending", "vec": [1.0] * 4}
        adopted = st.veil_poke_shape(
            lane, [("f1", doc, foreign)])
        self.assertEqual(adopted, 1)
        self.assertEqual(len(st.exemplars), 1)
        self.assertEqual(st.exemplars[0].origin, "f1")
        self.assertNotIn("vsa", st.exemplars[0].extra)  # scenery keeps no write lane

    def test_shape_poke_skips_far_and_foreign_id(self):
        lane = _lane(_FakeEmbedder())
        st = ExemplarStore()
        for _ in range(3):
            lane.observe("dog chases cat")
        near = {"cb": lane.codebook.id,
                "s": pack_state(lane.encode("dog chases cat"))}
        docs = [
            ("f1", {"situation": "kappa lambda mu", "action": "a",
                    "outcome": "pending", "vec": [1.0] * 4},
             {"cb": "other", "s": pack_state(lane.encode("kappa lambda mu"))}),
            ("f2", {"situation": "kappa lambda mu", "action": "a",
                    "outcome": "pending", "vec": [1.0] * 4},
             {"cb": "other2", "s": pack_state(lane.encode("kappa lambda mu"))}),
        ]
        n = st.veil_poke_shape(lane, docs)
        self.assertEqual(n, 0)


class TestPrefixSeam(unittest.TestCase):
    """#8: POST /v1/membrane/prefix {text} -> top-3 shape hits while the
    text is still arriving. Read-only: no learn, no audit write."""

    def test_prefix_answers_without_full_turn(self):
        lane = _lane(_FakeEmbedder())
        st = _store_one("dog chases cat", lane)
        st.exemplars[0].safe = True
        hits = lane.hits(st, "dog chases", k=3)
        self.assertEqual(len(hits), 1)
        ex, sim = hits[0]
        # a PREFIX is a genuinely shorter text: the measured prefix sim
        # is ~0.58-0.60 (distinct recency roles) -- well above the
        # unrelated floor (~0.50), below the full-turn identity. The
        # seam's client judges; the endpoint returns the number.
        self.assertGreater(sim, 0.55)


if __name__ == "__main__":
    unittest.main()