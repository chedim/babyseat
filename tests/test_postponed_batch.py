"""The postponed batch (docs/post-restart-plan.md items 1-4, 6):

- auto-settle (repeat-k): an escalating near-dup that keeps failing to learn
  settles -- replay the safe near match, or hint with the demoted one;
- the smartness metric: losses walk a loss EMA and outcome_fit punishes the
  earned loss direction; fit_hat stays claim-only at 0 trials;
- provenance: teed learning stamps the ANSWERING model (Exemplar.model);
- lazy imprints: seed_imprints_async starts on the first route, not at boot;
- the capture quota: machinery constants are clipped well below prose.
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import router as R
from membrane.field import Exemplar, ExemplarStore
from membrane.opencode import _CAP_ARGS, _CAP_BODY, _hygiene_demote_screened_models


def _vec(seed: float) -> np.ndarray:
    v = np.asarray([math.cos(seed), math.sin(seed), 0.3], dtype=float)
    return v / np.linalg.norm(v)


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Emb:
    """Unit 1024-dim embeddings via a deterministic token hash (real dim so
    gate math and settle checks agree)."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(1024)
            for i, w in enumerate((t or "").split()):
                v[i % 1024] += 1.0 + (hash((w, i)) % 97) / 97.0
            n = float(np.linalg.norm(v))
            out.append(v / n if n > 1e-12 else v)
        return out


def _route_info(name: str) -> dict:
    return {"route": name, "kind": "cloud", "pressure": 0.5}


class _Rec:
    """Drives one 'stuck' turn through the real gate + ring (learn skipped)."""

    def __init__(self, store, ex_idx):
        self.store = store
        self.i = ex_idx

    def settle_turn(self, mem, q):
        verdict, vec, _, _ = mem.gate_turn(
            {"messages": [{"role": "user", "content": q}]})
        mem._remember(q, vec, verdict, None)   # ex_idx None: the guard-skip
        return verdict


class TestAutoSettle(unittest.TestCase):
    def _mem(self, settle_k=3):
        from membrane.runtime import Membrane, StubResponder
        mem = Membrane(StubResponder(), field_path=":memory:",
                       audit_path=":memory:", embedder=_Emb(),
                       settle_k=settle_k)
        mem.store = _Store()
        return mem

    def test_unlearned_repeats_settle_to_replay(self):
        mem = self._mem(settle_k=3)
        q = "how do I rebase onto main"
        e = _Emb().embed([mem._time_prefix() + q])[0]
        mem.store.append(Exemplar(situation=q, action="rebase onto origin/main",
                                  outcome="pending", vec=list(e), safe=True))
        drift = list((e + 0.05) / np.linalg.norm(e + 0.05))
        mem.store.append(Exemplar(situation=q, action="force-push instead",
                                  outcome="pending", vec=drift, safe=True))
        t = _Rec(mem.store, 0)
        for _ in range(3):
            self.assertEqual(t.settle_turn(mem, q)["verdict"], "escalate")
        verdict = t.settle_turn(mem, q)
        self.assertEqual(verdict["verdict"], "settle-replay")
        self.assertEqual(verdict.get("action"), "rebase onto origin/main")
        self.assertTrue(verdict.get("settle"))

    def test_demoted_near_match_settles_to_hint(self):
        mem = self._mem(settle_k=2)
        q = "how do I reset a lost commit"
        bad = "rm -rf .git"
        e = _Emb().embed([mem._time_prefix() + q])[0]
        mem.store.append(Exemplar(situation=q, action=bad,
                                  outcome="pending", vec=list(e), safe=False))
        drift = list((e + 0.05) / np.linalg.norm(e + 0.05))
        mem.store.append(Exemplar(situation=q, action="panic",
                                  outcome="pending", vec=drift, safe=True))
        t = _Rec(mem.store, 0)
        for _ in range(2):
            self.assertEqual(t.settle_turn(mem, q)["verdict"], "escalate")
        settled = t.settle_turn(mem, q)     # the 3rd stuck turn settles (k=2)
        self.assertEqual(settled["verdict"], "escalate-with-hint")
        self.assertTrue(settled.get("settle"))
        self.assertEqual(settled["hint"], bad)

    def test_first_escalate_never_settles(self):
        mem = self._mem(settle_k=3)
        verdict, _, _, _ = mem.gate_turn(
            {"messages": [{"role": "user",
                           "content": "brand new question entirely"}]})
        self.assertEqual(verdict["verdict"], "escalate")
        self.assertNotIn("settle", verdict)


class TestOutcomeConditionedFit(unittest.TestCase):
    def test_loss_ema_pulls_fit_down_on_lost_topic(self):
        rec = R.ModelRecord(name="m")
        rec.imprint = [list(_vec(1.0))]        # claims topic A
        rec.trials, rec.wins = 1, 0            # one catastrophic loss on topic B
        rec.loss_ema = list(_vec(2.2))
        self.assertGreater(rec.fit_hat(_vec(1.0)), 0.0)   # the claim still seeds
        self.assertLessEqual(rec.outcome_fit(_vec(2.2)), 0.0)  # B subtracts

    def test_scalar_winrate_cannot_hide_a_chronic_loss(self):
        rec = R.ModelRecord(name="m")
        rec.imprint = [list(_vec(1.0))]
        rec.trials, rec.wins = 10, 9           # 90% scalar win-rate...
        rec.loss_ema = list(_vec(2.2))         # ...earned on chronic topic B
        rec.competence = None                  # the wins were on OTHER topics
        self.assertLess(rec.outcome_fit(_vec(2.2)), rec.imprint_fit(_vec(2.2)))

    def test_zero_trials_routes_by_claim_only(self):
        rec = R.ModelRecord(name="m", imprint=[list(_vec(1.0))])
        rec.loss_ema = list(_vec(2.2))         # history exists...
        rec.trials, rec.wins = 0, 0            # ...but nothing is EARNED yet
        self.assertAlmostEqual(rec.fit_hat(_vec(1.0)), 1.0, places=5)

    def test_router_records_loss_ema(self):
        reg = R.Registry("http://x")
        reg.refresh = lambda: None
        reg.records["a"] = R.ModelRecord(name="a")
        reg.note_outcome("a", _vec(2.2), survived=False)
        self.assertIsNotNone(reg.records["a"].loss_ema)


class TestProvenanceStamp(unittest.TestCase):
    def test_learn_pair_stamps_route_model(self):
        import collections
        import tempfile
        import threading
        from membrane.runtime import Membrane
        with tempfile.TemporaryDirectory() as td:
            mem = Membrane.__new__(Membrane)
            mem.store = _Store()
            mem.field_path = Path(td) / "f.json"
            mem.audit_path = Path(td) / "a.jsonl"
            mem.embedder = None
            mem.vague_filter = False
            mem.dedup_sim = 2.0
            mem.variety_gate = False
            mem.mut_lock = threading.RLock()
            mem.observer = False
            mem.self_author = False
            mem._recent = collections.deque(maxlen=4)
            mem.gate_notes = []
            verdict = {"verdict": "escalate",
                       "route": _route_info("glm-5.3-flash:cloud")}
            mem._learn("how to add a repo field", _vec(0.3), verdict,
                       "git remote add origin <url>", unsafe=False)
            self.assertEqual("glm-5.3-flash:cloud",
                             mem.store.exemplars[0].model)


class _HygRec:
    def __init__(self, name, caps=("completion", "tools")):
        self.name = name
        self.kind = "local"
        self.caps = list(caps)


class _HygReg:
    def __init__(self, records):
        self.records = records


class TestHygieneSweep(unittest.TestCase):
    def test_screened_model_answers_are_demoted(self):
        import threading
        store = _Store()
        store.append(Exemplar(situation="q1", action="a1", outcome="pending",
                              vec=[0.1, 0.2, 0.3], safe=True,
                              model="bakllava:latest"))
        store.append(Exemplar(situation="q2", action="a2", outcome="pending",
                              vec=[0.1, 0.2, 0.35], safe=True,
                              model="deepseek-r1:8b"))     # eligible
        store.append(Exemplar(situation="q3", action="a3", outcome="pending",
                              vec=[0.1, 0.2, 0.4], safe=False,
                              model="bakllava:latest"))    # already demoted

        class _M:
            def __init__(self):
                self.store = store
                self.mut_lock = threading.RLock()
                self.field_path = ""
                self.demote_threshold = 1

        class _Srv:
            membrane = _M()
            router = _HygReg({"bakllava:latest": _HygRec("bakllava:latest",
                                                         caps=("completion",)),
                              "deepseek-r1:8b": _HygRec("deepseek-r1:8b")})

        demoted = _hygiene_demote_screened_models(_Srv())
        self.assertEqual(demoted, 1)
        self.assertFalse(store.exemplars[0].safe)     # screened-out -> demoted
        self.assertTrue(store.exemplars[1].safe)      # eligible -> untouched
        self.assertFalse(store.exemplars[2].safe)     # was already unsafe

    def test_quota_clips_below_old_machinery_caps(self):
        self.assertLess(_CAP_ARGS, 2400)
        self.assertLess(_CAP_BODY, 8192)


class TestLazyImprints(unittest.TestCase):
    def test_no_seed_until_first_route(self):
        reg = R.Registry("http://x", embedder=object())
        self.assertFalse(reg._seed_started)
        reg.route(_vec(0.3))
        self.assertTrue(reg._seed_started)

    def test_route_and_ladder_both_arm_it(self):
        reg = R.Registry("http://x", embedder=object())
        reg.ladder()
        self.assertTrue(reg._seed_started)


if __name__ == "__main__":
    unittest.main()