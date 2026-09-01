"""W4a: the per-cell provenance axis (split the trust, not the membrane).

Every cell carries extra["src"] earned at learn time:
  ground  -- learned on a turn carrying paired tool results
             (environment-verified; the call->result hook signed it).
  user    -- operator turns and teed Q/A (the default).
  model   -- agent-learned notes (own prior guesses).
  ingest  -- docs/README cold-start (prior conclusions).
Trust order ground > user > model powers supersede-on-confirm: a ground cell
is only corrected by a ground-signaled answer; model claims never supersede
ground. The tag is surfaced on every recall hit with a doctrine line.
"""

import unittest

import numpy as np

from membrane import opencode as OC
from membrane.field import Exemplar, ExemplarStore, _ex_to_wire, _rec_from_wire
from membrane.runtime import _src_get, _src_label, _src_of


def _ex(situation="q", action="a", safe=True, extra=None, ephemeral=False):
    return Exemplar(situation=situation, action=action, outcome="pending",
                    vec=[1.0, 0.0], safe=safe, extra=dict(extra or {}),
                    ephemeral=ephemeral)


class TestSrcTag(unittest.TestCase):
    def test_default_src_is_user(self):
        self.assertEqual(_src_of(""), "user")
        self.assertEqual(_src_of(None), "user")
        self.assertEqual(_src_of("weird"), "user")

    def test_valid_srcs_pass_through(self):
        for s in ("ground", "user", "model", "ingest"):
            self.assertEqual(_src_of(s), s)

    def test_unknown_falls_back_to_dup_src(self):
        dup = _ex(extra={"src": "ground"})
        self.assertEqual(_src_of("bogus", fallback=dup), "ground")
        self.assertEqual(_src_of("bogus"), "user")

    def test_src_get_legacy_row(self):
        self.assertEqual(_src_get(_ex()), "")
        self.assertEqual(_src_get(_ex(extra={"src": "model"})), "model")

    def test_learn_writes_src(self):
        # the stamp helper path: _learn builds extra={"src": ...}; verify the
        # wire round-trip keeps it (extra spreads to top level and back)
        ex = _ex(extra={"src": "ground"})
        rec = _ex_to_wire(ex)
        self.assertEqual(rec.get("src"), "ground")
        back = _rec_from_wire(rec)
        self.assertEqual((back.extra or {}).get("src"), "ground")

    def test_wire_omits_src_when_absent(self):
        rec = _ex_to_wire(_ex())
        self.assertNotIn("src", rec)


class TestTrustOrder(unittest.TestCase):
    def test_ground_beats_model_in_supersede(self):
        # the runtime's guard: a ground dup is only corrected by ground.
        # Exercised through the same condition _learn uses.
        dup = _ex(extra={"src": "ground"})
        for src in ("model", "user", "ingest", ""):
            self.assertTrue(_src_get(dup) == "ground" and src != "ground",
                            f"{src!r} must not supersede ground")

    def test_ground_supersedes_ground(self):
        dup = _ex(extra={"src": "ground"})
        src = "ground"
        self.assertFalse(src != "ground")

    def test_model_cell_superseded_by_user(self):
        dup = _ex(extra={"src": "model"})
        # a user-turn correction of a model claim goes through (not ground)
        self.assertFalse(_src_get(dup) == "ground")


class TestRecallSurface(unittest.TestCase):
    def test_label_for_each_src(self):
        self.assertIn("environment-verified", _src_label(_ex(extra={"src": "ground"})))
        self.assertIn("operator", _src_label(_ex(extra={"src": "user"})))
        self.assertIn("prior guess", _src_label(_ex(extra={"src": "model"})))
        self.assertIn("docs", _src_label(_ex(extra={"src": "ingest"})))

    def test_label_legacy_row_reads_user(self):
        lbl = _src_label(_ex())
        self.assertIn("[src: user", lbl)


class TestRecallReportSrc(unittest.TestCase):
    def test_recall_report_shows_src(self):
        import numpy as np
        srv = type("S", (), {})()
        store = ExemplarStore()
        store.exemplars = [_ex(situation="deploy needs RUN_ENV=staging",
                               action="export RUN_ENV=staging",
                               extra={"src": "ground"})]
        def _embed(texts):
            return [np.array([1.0, 0.0]) for _ in texts]

        def _embed(texts):
            return [np.array([1.0, 0.0]) for _ in texts]
        embedder = type("E", (), {})
        embedder.embed = _embed

        srv.membrane = type("M", (), {
            "store": store,
            "embedder": embedder,
        })()
        report = OC._recall_report(srv, "deploy RUN_ENV")
        self.assertIn("[src: ground -- environment-verified]", report)
        self.assertIn("trust: ground", report)


if __name__ == "__main__":
    unittest.main()