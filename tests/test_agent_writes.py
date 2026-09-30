"""The agent write lane: thought-path learn EXECUTES by default.

Covenant (`--no-agent-writes` reverts it):
- a `membrane: learn that X` found in assistant prose becomes a NOTE event
  on the entity field (src 'model', audited `origin: agent`);
- guardrails: empty payload, fenced code, and oversized pastes write nothing;
- executed asks mark the once-only ledger (a resend must not re-learn);
- with --no-agent-writes, the same ask is a proposal, never a write.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _ef_fakes as _fakes
from membrane import opencode as OC
from membrane import streams as ST
from membrane.efield import EntityField
from membrane.efstore import MemBackend


def _world():
    f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
    srv = type("S", (), {"membrane": type("M", (), {
        "embedder": f.emb, "audit_path": ""})()})()
    srv.efield = f
    srv._efield_boot_seq = 0
    srv._served_thought_asks = {}
    return srv, f


def _live(f):
    return [d for _, d in f.b.scan("events", {"origin": f.origin})
            if d.get("stream") == "discourse"]


def _learn_text(fact: str) -> str:
    return f"membrane: learn that {fact}"


class TestAgentLearn(unittest.TestCase):
    def setUp(self):
        self.srv, self.f = _world()

    def _learn(self, payload):
        with unittest.mock.patch.object(OC, "_write_audit"):
            return OC._agent_learn(self.srv, payload)

    def test_executes_and_audits(self):
        with unittest.mock.patch.object(OC, "_write_audit") as w:
            out = OC._thought_answer(self.srv, "learn",
                                     _learn_text("the flaky test needs a retry"))
        self.assertIn("agent learn", out)
        self.assertIn("note #", out)
        self.assertEqual(len(_live(self.f)), 1)
        with unittest.mock.patch.object(OC, "_write_audit") as w:
            srv, _ = _world()
            OC._agent_learn(srv, "another lesson")
            rec = w.call_args[0][1]
            self.assertEqual(rec.get("origin"), "agent")
            self.assertEqual(rec.get("tag"), "agent-note")
            self.assertEqual(rec.get("event"), "agent-learn")

    def test_empty_writes_nothing(self):
        out = self._learn("")
        self.assertIn("nothing to learn", out)
        self.assertEqual(len(_live(self.f)), 0)

    def test_fenced_code_rejected(self):
        out = self._learn("lesson with ``` code ```")
        self.assertIn("Nothing written", out)
        self.assertEqual(len(_live(self.f)), 0)

    def test_oversize_rejected(self):
        out = self._learn("x" * (OC._AGENT_LEARN_MAX_CHARS + 1))
        self.assertIn("too long", out)
        self.assertEqual(len(_live(self.f)), 0)

    def test_dedup_merges_not_appends(self):
        # write_events is content-keyed: the same lesson re-sent keeps its
        # key and is not re-applied (one note, never two).
        with unittest.mock.patch.object(OC, "_write_audit"):
            OC._thought_answer(self.srv, "learn", _learn_text("same lesson"))
            OC._thought_answer(self.srv, "learn", _learn_text("same lesson"))
        self.assertEqual(len(_live(self.f)), 1)

    def test_executed_marks_ledger(self):
        with unittest.mock.patch.object(OC, "_write_audit"):
            OC._thought_answer(self.srv, "learn", _learn_text("a lesson"))
        key = hash(("learn",
                    " ".join(_learn_text("a lesson").split()).lower()))
        self.assertTrue(self.srv._served_thought_asks.get(key))

    def test_no_agent_writes_proposes_only(self):
        srv = type("S", (), {"membrane": type("M", (), {
            "embedder": self.f.emb, "audit_path": ""})()})()
        srv.efield = self.f
        srv._efield_boot_seq = 0
        srv._served_thought_asks = {}
        srv.agent_writes = False
        with unittest.mock.patch.object(OC, "_write_audit"):
            out = OC._thought_answer(srv, "learn", _learn_text("a lesson"))
        self.assertIn("report-only", out)
        self.assertIn("PROPOSED: a lesson", out)
        self.assertEqual(len(_live(self.f)), 0)


if __name__ == "__main__":
    unittest.main()