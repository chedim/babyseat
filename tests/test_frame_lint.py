"""W3a: frame-lint (lane-discipline violations made visible).

The lane discipline ("at most ONE canonical frame per reply, on its own
line, as the LAST line") is taught in the doctrine; violations self-poison
the field silently. The lint makes them countable:

- stacked: two+ canonical frames in one reply -> only the first is
  servable; the rest miss the pre-gate intercept and the turn absorbs as
  junk memory. A `frame_discipline_violation` audit event fires.
- buried: a canon-frame-looking line mid-prose (multi-line reply, frame
  neither first nor last) -> whole-message and last-line scans both miss
  it. Also audited.
- fenced code is exempt (code legitimately contains frame-like lines);
  a single trailing frame is the DISCIPLINED form, never a violation.
"""

import json
import tempfile
import unittest
from pathlib import Path

from membrane import opencode as OC


def _srv(audit_path: str = ""):
    return type("S", (), {
        "membrane": type("M", (), {"audit_path": audit_path})(),
        "_frame_wait": {},
    })()


class TestFrameLint(unittest.TestCase):
    def test_single_trailing_frame_clean(self):
        srv = _srv()
        text = ("Reasoning about the fix.\n\n"
                "membrane: learn that the gate needs live_only")
        self.assertEqual(OC._frame_lint(srv, text), 0)
        self.assertEqual(srv._frame_wait.get("violations", 0), 0)

    def test_whole_message_frame_clean(self):
        srv = _srv()
        self.assertEqual(OC._frame_lint(srv, "field state"), 0)

    def test_stacked_frames_flagged(self):
        srv = _srv()
        text = ("prose here\n"
                "membrane: learn that x\n"
                "field state")
        self.assertEqual(OC._frame_lint(srv, text), 1)
        self.assertEqual(srv._frame_wait.get("viol_stacked", 0), 1)

    def test_buried_mid_prose_frame_flagged(self):
        srv = _srv()
        text = ("we discussed it here\n"
                "membrane: learn that the flaky test needs a retry\n"
                "and then moved on to other checks entirely")
        self.assertEqual(OC._frame_lint(srv, text), 1)
        self.assertEqual(srv._frame_wait.get("viol_buried", 0), 1)

    def test_fenced_code_exempt(self):
        srv = _srv()
        text = ("```bash\n"
                "membrane: learn that x\n"
                "membrane: forget #1\n"
                "```\n")
        self.assertEqual(OC._frame_lint(srv, text), 0)

    def test_audit_event_written(self):
        with tempfile.TemporaryDirectory() as d:
            ap = str(Path(d) / "audit.jsonl")
            srv = _srv(audit_path=ap)
            OC._frame_lint(srv, "prose\nmembrane: learn that a\nmembrane: "
                                "learn that b")
            rec = json.loads(Path(ap).read_text().strip())
            self.assertEqual(rec["event"], "frame_discipline_violation")
            self.assertEqual(rec["kinds"], ["stacked"])

    def test_counts_accumulate(self):
        srv = _srv()
        OC._frame_lint(srv, "a\nmembrane: learn that x\nb\nmembrane: "
                            "learn that c")
        OC._frame_lint(srv, "field state")
        self.assertEqual(srv._frame_wait.get("violations", 0), 1)
        self.assertEqual(srv._frame_wait.get("viol_stacked", 0), 1)

    def test_state_report_carries_violations(self):
        srv = _srv()
        import numpy as np
        store = type("S", (), {"exemplars": [],
                               "_superseded_set": lambda s: frozenset(),
                               "__len__": lambda s: 0,
                               "vectors": lambda s: np.zeros((0, 0))})()
        srv.membrane.store = store
        srv.membrane.embedder = type("E", (), {"model": "m"})()
        srv.membrane.sim_min = 0.85
        srv.membrane.dedup_sim = 0.95
        srv.membrane.reask_window_s = 300
        srv.membrane.demote_threshold = 1
        srv.membrane.audit_path = ""
        srv.membrane.gate_notes = []
        srv.compactor = None
        srv.backend = "b"
        srv.rag_k = 4
        srv.rag_sim_min = 0.5
        srv.rag_ctx_chars = 600
        srv.rag_mem_chars = 200
        srv.archive = False
        srv._watch_board = None
        srv._intent_board = None
        srv._codegraph = None
        srv.momentum = False
        srv.compact_model = "m"
        srv.global_field = None
        srv._frame_wait = {"violations": 2, "viol_stacked": 1, "viol_buried": 1}
        report = OC._state_report(srv)
        self.assertIn("frame discipline: 2 violation(s)", report)


if __name__ == "__main__":
    unittest.main()