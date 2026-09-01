"""Drift velocity telemetry: the temporal axis, audit-first.

The gate's spatial stats (sim/nov/rank) say nothing about SEQUENCE. Drift =
cosine between consecutive turn vectors -- stamped on the verdict and the
audit record, never a gate input (promoted only if the audit trail earns it):
- first turn -> None (no previous vector)
- identical text -> 1.0 (hovering: the loop-detection trigger)
- unrelated text -> far from 1.0 (context switch)
- embedder failure degrades to None, never breaks the turn
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from membrane.runtime import Membrane


class _SeqEmb:
    """Hash-of-text embedder over a stable 16-dim basis: same text -> same
    vector, different text -> near-orthogonal vectors (deterministic)."""

    def embed(self, texts):
        out = []
        for t in texts:
            h = int.from_bytes(
                __import__("hashlib").sha256(t.encode()).digest()[:8], "big")
            v = np.zeros(16)
            for j in range(16):
                v[j] = ((h >> (j * 4)) & 0xF) / 15.0
            v[0] = 1.0                       # shared component: some overlap
            n = np.linalg.norm(v)
            out.append((v / n).tolist())
        return out


class _R:
    def respond(self, body, hint=None, auth=None):
        return {"choices": [{"message": {"content": "ok"}}]}


def _mk(tmp: str) -> Membrane:
    return Membrane(
        responder=_R(), audit_path=str(Path(tmp) / "audit.jsonl"),
        field_path=str(Path(tmp) / "field.json"), embedder=_SeqEmb(),
        judge=None, observer=True, ephemeral=False)


def _body(q):
    return {"messages": [{"role": "user", "content": q}]}


class TestDriftTelemetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_first_turn_is_none_then_values(self):
        m = _mk(self.tmp)
        v, *_ = m.gate_turn(_body("hello world"))
        self.assertIsNone(v["drift"])
        v2, *_ = m.gate_turn(_body("hello world"))
        self.assertIsNotNone(v2["drift"])
        self.assertGreater(v2["drift"], 0.999)   # same text: hovering
        v3, *_ = m.gate_turn(_body("completely different subject matter"))
        self.assertLess(v3["drift"], 0.999)      # a different trajectory

    def test_lands_in_audit_record(self):
        m = _mk(self.tmp)
        m.gate_turn(_body("hello world"))
        m.gate_turn(_body("hello world again"))
        verdict = {"verdict": "escalate", "sim": 0.0, "nov": None,
                   "drift": 0.42, "ex_idx": None}
        m._audit("q", verdict, {"choices": [{"message": {"content": "c"}}]},
                 None, learned=False)
        rec = json.loads(Path(self.tmp, "audit.jsonl").read_text().strip())
        self.assertEqual(rec["drift"], 0.42)

    def test_malformed_prev_vec_degrades_to_none(self):
        m = _mk(self.tmp)
        m.gate_turn(_body("hello world"))
        m._last_vec = [0.1, 0.2]          # wrong dim: drift must skip, not crash
        v, *_ = m.gate_turn(_body("hello world"))
        self.assertIsNone(v["drift"])
        self.assertIsNotNone(m._last_vec)  # tracking continues from the good vec


if __name__ == "__main__":
    unittest.main()