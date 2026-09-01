"""W4b: the user ledger (the operator's verbatim decision log).

- append-only JSONL next to the field, one {"ts","text"} per line.
- MODEL-UNWRITABLE: only the operator learn lane appends (tested at the
  _learn_report level); corrupt lines are skipped, never fatal.
- USER-EDITABLE: a hand-pruned file is re-read (mtime-keyed cache).
- recalled VERBATIM when near the current turn, marked operator-authored.
"""

import json
import os
import tempfile
import time
import unittest

import numpy as np

from membrane.ledger import UserLedger


def _emb(texts):
    # toy embedder: bag-of-character- presence vectors (deterministic)
    out = []
    for t in texts:
        v = np.zeros(16)
        for ch in t.lower():
            v[ord(ch) % 16] += 1.0
        out.append(v.tolist())
    return out


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "field.ledger.jsonl")

    def tearDown(self):
        self.d.cleanup()

    def test_append_and_reread(self):
        led = UserLedger(self.path)
        self.assertTrue(led.append("deploy needs RUN_ENV=staging"))
        self.assertTrue(led.append("always use the venv python for tests"))
        self.assertEqual(led.count(), 2)
        # a NEW instance (fresh process) reads the same file
        led2 = UserLedger(self.path)
        self.assertEqual(led2.count(), 2)
        recs = led2._read()
        self.assertEqual(recs[0]["text"], "deploy needs RUN_ENV=staging")

    def test_verbatim_preserved(self):
        led = UserLedger(self.path)
        raw = "Ship behind the  --feature flag, never on Friday"
        led.append(raw)
        got = led._read()[0]["text"]
        self.assertEqual(got, raw)          # verbatim: no reflow of meaning

    def test_corrupt_lines_skipped(self):
        with open(self.path, "w") as f:
            f.write('{"ts":"t","text":"good line"}\n')
            f.write("NOT JSON (operator mid-edit)\n")
            f.write('{"ts":"t2","text":"second good"}\n')
        led = UserLedger(self.path)
        self.assertEqual(led.count(), 2)
        self.assertEqual(led._read()[1]["text"], "second good")

    def test_empty_and_overlong_rejected(self):
        led = UserLedger(self.path)
        self.assertFalse(led.append(""))
        self.assertFalse(led.append("   "))
        long = "x" * (UserLedger.MAX_TEXT + 100)
        self.assertTrue(led.append(long))
        self.assertEqual(len(led._read()[0]["text"]), UserLedger.MAX_TEXT)

    def test_top_k_finds_near_turn(self):
        led = UserLedger(self.path)
        led.append("deploy needs RUN_ENV=staging")
        led.append("tests always run with the venv python")
        led.append("the compactor must never delete canon rows")
        q = _emb(["deploy needs RUN_ENV=staging"])[0]
        hits = led.top_k(q, type("E", (), {"embed": staticmethod(_emb)})())
        self.assertTrue(hits)
        self.assertIn("RUN_ENV", hits[0][0]["text"])

    def test_block_marks_operator_authority(self):
        led = UserLedger(self.path)
        led.append("deploy needs RUN_ENV=staging")
        q = _emb(["what env does deploy need?"])[0]
        emb = type("E", (), {"embed": staticmethod(_emb)})()
        blk = led.block(q, emb)
        self.assertIn("[membrane-ledger]", blk)
        self.assertIn("OPERATOR's verbatim", blk)
        self.assertIn("RUN_ENV=staging", blk)

    def test_block_none_when_nothing_near(self):
        led = UserLedger(self.path)
        emb = type("E", (), {"embed": staticmethod(_emb)})()
        self.assertIsNone(led.block(np.ones(16), emb))
        led.append("zzzz qqqq xx9 unrelated editors preference")
        q = _emb(["deploy staging RUN_ENV flag"])[0]
        # the toy embedder's char-presence vectors overlap on common letters
        # (0.76); the proxy's default bar is for a real embedder -- an
        # explicitly raised bar proves the sim gate itself works
        self.assertIsNone(led.block(q, emb, sim_min=0.9))
        hits = led.block(q, emb, sim_min=0.5)
        self.assertIsNotNone(hits)

    def test_hand_prune_is_reread(self):
        led = UserLedger(self.path)
        led.append("decision one")
        led.append("decision two")
        self.assertEqual(led.count(), 2)
        # the operator prunes by hand: rewrite with one line, bump mtime
        with open(self.path, "w") as f:
            f.write(json.dumps({"ts": "t", "text": "decision two"}) + "\n")
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertEqual(led.count(), 1)
        self.assertEqual(led._read()[0]["text"], "decision two")


if __name__ == "__main__":
    unittest.main()