"""THE SKETCH-FIRST GATE (the membrain upgrade, phase C -- the retina
lesson): reflex decisions answer on the cheap JL projection (96-d);
only the ambiguous band pays the full embed. THE PARITY PROPERTY is
what makes this honest (the operator's skepticism is the test's job):
the sketch's clear-band answers agree with the full-embedding path.
SKETCH_HI=0.95 near-dup replay / SKETCH_LO=0.25 clear miss; the
ambiguous band falls through to decide()'s full path."""

import unittest
from unittest import mock

import numpy as np

from membrane.field import Exemplar, ExemplarStore
from membrane.gate import decide


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


class TestSketchReflex(unittest.TestCase):
    def _store(self, n=8):
        st = ExemplarStore()
        for i in range(n):
            v = np.zeros(1024)
            v[i] = 1.0
            ex = Exemplar(situation=f"situation {i}", action="a",
                          outcome="done", vec=v)
            ex.safe = True
            st.exemplars.append(ex)
        return st

    def test_parity_near_dup(self):
        """THE PARITY PROPERTY: a probe near a stored vec -- the sketch
        path's verdict matches the full-embedding path's (both see the
        near-dup in the same direction)."""
        st = self._store()
        probe = st.exemplars[0].vec + np.random.RandomState(0).randn(1024) * 0.01
        probe = probe / np.linalg.norm(probe)
        sv = st.sketch_reflex(probe)
        self.assertIsNotNone(sv, "a near-dup probe must answer at sketch")
        self.assertEqual(sv["verdict"], "escalate-with-hint")
        self.assertEqual(sv["ex_idx"], 0)
        # parity: the FULL path also reads this as a near-dup (the same
        # nearest cell, same direction)
        full = decide(np.asarray(probe), st, k=5)
        self.assertEqual(full.get("ex_idx"), 0)

    def test_clear_miss_answers_at_sketch(self):
        """A probe orthogonal to everything: the sketch's clear miss
        (verdict escalate) -- no full-embed needed to know that."""
        st = self._store()
        probe = np.zeros(1024)
        probe[100] = 1.0          # far from all 8 basis rows
        probe = probe / np.linalg.norm(probe)
        sv = st.sketch_reflex(probe)
        self.assertIsNotNone(sv)
        self.assertEqual(sv["verdict"], "escalate")
        self.assertIsNone(sv["ex_idx"])

    def test_ambiguous_band_falls_through(self):
        """The middle band (no sketch verdict) falls through to the
        full-embed path (sketch_reflex returns None)."""
        st = self._store()
        # between the basis rows: the sketch sim lands in the middle band
        probe = np.zeros(1024)
        probe[0] = 0.5
        probe[1] = 0.5
        probe = probe / np.linalg.norm(probe)
        # the sketch sim to either basis row ~0.7: between 0.25 and 0.95
        sv = st.sketch_reflex(probe)
        self.assertIsNone(sv)
        # and the gate falls through to the full path (which answers)
        d = decide(np.asarray(probe), st, k=5)
        self.assertIn(d["verdict"], ("act", "escalate", "escalate-with-hint"))

    def test_embed_cost_saved_on_replay(self):
        """The point: a replay-heavy workload answers most turns at
        sketch cost (the sketch-reflex verdicts carry stage='sketch')."""
        st = self._store()
        probe = st.exemplars[0].vec + np.random.RandomState(1).randn(1024) * 0.01
        probe = probe / np.linalg.norm(probe)
        d = decide(np.asarray(probe), st, k=5)
        self.assertEqual(d.get("stage"), "sketch")


if __name__ == "__main__":
    unittest.main()


class TestPlasticity(unittest.TestCase):
    """THE PLASTICITY KNOB (the membrain upgrade, phase A -- predictive
    coding): the capture's TTL scales by SURPRISE (the gate's top-1 sim
    = the inverse of prediction error). A repeat (sim >= 0.98) is a
    short-lived candidate; a novel shape (< 0.5) earns a long TTL. The
    conservative default (no surprise given) is the base TTL; the
    MEMBRANE_PLASTICITY=0 kill switch reverts."""

    def test_dull_capture_short_ttl(self):
        import os
        from membrane.field import EPH_TTL_S
        st = ExemplarStore()
        ex = Exemplar(situation="a repeat", action="a", outcome="done",
                      vec=[1.0] * 1024)
        with mock.patch.dict(os.environ, {"MEMBRANE_PLASTICITY": "1"}):
            st.capture_ephemeral(ex, surprise=0.99)
        self.assertLess(ex.extra["eph_ttl"], EPH_TTL := __import__(
            "membrane.field", fromlist=["EPH_TTL_S"]).EPH_TTL_S)

    def test_novel_capture_long_ttl(self):
        import os
        from membrane.field import EPH_TTL_S
        st = ExemplarStore()
        ex = Exemplar(situation="a novel shape", action="a",
                      outcome="done", vec=[0.0] * 1024)
        with mock.patch.dict(os.environ, {"MEMBRANE_PLASTICITY": "1"}):
            st.capture_ephemeral(ex, surprise=0.3)
        self.assertGreater(ex.extra["eph_ttl"], EPH_TTL_S)

    def test_kill_switch_reverts(self):
        import os
        from membrane.field import EPH_TTL_S
        st = ExemplarStore()
        ex = Exemplar(situation="dull with switch off", action="a",
                      outcome="done", vec=[1.0] * 1024)
        with mock.patch.dict(os.environ, {"MEMBRANE_PLASTICITY": "0"}):
            st.capture_ephemeral(ex, surprise=0.99)
        self.assertEqual(ex.extra["eph_ttl"], EPH_TTL_S)
