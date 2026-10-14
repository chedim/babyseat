"""THE OPT-IN PROMPT'S NAME (issue #6, PR #7): membrane home's JS opt-in
prompt hard-coded the seat's birth nickname -- rename the seat (via
membrane::name) and the tab still asked 'let Argus act as you here'.
THE MERGED CONTRACT (the honest de-naming): the refusal PROMPT carries
NO name at all -- 'the assistant' in place of any hard-coded nickname --
so a rename can never make the prompt lie again (issue #6's full ask --
the LIVE agreed name -- is not yet rendered anywhere; noted there).

Pinned through jsexec.run's refusal door: no seat bound, and a live
seat with no opted-in tab.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import jsexec as JS  # noqa: E402


class _Srv:
    def __init__(self, tabs=()):
        self._js_tabs = {t: {"sid": "s", "person": "me", "seen": 1e18,
                             "device": ""}
                         for t in tabs}     # seen: forever fresh


class TestTheOptInPrompt(unittest.TestCase):
    def test_no_seat_bound_is_honest(self):
        old, JS._seat = JS._seat, None
        try:
            r = JS.run("1 + 2")
        finally:
            JS._seat = old
        self.assertEqual(r["rc"], 127)
        self.assertEqual(r["text"], "")
        self.assertIn("no seat bound", r["err"])

    def test_no_opted_in_tab_names_nobody(self):
        # THE EXHIBIT: a renamed seat's prompt still said 'Argus'. The
        # refusal's prompt must name NOBODY: 'the assistant' stands in,
        # and a birth nickname hard-coded in the door is a bug by
        # definition.
        old, JS._seat = JS._seat, _Srv()
        try:
            r = JS.run("1 + 2")
        finally:
            JS._seat = old
        self.assertEqual(r["rc"], 127)
        self.assertIn("no browser tab runs JS for this seat", r["err"])
        self.assertIn("opt in", r["err"])
        self.assertNotIn("Argus", r["err"], r)
        self.assertIn("the assistant", r["err"])


if __name__ == "__main__":
    unittest.main()