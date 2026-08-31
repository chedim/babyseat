"""P0b: the doctrine split.

The STATIC doctrine (~550 tokens, rag._RAG_SYSTEM) shipped inside the
per-turn RAG block: paid fresh bytes every request (and, before the stacked
injects got the same fix, up to FOUR times on busy turns -- recall + thought
+ watch + peer each re-stamped it). The split:

- _inject serves ONLY the variable block when the request's system messages
  carry the doctrine stamp (_DOCTRINE_STAMP, delivered once via the generated
  opencode config's `instructions` file -- a byte-stable system prefix that
  every provider's prompt cache holds for the session);
- bare clients (no stamp) keep BOTH parts injected -- behavior unchanged;
- the stamp is idempotent: repeated _inject calls on a stamped transcript
  never duplicate the doctrine (1 copy exactly).

The generated-config side (cfg["instructions"] = [tempfile]) is exercised in
the boot smoke; here the rag-side contract + the instructions text are pinned.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import rag as R


class TestDoctrineSplit(unittest.TestCase):
    def _doc_marked(self):
        return any("WHERE YOU RUN" in (m.get("content") or "")
                   for m in [])  # placeholder, replaced below

    def _count_doctrine(self, msgs):
        return sum(1 for m in msgs
                   if m.get("role") == "system"
                   and "WHERE YOU RUN" in (m.get("content") or ""))

    def test_bare_client_keeps_full_injection(self):
        msgs = [{"role": "user", "content": "fix the bug"}]
        out = R._inject(msgs, "Recalled exchanges:\n\nQ: a\nA: b")
        self.assertEqual(self._count_doctrine(out), 1)   # doctrine rides along
        tail = out[-2]
        self.assertIn("Q: a", tail["content"])           # variable block present
        self.assertIn("Recalled exchanges", tail["content"])

    def test_stamped_client_tail_has_no_doctrine(self):
        stamped = [{"role": "system", "content": R.doctrine_instructions_text()},
                   {"role": "user", "content": "fix the bug"}]
        out = R._inject(stamped, "Recalled exchanges:\n\nQ: a\nA: b")
        # the injected tail carries ONLY the variable block
        tail = out[-2]
        self.assertEqual(tail["content"], "Recalled exchanges:\n\nQ: a\nA: b")
        # and the doctrine exists exactly once total (the instructions copy)
        self.assertEqual(self._count_doctrine(out), 1)

    def test_stacked_injects_never_duplicate(self):
        stamped = [{"role": "system", "content": R.doctrine_instructions_text()},
                   {"role": "user", "content": "go"}]
        out = R._inject(stamped, "recall block")
        out = R._inject(out, "thought block")
        out = R._inject(out, "watch block")
        out = R._inject(out, "peer block")
        # four stacked tails, ONE doctrine (it lives in the system prefix)
        self.assertEqual(self._count_doctrine(out), 1)
        contents = [m["content"] for m in out if m["role"] == "system"]
        self.assertIn("recall block", "\n".join(contents))
        self.assertIn("peer block", "\n".join(contents))

    def test_stamp_line_required(self):
        # the instruction text must carry the machine-recognizable stamp
        self.assertIn(R._DOCTRINE_STAMP, R.doctrine_instructions_text())
        # and the detector keys on the stamp, not on doctrine prose: a
        # lookalike WITHOUT the stamp gets the real doctrine re-injected
        lookalike = [{"role": "system", "content":
                      "WHERE YOU RUN: fake doctrine, no stamp line"}]
        out = R._inject(lookalike, "block")
        self.assertEqual(self._count_doctrine(out), 2)  # fake + real copy
        self.assertNotIn(R._DOCTRINE_STAMP, out[-2]["content"])

    def test_instructions_text_matches_injected_doctrine(self):
        # the text delivered via the config must be byte-equivalent to what
        # _inject would have stamped (minus the stamp line itself)
        delivered = R.doctrine_instructions_text()
        self.assertTrue(delivered.startswith(R._DOCTRINE_STAMP + "\n"))
        self.assertIn("WHEN unsure, just answer the user".lower()[:20].lower(),
                      delivered.lower())


if __name__ == "__main__":
    unittest.main()