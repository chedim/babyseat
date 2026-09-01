"""W1a/W1b: frame-wait telemetry.

The timing discipline ("emit once, wait, act") is carried by the system,
not just the doctrine:
- a discipline-compliant TRAILING frame (last line of a long/fenced reply)
  is detected where whole-message matching missed it (buried=True);
- a repeat of an UNDELIVERED ask is not silently skipped: it bumps the
  re-ask counter and writes a `frame-wait` audit record;
- delivered asks bump the served count; summary surfaces in state.
"""

import json
import tempfile
import unittest
from pathlib import Path

from membrane import opencode as OC


def _srv(audit_path: str = ""):
    return type("S", (), {
        "membrane": type("M", (), {"audit_path": audit_path})(),
        "_served_thought_asks": {},
    })()


def _body(*contents: str) -> dict:
    return {"messages": [{"role": "assistant", "content": c}
                         for c in contents]}


class TestLastLineAsk(unittest.TestCase):
    def test_trailing_canon_frame_detected(self):
        reply = ("Long reasoning prose about the bug.\n\n"
                 "Second paragraph, still prose.\n"
                 "membrane: learn that the flaky test needs a retry")
        self.assertEqual(OC._last_line_ask(reply),
                         "membrane: learn that the flaky test needs a retry")

    def test_mid_prose_mention_ignored(self):
        reply = ("we could 'membrane: learn that X' here, but let me first\n"
                 "check the gate math before deciding anything at all")
        self.assertIsNone(OC._last_line_ask(reply))

    def test_last_line_inside_open_fence_ignored(self):
        reply = ("prose\n```python\nmembrane: learn that code is not an ask")
        self.assertIsNone(OC._last_line_ask(reply))

    def test_capture_prefix_ignored(self):
        self.assertIsNone(OC._last_line_ask("reasoning: membrane: learn that x"))

    def test_short_bare_ask_detected(self):
        self.assertEqual(OC._last_line_ask("some prose\nfield state"),
                         "field state")


class TestFrameWait(unittest.TestCase):
    def test_detect_returns_triple_and_counts(self):
        srv = _srv()
        asks = OC._assistant_thought_asks(
            srv, _body("prose prose\nmembrane: learn that a lesson"))
        self.assertEqual(asks, [("learn", "membrane: learn that a lesson",
                                 True)])
        fw = OC._frame_wait_summary(srv)
        self.assertEqual(fw["unique_asks"], 1)
        self.assertEqual(fw["buried"], 1)

    def test_reask_counts_and_audits(self):
        with tempfile.TemporaryDirectory() as td:
            ap = str(Path(td) / "audit.jsonl")
            srv = _srv(ap)
            text = "membrane: learn that a lesson"
            first = OC._assistant_thought_asks(srv, _body(text))
            self.assertEqual(len(first), 1)
            OC._serve_thoughts_served(srv, first)      # delivered
            second = OC._assistant_thought_asks(srv, _body(text))
            self.assertEqual(second, [])               # silent for the model
            fw = OC._frame_wait_summary(srv)
            self.assertEqual(fw["re_asks"], 1)
            self.assertEqual(fw["served"], 1)
            rec = json.loads(Path(ap).read_text().splitlines()[-1])
            self.assertEqual(rec["event"], "frame-wait")
            self.assertEqual(rec["fam"], "learn")

    def test_double_reask_sets_hot(self):
        srv = _srv()
        text = "field state"
        asks = OC._assistant_thought_asks(srv, _body(text))
        OC._serve_thoughts_served(srv, asks)                  # delivered
        OC._assistant_thought_asks(srv, _body(text))          # re-ask 1
        OC._assistant_thought_asks(srv, _body(text))          # re-ask 2
        self.assertTrue(OC._frame_wait_summary(srv)["hot"])

    def test_serve_accepts_pairs_and_triples(self):
        srv = _srv()
        OC._serve_thoughts_served(srv, [("state", "field state", True),
                                        ("state", "field stats")])
        fw = OC._frame_wait_summary(srv)
        self.assertEqual(fw["served"], 2)
        self.assertTrue(srv._served_thought_asks.get(
            hash(("state", "field state"))))


if __name__ == "__main__":
    unittest.main()