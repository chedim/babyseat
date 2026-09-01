"""The agent self-serve forget: index-form executes, wordy form proposes.

The thought intercept answers `membrane: forget ...` found in assistant
prose. Guards under test (the 2026-08-31 overshoot is the reason each
exists):
- strict index grammar ('#12', '12', '3-7', '3-7, 9'); ANY word -> proposal
- hard cap AGENT_FORGET_LIMIT -> refuse, name the operator path
- execution deletes from the store, persists, audits with origin 'agent'
- proposals list candidates but remove nothing
"""

import unittest
import unittest.mock

import numpy as np

from membrane import opencode as OC
from membrane.field import Exemplar, ExemplarStore


class _Store(ExemplarStore):
    def save(self, path=None):
        pass                      # no-op: tests assert in-RAM state only


class _Mem:
    def __init__(self, store, embedder=None):
        self.store = store
        self.embedder = embedder
        self.audit_path = ""
        self.field_path = ""        # read by _agent_forget; _Store.save ignores it


class _Emb:
    """Deterministic embedder: text -> a 3-dim unit vector from its sha256
    (NOT hash(): that is per-process randomized, so an unlucky PYTHONHASHSEED
    could drop every sim under the 0.55 proposal floor -> flaky)."""

    def embed(self, texts):
        import hashlib
        out = []
        for t in texts:
            h = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "big")
            v = np.asarray([(h >> 8) & 0xFF, (h >> 16) & 0xFF, h & 0xFF],
                           dtype=float)
            n = np.linalg.norm(v)
            out.append(v / n if n > 1e-12 else np.asarray([0.6, 0.8, 0.0]))
        return out


def _srv_with(store, embedder=None):
    return type("S", (), {"membrane": _Mem(store, embedder)})()


def _fill(store, n=5):
    for i in range(n):
        store.append(Exemplar(situation=f"situation {i}", action=f"action {i}",
                              outcome="ok", vec=[0.1, 0.2, 0.3]))


class TestParseAgentForget(unittest.TestCase):
    def test_pure_forms(self):
        self.assertEqual(OC._parse_agent_forget("#12"), [12])
        self.assertEqual(OC._parse_agent_forget("12"), [12])
        self.assertEqual(OC._parse_agent_forget("3-7"), [3, 4, 5, 6, 7])
        self.assertEqual(OC._parse_agent_forget("3-7, 9"), [3, 4, 5, 6, 7, 9])
        self.assertEqual(OC._parse_agent_forget("9,3-7,9"), [3, 4, 5, 6, 7, 9])

    def test_wordy_is_none(self):
        for s in ("everything", "the raw obj output", "12 monkeys",
                  "3-7 and 9", "the last hour", ""):
            self.assertIsNone(OC._parse_agent_forget(s), s)

    def test_negative_not_index(self):
        self.assertIsNone(OC._parse_agent_forget("-3"))

    def test_open_range_rejected(self):
        self.assertIsNone(OC._parse_agent_forget("3-"))


class TestAgentForget(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        _fill(self.store, 30)
        self.srv = _srv_with(self.store, _Emb())

    def test_index_form_executes(self):
        out = OC._agent_forget(self.srv, "#0, 5")
        self.assertIn("removed 2 of 30", out)
        self.assertEqual(len(self.store), 28)
        self.assertEqual(self.store.exemplars[0].situation, "situation 1")

    def test_range_form_executes(self):
        out = OC._agent_forget(self.srv, "3-7")
        self.assertIn("removed 5 of 30", out)
        self.assertEqual(len(self.store), 25)

    def test_out_of_range_only(self):
        out = OC._agent_forget(self.srv, "999")
        self.assertIn("Nothing removed", out)
        self.assertIn("none of those indexes exist", out)
        self.assertEqual(len(self.store), 30)

    def test_mixed_partial_uses_in_range(self):
        out = OC._agent_forget(self.srv, "0, 999")
        self.assertIn("removed 1 of 30", out)
        self.assertEqual(len(self.store), 29)

    def test_cap_refuses(self):
        out = OC._agent_forget(self.srv, "0-29")
        self.assertIn("exceed the agent cap", out)
        self.assertIn("Nothing removed", out)
        self.assertEqual(len(self.store), 30)

    def test_wordy_proposes_and_removes_nothing(self):
        out = OC._agent_forget(self.srv, "that raw obj response junk")
        self.assertIn("wordy asks never delete", out)
        self.assertIn("Nothing removed", out)
        self.assertEqual(len(self.store), 30)

    def test_audit_written_on_execute(self):
        srv = _srv_with(self.store, _Emb())
        with unittest.mock.patch.object(OC, "_write_audit") as w:
            OC._agent_forget(srv, "#1")
            self.assertEqual(w.call_args[0][1].get("origin"), "agent")

    def test_no_store_or_embedder_degrades(self):
        out = OC._agent_forget(_srv_with(None, None), "#1")
        self.assertIn("unavailable", out)


if __name__ == "__main__":
    unittest.main()