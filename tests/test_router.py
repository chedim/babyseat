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
         "capabilities": ["completion", "tools"]},
        {"name": "deepseek-r1:8b", "size": 4 * 2**30,
         "capabilities": ["completion", "tools"]},
        {"name": "bakllava:latest", "size": 4 * 2**30,
         "capabilities": ["completion", "vision"]},          # no tools
        {"name": "karanchopda333/whisper:latest", "size": 2**30,
         "capabilities": ["completion", "tools"]},           # whisper marker
        {"name": "glm-5.3-flash:cloud", "size": 0,
         "remote_host": "https://api.z.ai", "capabilities": ["tools",
                                                             "completion",
                                                             "thinking"]},
        {"name": "claude-opus:cloud", "size": 0,
         "remote_host": "https://api.anthropic.com"},        # no caps served
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


class TestCompatibilityScreen(unittest.TestCase):
    """The DECLARED filter: opencode-eligibility before any scoring."""
    def test_bakllava_never_routes(self):
        reg = _Reg("http://x", tags=TAGS)
        lv = reg.records["bakllava:latest"]
        self.assertFalse(R.local_is_eligible(lv))   # completion but no tools

    def test_whisper_never_routes(self):
        reg = _Reg("http://x", tags=TAGS)
        self.assertFalse(R.local_is_eligible(
            reg.records["karanchopda333/whisper:latest"]))

    def test_tools_coder_and_unknown_caps_cloud_pass(self):
        reg = _Reg("http://x", tags=TAGS)
        self.assertTrue(R.local_is_eligible(
            reg.records["deepseek-r1:8b"]))
        # caps unknown -> conservative keep (name screen passed)
        self.assertTrue(R.local_is_eligible(
            reg.records["claude-opus:cloud"]))

    def test_oversized_local_is_never_a_candidate(self):
        # 18.5GB > max_local_gib 12: filtered OUT of locals entirely
        reg = _Reg("http://x", tags=TAGS, max_local_gib=12.0)
        best, info = reg.route(_vec(1.0), urgency=1.0)
        self.assertNotEqual(best and best.name, "qwen2.5-coder:32b")

    def test_needs_tools_never_picks_toolsless_cloud(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        # claude serves no caps -> cap_ok keeps it (declared-unknown covenant);
        # bakllava is screened out; glm declares tools. So with needs_tools
        # the route may be glm (tools-declared) or claude (caps unknown) --
        # but never bakllava or whisper.
        for _ in range(10):
            best, _ = reg.route(_vec(0.1), needs_tools=True)
            self.assertIsNotNone(best)
            self.assertNotIn(best.name, ("bakllava:latest",
                                         "karanchopda333/whisper:latest"))


class TestReversedLadder(unittest.TestCase):
    """The OPERATOR default: the cheapest eligible cloud holds the floor."""
    def test_cloud_floor_on_idle_box(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        lp = reg.records["deepseek-r1:8b"]
        lp.imprint = [list(_vec(1.0))]          # local claims a great fit
        lp.trials = 0                            # ...but has EARNED nothing
        best, info = reg.route(_vec(1.0), urgency=1.0)
        self.assertEqual(best.kind, "cloud")
        self.assertEqual(info["route"], "glm-5.3-flash:cloud")  # cheapest cloud
        self.assertIn("floor", info["reason"])

    def test_no_cloud_falls_back_to_best_local(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0, cloud_budget=0.0)
        lp = reg.records["deepseek-r1:8b"]
        lp.imprint = [list(_vec(1.0))]
        best, info = reg.route(_vec(1.0), urgency=1.0)
        self.assertEqual(best.kind, "local")
        self.assertIn("no cloud", info["reason"])

    def test_earned_local_takes_the_rank(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        v = _vec(1.0)
        lp = reg.records["deepseek-r1:8b"]       # 4GB, tools, holds in RAM
        for _ in range(6):
            reg.note_outcome("deepseek-r1:8b", v, survived=True)
        cp = reg.records["glm-5.3-flash:cloud"]
        cp.imprint = [list(_vec(2.5))]            # floor claims a DIFFERENT topic
        lp.imprint = [list(v)]
        best, info = reg.route(v, urgency=1.0)
        self.assertEqual(best.kind, "local")
        self.assertIn("earned-local", info["reason"])

    def test_tie_keeps_the_floor(self):
        # equal fits -> the incumbent cloud keeps the rank (conservative)
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        v = _vec(1.0)
        lp = reg.records["deepseek-r1:8b"]
        for _ in range(6):
            reg.note_outcome("deepseek-r1:8b", v, survived=True)
        cp = reg.records["glm-5.3-flash:cloud"]
        cp.imprint = [list(v)]
        lp.imprint = [list(v)]
        best, info = reg.route(v, urgency=1.0)
        self.assertEqual(best.kind, "cloud")

    def test_prefer_local_flips_the_floor_back(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0, prefer_local=True)
        best, info = reg.route(_vec(1.0), urgency=1.0)
        self.assertEqual(best.kind, "local")
        self.assertIn("prefer-local", info["reason"])

    def test_explore_never_cold_loads_or_picks_toys(self):
        reg = _Reg("http://x", tags=TAGS, explore=1.0)
        # the unsampleable/filtered set: oversize qwen2.5-coder:32b (too big
        # to hold), bakllava (incompatible), whisper (non-chat marker),
        # claude-opus:cloud (never explored: cloud). deepseek-r1:8b (4GB,
        # eligible) is the only sampleable local -> a local route from
        # exploration must be IT, and cloud routes must be glm (the floor).
        for _ in range(30):
            best, info = reg.route(_vec(0.3), urgency=1.0)
            if best is not None and best.kind == "local":
                self.assertEqual(best.name, "deepseek-r1:8b")
            self.assertTrue(info["reason"])

    def test_pressure_valve_record_still_accrues_on_cloud_routes(self):
        reg = _Reg("http://x", tags=TAGS)
        reg.note_outcome("glm-5.3-flash:cloud", _vec(0.7), survived=True,
                         pressure=2.0)
        self.assertEqual(len(reg.calibration), 1)

    def test_budget_exhausted_drops_cloud(self):
        reg = _Reg("http://x", tags=TAGS, cloud_budget=5.0)
        reg.record_spend(5.0)
        # budget gone: no cloud candidate may be routed
        best, info = reg.route(_vec(0.3))
        self.assertNotEqual(best and best.kind, "cloud")


class TestExploreLane(unittest.TestCase):
    """The @explore sentinel: deliberate evidence farming, never a hijack."""

    def test_lane_routes_least_tried_eligible_local(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        reg.records["deepseek-r1:8b"].trials = 3
        reg.records["bakllava:latest"].trials = 0   # ineligible: screened out
        best, info = reg.route(_vec(1.0), ceiling="@explore")
        self.assertEqual(best.name, "deepseek-r1:8b")
        self.assertTrue(info["explored"])
        self.assertIn("explore-lane", info["reason"])

    def test_lane_never_routes_cloud(self):
        for _ in range(10):
            best, info = reg = _Reg("http://x", tags=TAGS) \
                .route(_vec(0.2), ceiling="@explore")
            self.assertIsNotNone(best)
            self.assertEqual(best.kind, "local")

    def test_lane_never_cold_loads_oversize(self):
        reg = _Reg("http://x", tags=TAGS, max_local_gib=12.0)
        best, _ = reg.route(_vec(1.0), ceiling="@explore")
        # qwen2.5-coder:32b (18.5GB) may NEVER be the lane's pick here:
        # the only holding local is deepseek-r1:8b
        self.assertEqual(best.name, "deepseek-r1:8b")

    def test_lane_no_eligible_local_reports_honestly(self):
        reg = _Reg("http://x", tags={"models": [
            {"name": "karanchopda333/whisper:latest", "size": 2**30,
             "capabilities": ["completion", "tools"]}]})
        best, info = reg.route(_vec(1.0), ceiling="@explore")
        self.assertIsNone(best)
        self.assertIn("explore-lane", info["reason"])


class TestChatExploreGuard(unittest.TestCase):
    """A chat turn NEVER gambles while a cloud floor exists."""

    def test_no_explore_while_cloud_floor_holds(self):
        reg = _Reg("http://x", tags=TAGS, explore=1.0)   # dice ALWAYS in
        for _ in range(40):
            best, info = reg.route(_vec(0.3), ceiling="glm-5.3-flash:cloud")
            self.assertEqual(best.name, "glm-5.3-flash:cloud")
            self.assertFalse(info.get("explored"))

    def test_offline_box_still_explores(self):
        reg = _Reg("http://x", tags=TAGS, explore=1.0, cloud_budget=0.0)
        best, info = reg.route(_vec(0.3))
        self.assertEqual(best.kind, "local")
        self.assertTrue(info.get("explored"))

    def test_lane_ignores_operator_cloud_ceiling(self):
        # the lane is the operator's deliberate farm: even a cloud pick in
        # opencode does not stop a subagent pointed at @explore. A specific
        # pick is a HARD pick (the router never overrides it); the lane
        # ignores it by design.
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        best, _ = reg.route(_vec(1.0), ceiling="claude-opus:cloud")
        self.assertEqual(best.name, "claude-opus:cloud")  # the pick IS the rung
        best, _ = reg.route(_vec(1.0), ceiling="@explore")
        self.assertEqual(best.kind, "local")


class TestAutoselectLanes(unittest.TestCase):
    """The autoselect sentinels: the router itself as a selectable model."""

    def test_autoselect_routes_with_no_ceiling(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        best, info = reg.route(_vec(0.3), ceiling=R.AUTO_CLOUD)
        # the sentinel chose "route it all": the expensive rung is reachable
        self.assertIsNotNone(best)
        self.assertEqual(info.get("lane"), R.AUTO_CLOUD)
        # default lane picks the cheapest cloud floor, not claude
        self.assertEqual(best.name, "glm-5.3-flash:cloud")

    def test_autoselect_prefers_local(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        best, info = reg.route(_vec(0.3), ceiling=R.AUTO_LOCAL)
        self.assertEqual(best.kind, "local")
        self.assertEqual(info.get("lane"), R.AUTO_LOCAL)
        self.assertIn("prefer-local", info["reason"])
        # default lane picks the cheapest cloud; the local lane does not
        reg2 = _Reg("http://x", tags=TAGS, explore=0.0)
        best2, _ = reg2.route(_vec(0.3), ceiling=R.AUTO_CLOUD)
        self.assertEqual(best2.kind, "cloud")
        # the registry's own policy is untouched after either route (the
        # lane rides as a parameter, never a registry mutation)
        self.assertFalse(reg.prefer_local)
        self.assertFalse(reg2.prefer_local)

    def test_at_aliases_normalize(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        best, info = reg.route(_vec(0.3), ceiling="@autoselect-local")
        self.assertEqual(best.kind, "local")
        self.assertEqual(info.get("lane"), R.AUTO_LOCAL)
        best2, info2 = reg.route(_vec(0.3), ceiling="@Autoselect")
        self.assertEqual(info2.get("lane"), R.AUTO_CLOUD)
        self.assertEqual(reg.ladder(ceiling="@autoselect"),
                         reg.ladder(ceiling=R.AUTO_CLOUD))

    def test_specific_selection_is_not_rerouted(self):
        """A SPECIFIC model pick is the operator's rung, not a price hint:
        the router only routes when a lane sentinel was selected."""
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        best, info = reg.route(_vec(0.3), ceiling="claude-opus:cloud")
        # the pick itself wins (no demotion to the cheapest cloud under it)
        self.assertEqual(best.name, "claude-opus:cloud")
        self.assertIsNone(info.get("lane"))
        # the registry's own policy survives a specific pick
        self.assertFalse(reg.prefer_local)

    def test_context_window_filters_oversize_requests(self):
        """A rung whose DECLARED context window cannot hold the request is
        skipped (never an oversized-context 400 from the backend)."""
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        reg.records["glm-5.3-flash:cloud"].context_length = 100
        # a big request: the small-window rung is out, claude holds the floor
        best, _ = reg.route(_vec(0.3), ceiling=R.AUTO_CLOUD,
                            ctx_tokens=5000)
        self.assertEqual(best.name, "claude-opus:cloud")
        # unknown window (claude) or unknown request -> no filtering
        best2, _ = reg.route(_vec(0.3), ceiling=R.AUTO_CLOUD)
        self.assertEqual(best2.name, "glm-5.3-flash:cloud")
        # the ladder skips the filtered rung too
        stack = [r.name for r in reg.ladder(ceiling=R.AUTO_CLOUD,
                                            ctx_tokens=5000)]
        self.assertNotIn("glm-5.3-flash:cloud", stack)
        self.assertIn("claude-opus:cloud", stack)

    def test_estimate_tokens_rough(self):
        msgs = [{"content": "x" * 400}, {"content": "y" * 400}]
        est = R.Registry.estimate_tokens(msgs, reserve=100)
        # 800 content chars + 2x "[]" tool-call stubs = 804; /4 + reserve
        self.assertEqual(est, int(804 / 4) + 100)
        self.assertEqual(R.Registry.estimate_tokens(None, reserve=100), 100)

    def test_autoselect_ladder_full_stack(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        stack = [r.name for r in reg.ladder(ceiling=R.AUTO_CLOUD)]
        self.assertIn("claude-opus:cloud", stack)   # no ceiling: everything
        self.assertIn("deepseek-r1:8b", stack)
        self.assertEqual(stack[0], "glm-5.3-flash:cloud")  # cheapest floor

    def test_autoselect_local_ladder_prefers_local_floor(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        stack = [r.name for r in reg.ladder(ceiling=R.AUTO_LOCAL)]
        # prefer-local: holding eligible locals lead the failover stack
        # (qwen 18GB is over the hold cap; deepseek 4GB leads; clouds follow
        # as the escalation, never before the local floor)
        self.assertEqual(stack[0], "deepseek-r1:8b")
        self.assertEqual(stack[-2:], ["glm-5.3-flash:cloud",
                                      "claude-opus:cloud"])

    def test_autoselect_sentinels_are_not_rungs(self):
        reg = _Reg("http://x", tags=TAGS, explore=0.0)
        self.assertNotIn(R.AUTO_CLOUD, reg.records)
        self.assertNotIn(R.AUTO_LOCAL, reg.records)


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