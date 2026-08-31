"""The orchestration router: pure-data tests (no Ollama needed).

Covers the covenants the design pins:
- the cost LADDER: local 0 -> cheap cloud -> expensive cloud; cloud is the
  LAST RESORT (never wins on an idle box; wins only under measured pressure
  with a real fit margin; budget-exhausted cloud drops out entirely).
- the imprint PRIOR: 0 trials -> routing is 100% by self-claim; lived wins
  dilute the phantom mass (K = PRIOR_MASS_K).
- the measured record: wins walk the competence EMA; losses (re-asks) count
  as trials; the calibration record (pressure, survived) drifts the
  local-slowness threshold; latency EWMAs feed the delay terms.
- failover bookkeeping and persistence round-trip.
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from membrane import router as R


def _vec(seed: float) -> np.ndarray:
    v = np.asarray([math.cos(seed), math.sin(seed), 0.3], dtype=float)
    return v / np.linalg.norm(v)


class _Reg(R.Registry):
    """Registry with the live HTTP walk stubbed out (canned payloads below)."""

    def __init__(self, *a, **kw):
        self._canned = kw.pop("tags", None)
        super().__init__(*a, **kw)

    def refresh(self):          # noqa: D102 - canned /api/tags
        tags = self._canned
        if not tags:
            return
        ps = tags.get("_ps") or {}
        self.resident = {m.get("name", "") for m in ps.get("models", [])}
        for m in tags.get("models", []) or []:
            name = m.get("name") or m.get("model") or ""
            if not name:
                continue
            rec = self.records.setdefault(name, R.ModelRecord(name=name))
            rec.kind = ("cloud" if (bool(m.get("remote_host"))
                                    or not m.get("size")) else "local")
            rec.size_bytes = int(m.get("size") or 0)
            caps = list(m.get("capabilities") or [])
            rec.caps = caps


TAGS = {
    "models": [
        {"name": "qwen2.5-coder:32b", "size": 18 * 2**30,
         "capabilities": ["tools"]},
        {"name": "deepseek-r1:8b", "size": 4 * 2**30,
         "capabilities": ["tools"]},
        {"name": "glm-5.3-flash:cloud", "size": 0,
         "remote_host": "https://api.z.ai", "capabilities": ["tools"]},
        {"name": "claude-opus:cloud", "size": 0,
         "remote_host": "https://api.anthropic.com"},
    ],
    "_ps": {"models": [{"name": "qwen2.5-coder:32b"}]},   # resident
}


class TestDiscovery(unittest.TestCase):
    def test_locals_and_clouds_detected(self):
        reg = _Reg("http://x", tags=TAGS)
        self.assertEqual(reg.records["qwen2.5-coder:32b"].kind, "local")
        self.assertEqual(reg.records["deepseek-r1:8b"].kind, "local")
        self.assertEqual(reg.records["glm-5.3-flash:cloud"].kind, "cloud")
        self.assertEqual(reg.records["claude-opus:cloud"].kind, "cloud")
        self.assertIn("qwen2.5-coder:32b", reg.resident)

    def test_price_ladder_orders_the_stack(self):
        reg = _Reg("http://x", tags=TAGS)
        p_local = reg.records["qwen2.5-coder:32b"].price()
        p_cheap = reg.records["glm-5.3-flash:cloud"].price()
        p_exp = reg.records["claude-opus:cloud"].price()
        self.assertEqual(p_local, 0.0)
        self.assertLess(p_cheap, p_exp)
        self.assertGreater(p_exp, R.CLOUD_PRICE)   # a table hit overrides default


class TestImprintPrior(unittest.TestCase):
    def test_zero_trials_routes_by_claim(self):
        reg = _Reg("http://x", tags=TAGS)
        rec = reg.records["glm-5.3-flash:cloud"]
        self.assertEqual(rec.trials, 0)
        rec.imprint = [list(_vec(1.0))]
        self.assertAlmostEqual(rec.fit_hat(_vec(1.0)), 1.0, places=5)
        self.assertAlmostEqual(rec.fit_hat(_vec(2.5)),
                               rec.imprint_fit(_vec(2.5)), places=5)

    def test_wins_dilute_the_claim(self):
        rec = R.ModelRecord(name="m", imprint=[list(_vec(1.0))])
        # 30 wins in a DIFFERENT direction: the claim's vote shrinks
        rec.trials, rec.wins = 30, 30
        rec.competence = list(_vec(2.0))
        fit_on_claim_topic = rec.fit_hat(_vec(2.5))
        # earned direction should dominate: fit at the claim's direction
        # must exceed fit far from both
        fit_at_competence = rec.fit_hat(_vec(2.0))
        self.assertGreater(fit_at_competence, fit_on_claim_topic)
        # phantom share after 30 trials:
        share = R.PRIOR_MASS_K / (30 + R.PRIOR_MASS_K)
        self.assertLess(share, 0.10)


class TestCostFloor(unittest.TestCase):
    def test_cloud_never_wins_idle_box(self):
        reg = _Reg("http://x", tags=TAGS)
        # local candidate has a GREAT imprint for this situation
        lp = reg.records["qwen2.5-coder:32b"]
        lp.imprint = [list(_vec(1.0))]
        cp = reg.records["claude-opus:cloud"]
        cp.imprint = [list(_vec(1.1))]     # marginally better fit
        best, info = reg.route(_vec(1.0), urgency=1.0)
        self.assertEqual(best.kind, "local")
        self.assertEqual(info["route"], "qwen2.5-coder:32b")

    def test_pressure_valve_lets_cloud_win(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)   # no eps-assert noise
        lp = reg.records["qwen2.5-coder:32b"]
        lp.imprint = [list(_vec(1.0))]
        lp.latency_ewma = 45.0             # >> default threshold 10s: stuck
        cp = reg.records["claude-opus:cloud"]
        # cloud is tangibly better for THIS situation: with the local stuck
        # (45s EWMA under load) the valve justifies the money.
        cp.imprint = [list(_vec(1.0)), list(_vec(0.2))]
        reg.pressure = lambda: 2.0         # monkeypatched: box is pegged
        best, info = reg.route(_vec(1.0) * 0.3 + _vec(0.2) * 0.7, urgency=2.0)
        self.assertEqual(info["route"], "claude-opus:cloud")
        self.assertIn("valve", info["reason"])

    def test_budget_exhausted_drops_cloud(self):
        reg = _Reg("http://x", tags=TAGS, cloud_budget=5.0)
        reg.record_spend(5.0)
        # budget gone: no cloud candidate may be routed
        best, info = reg.route(_vec(0.3))
        self.assertNotEqual(best and best.kind, "cloud")


class TestOutcomeRecord(unittest.TestCase):
    def test_win_walks_competence_and_counts(self):
        reg = _Reg("http://x", tags=TAGS)
        v = _vec(0.7)
        for _ in range(4):
            reg.note_outcome("deepseek-r1:8b", v, survived=True)
        rec = reg.records["deepseek-r1:8b"]
        self.assertEqual(rec.trials, 4)
        self.assertEqual(rec.wins, 4)
        self.assertIsNotNone(rec.competence)

    def test_loss_counts_trial_not_win(self):
        reg = _Reg("http://x", tags=TAGS)
        reg.note_outcome("deepseek-r1:8b", _vec(0.7), survived=False)
        rec = reg.records["deepseek-r1:8b"]
        self.assertEqual((rec.trials, rec.wins), (1, 0))
        self.assertIsNone(rec.competence)

    def test_cloud_loss_lands_in_calibration(self):
        reg = _Reg("http://x", tags=TAGS)
        reg.note_outcome("glm-5.3-flash:cloud", _vec(0.7), survived=False,
                         pressure=1.5)
        reg.note_outcome("glm-5.3-flash:cloud", _vec(0.7), survived=True,
                         pressure=2.5)
        self.assertEqual(len(reg.calibration), 2)
        thr_default = R.DEFAULT_PRESSURE_THRESHOLD
        self.assertEqual(reg.calibrated_threshold(), thr_default)  # too few
        # accrue evidence -> the measured line moves off the declared default
        for i in range(10):
            reg.note_outcome("glm-5.3-flash:cloud", _vec(0.7),
                             survived=(i % 2 == 0), pressure=3.0 + i)
        self.assertNotEqual(reg.calibrated_threshold(), thr_default)


class TestPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "router.json"
            reg = _Reg("http://x", tags=TAGS, state_path=p)
            reg.note_latency("deepseek-r1:8b", 3.3)
            reg.note_outcome("deepseek-r1:8b", _vec(0.7), survived=True)
            reg2 = _Reg("http://x", tags=TAGS, state_path=p)
            rec = reg2.records["deepseek-r1:8b"]
            self.assertEqual(rec.wins, 1)
            self.assertAlmostEqual(rec.latency_ewma, 3.3, places=5)


class TestFailoverPath(unittest.TestCase):
    def test_capability_filter(self):
        reg = _Reg("http://x", tags=TAGS)
        # a tools-needing turn must not pick a tools-less cloud model
        best, _ = reg.route(_vec(0.1), needs_tools=True)
        self.assertIn("tools", best.caps)


if __name__ == "__main__":
    unittest.main(verbosity=2)