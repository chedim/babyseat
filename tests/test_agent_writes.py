"""The agent write lane: thought-path learn EXECUTES by default.

Covenant (`--no-agent-writes` reverts it):
- a `membrane: learn that X` found in assistant prose goes through the SAME
  learn_pair pipeline as teed turns (vague/dedup/variety guards) -- written
  tagged `agent-note`, audited `origin: agent`;
- guardrails: empty payload, fenced code, and oversized pastes write nothing;
- executed asks mark the once-only ledger (a resend must not re-learn);
- with --no-agent-writes, the same ask is a proposal, never a write.
"""

import unittest
import unittest.mock

import numpy as np

from membrane import opencode as OC
from membrane.field import Exemplar, ExemplarStore


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Mem:
    def __init__(self, store, embedder=None):
        self.store = store
        self.embedder = embedder
        self.audit_path = ""
        self.field_path = ""
        self.reask_ring: list = []

    def learn_pair(self, situation, action, unsafe=False, src="user"):
        """Mirror runtime.learn_pair's observable contract: append (the fake
        store has no variety gate -- dedup by exact situation here), return
        the new index (or None when the caller guards reject first)."""
        for i, e in enumerate(self.store.exemplars):
            if e.situation == situation:
                return i                 # merged into the existing cell
        self.store.append(Exemplar(situation=situation, action=action,
                                   outcome="ok",
                                   vec=self.embedder.embed([situation])[0]
                                   if self.embedder else [0.1, 0.2, 0.3]))
        return len(self.store.exemplars) - 1


class _Emb:
    """Deterministic embedder: text -> a 3-dim unit vector from its hash."""

    def embed(self, texts):
        out = []
        for t in texts:
            h = hash(t) & 0xFFFFFFFF
            v = np.asarray([(h >> 8) & 0xFF, (h >> 16) & 0xFF, h & 0xFF],
                           dtype=float)
            n = np.linalg.norm(v)
            out.append(v / n if n > 1e-12 else np.asarray([0.6, 0.8, 0.0]))
        return out


def _srv(store, embedder=None, agent_writes=True):
    return type("S", (), {
        "membrane": _Mem(store, embedder),
        "agent_writes": agent_writes,
        "_served_thought_asks": {},
    })()


def _learn_text(fact: str) -> str:
    return f"membrane: learn that {fact}"


class TestAgentLearn(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.srv = _srv(self.store, _Emb())

    def test_executes_and_audits(self):
        out = OC._thought_answer(self.srv, "learn",
                                 _learn_text("the flaky test needs a retry"))
        self.assertIn("agent learn", out)
        self.assertIn("written as exemplar #0", out)
        self.assertEqual(len(self.store), 1)
        with unittest.mock.patch.object(OC, "_write_audit") as w:
            OC._agent_learn(_srv(self.store, _Emb()), "another lesson")
            rec = w.call_args[0][1]
            self.assertEqual(rec.get("origin"), "agent")
            self.assertEqual(rec.get("tag"), "agent-note")
            self.assertEqual(rec.get("event"), "agent-learn")

    def test_empty_writes_nothing(self):
        out = OC._thought_answer(self.srv, "learn", "membrane: learn that")
        self.assertIn("nothing to learn", out)
        self.assertEqual(len(self.store), 0)

    def test_fenced_code_rejected(self):
        out = OC._thought_answer(self.srv, "learn",
                                 _learn_text("lesson with ``` code ```"))
        self.assertIn("Nothing written", out)
        self.assertEqual(len(self.store), 0)

    def test_oversize_rejected(self):
        out = OC._thought_answer(self.srv, "learn",
                                 _learn_text("x" * (OC._AGENT_LEARN_MAX_CHARS + 1)))
        self.assertIn("too long", out)
        self.assertEqual(len(self.store), 0)

    def test_dedup_merges_not_appends(self):
        OC._thought_answer(self.srv, "learn", _learn_text("same lesson"))
        OC._thought_answer(self.srv, "learn", _learn_text("same lesson"))
        self.assertEqual(len(self.store), 1)

    def test_executed_marks_ledger(self):
        OC._thought_answer(self.srv, "learn", _learn_text("a lesson"))
        key = hash(("learn", " ".join(_learn_text("a lesson").split()).lower()))
        self.assertTrue(self.srv._served_thought_asks.get(key))

    def test_no_agent_writes_proposes_only(self):
        srv = _srv(self.store, _Emb(), agent_writes=False)
        out = OC._thought_answer(srv, "learn", _learn_text("a lesson"))
        self.assertIn("report-only", out)
        self.assertIn("PROPOSED: a lesson", out)
        self.assertEqual(len(self.store), 0)


if __name__ == "__main__":
    unittest.main()