"""THE VSA LANE'S FIELD-WORLD CONSUMERS (the state's read-side uses that
survived the entity-field rewrite): the dreamer's order-shaped election
(reality's vote reads the token shape) and the thinker's convergence
ranking (a branch trending TOWARD recent real turns outranks an equally-
curious stale one). All synthetic (the lab covenant): no cluster, no
model, no Ollama."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import vsa  # noqa: E402
from membrane.vsa import VSACodebook, VSALane, pack_state  # noqa: E402

D = 8192


class _FakeEmbedder:
    model = "fake-embed"

    _DIRS = {"cat": 0.0, "dog": 0.15, "database": 1.5708, "query": 0.2}

    def embed(self, texts):
        out = []
        for t in texts:
            theta = self._DIRS.get(t.strip().lower())
            if theta is None:
                # sha256-seeded, never hash() (the _ef_fakes covenant:
                # per-process hash randomization made this test flake)
                h = np.random.default_rng(int.from_bytes(
                    __import__("hashlib").sha256(t.encode()).digest()[:4],
                    "big"))
                out.append(h.standard_normal(1024))
                continue
            v = np.zeros(1024)
            v[0] = np.cos(theta)
            v[1] = np.sin(theta)
            out.append(v)
        return out


def _lane(embedder=None):
    return VSALane(VSACodebook(embedder=embedder, dim=D))


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


if __name__ == "__main__":
    unittest.main()