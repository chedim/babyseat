"""The agent self-serve forget: index-form executes, wordy form proposes.

The thought intercept answers `membrane: forget ...` found in assistant
prose. Guards under test (the 2026-08-31 overshoot is the reason each
exists):
- strict index grammar ('#12', '12', '3-7', '3-7, 9'); ANY word -> proposal
- hard cap AGENT_FORGET_LIMIT -> refuse, name the operator path
- execution tombstones EVENTS on the entity field (status 'forgotten'),
  recomputes the entities they moved, audits with origin 'agent'
- proposals list nearest events but remove nothing
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


def _world(n=5):
    """A seat stub over a RAM entity field with n captured turns. The
    sentences are LONG ENOUGH to pass streams.sentences' length floor
    (short turns keep no sentence, so nothing can land near a query)."""
    f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
    for i in range(n):
        f.write_events(ST.split_turn(
            f"situation {i}: the jibberword ladder is stuck again",
            f"we unfroze action {i} and the router walked its ladder down"),
            session="s")
    f.process()
    srv = type("S", (), {"membrane": type("M", (), {
        "embedder": f.emb, "audit_path": ""})()})()
    srv.efield = f
    srv._efield_boot_seq = 0
    return srv, f


class TestParseAgentForget(unittest.TestCase):
    def test_pure_forms(self):
        self.assertEqual(OC._parse_agent_forget("#12"), [12])
        self.assertEqual(OC._parse_agent_forget("12"), [12])
        self.assertEqual(OC._parse_agent_forget("3-7"), [3, 4, 5, 6, 7])
        self.assertEqual(OC._parse_agent_forget("3-7, 9"), [3, 4, 5, 6, 7, 9])
        self.assertEqual(OC._parse_agent_forget("9,3-7,9"),
                         [3, 4, 5, 6, 7, 9])

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
        self.srv, self.f = _world()
        import membrane.opencode as _oc
        self._oc = _oc
        with unittest.mock.patch.object(_oc, "_write_audit"):
            pass

    def _forget(self, spec):
        with unittest.mock.patch('membrane.lanes._write_audit'):
            return OC._agent_forget(self.srv, spec)

    def _live(self):
        return [d for _, d in self.f.b.scan("events", {"origin": self.f.origin})
                if d.get("n") is not None and d.get("status") != "forgotten"]

    def test_index_form_executes(self):
        out = self._forget("#1, 9")
        self.assertIn("forgotten", out)
        self.assertIn("#1:", out)
        self.assertEqual(len(self._live()), 8)   # 5 turn-pairs, 2 forgotten

    def test_range_form_executes(self):
        out = self._forget("5-9")
        self.assertIn("forgotten", out)
        self.assertEqual(len(self._live()), 5)   # the other 5 of 10

    def test_out_of_range_only(self):
        out = self._forget("999")
        self.assertIn("no such event", out)
        self.assertEqual(len(self._live()), 10)

    def test_mixed_partial_uses_in_range(self):
        out = self._forget("1, 999")
        self.assertIn("#1:", out)
        self.assertIn("no such event", out)
        self.assertEqual(len(self._live()), 9)

    def test_cap_refuses(self):
        from membrane.opencode import _AGENT_FORGET_LIMIT as cap
        out = self._forget("1-" + str(cap + 1))
        self.assertIn("exceed the agent cap", out)
        self.assertIn("Nothing removed", out)
        self.assertEqual(len(self._live()), 10)

    def test_wordy_proposes_and_removes_nothing(self):
        # the pseudo-random fake embedder only agrees with a VERBATIM
        # stored sentence; a wordy ask near one proposes, never deletes
        out = self._forget("situation 3: the jibberword ladder is stuck"
                           " again")
        self.assertIn("wordy asks never delete", out)
        self.assertIn("Nearest events", out)
        self.assertEqual(len(self._live()), 10)

    def test_audit_written_on_execute(self):
        with unittest.mock.patch('membrane.lanes._write_audit') as w:
            OC._agent_forget(self.srv, "#1")
            self.assertEqual(w.call_args[0][1].get("origin"), "agent")

    def test_no_field_or_embedder_degrades(self):
        srv = type("S", (), {"membrane": type("M", (), {
            "embedder": None, "audit_path": ""})()})()
        srv.efield = self.f
        srv._efield_boot_seq = 0
        out = OC._agent_forget(srv, "#1")
        self.assertIn("unavailable", out)


if __name__ == "__main__":
    unittest.main()