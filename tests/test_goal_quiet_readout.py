"""WHY IS THIS GOAL QUIET (#633 steps 3+4, red first): every open goal that
is not due reads back its reason -- parked (timer / entity / operator) or
in backoff -- with the next due time; the loops line carries the digest
even when no loop runs; the pass prompt tells a loop to NAME what a BLOCKED
waits on."""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from tests.test_blocked_wake import _Srv, _goal, _store  # noqa: E402


class TestQuietReadout(unittest.TestCase):
    def test_reasons_per_goal(self):
        est = _store()
        srv = _Srv(est)
        now = time.time()
        _goal(est, "tm", intent_planted_ts=0, blocked_ts=now - 10,
              blocked_until=now + 3600, blocked_on="retry after 1h")
        _goal(est, "en", intent_planted_ts=0, blocked_ts=now - 10,
              blocked_entity="w:agatha", blocked_on="on w:agatha")
        _goal(est, "op", intent_planted_ts=0, blocked_ts=now - 10, blocked_on="signature")
        _goal(est, "bo", intent_planted_ts=now - 60, landings=[{"op": "ignored"}] * 2)
        _goal(est, "fresh", intent_planted_ts=now - 60)
        rows = {r["name"]: r for r in WA.quiet_goals(srv, now=now)}
        self.assertEqual(set(rows), {"tm", "en", "op", "bo"})
        self.assertEqual(rows["tm"]["state"], "parked")
        self.assertEqual(rows["tm"]["kind"], "timer")
        self.assertAlmostEqual(rows["tm"]["next_ts"], now + 3600, delta=1)
        self.assertEqual(rows["en"]["kind"], "entity")
        self.assertIn("w:agatha", rows["en"]["why"])
        self.assertEqual(rows["op"]["kind"], "operator")
        self.assertIn("signature", rows["op"]["why"])
        self.assertEqual(rows["bo"]["state"], "backoff")
        self.assertIn("x4", rows["bo"]["why"])
        self.assertGreater(rows["bo"]["next_ts"], now)

    def test_loops_line_carries_digest_when_idle(self):
        est = _store()
        srv = _Srv(est)
        _goal(est, "op", intent_planted_ts=0, blocked_ts=time.time() - 10)
        line = WA.loops_banner_line(srv)
        self.assertIsNotNone(line)
        self.assertIn("asleep: 1 goal", line)

    def test_prompt_asks_blocked_to_name_its_wait(self):
        src = Path(WA.__file__).read_text()
        self.assertIn("BLOCKED retry after", src)
        self.assertIn("BLOCKED on <entity-id>", src)


if __name__ == "__main__":
    unittest.main()
