"""THE ORBS' CHAT VIEWS (2026-10-11, the operator: "make the chat switch to the
thinker's loop when I select the thinker orb, or loop-x when selecting a loop
orb, ditto the dreamer"): a loop's orb carries its session; /membrane/minds
lists the loops' sessions, the thinker's inner speech, the dreamer's picks --
and the dreamer's goal text never rides a frame (frames are ids only)."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.fieldpulse import FieldPulse  # noqa: E402


class _Th:
    mind_log = [{"ts": 1.0, "kind": "chat", "about": "battery"}]


class _Srv:
    thinker = _Th()


class TestMindsView(unittest.TestCase):
    def test_a_loop_orb_carries_its_session(self):
        fp = FieldPulse(_Srv())
        fp.focus("watch-bat", "Component:bat", "start")
        fp.loop_session("watch-bat", "Component:bat", "ses_loop1")
        frames = fp.frames()
        self.assertIn({"type": "mind.focus", "agent": "loop-1", "eid": "Component:bat", "phase": "start",
                       "sid": "ses_loop1"}, frames)
        self.assertEqual(fp.minds()["loops"], {"loop-1": {"sid": "ses_loop1", "running": True}})
        fp.focus("watch-bat", "Component:bat", "end")
        self.assertEqual(fp.minds()["loops"]["loop-1"], {"sid": "ses_loop1", "running": False})   # still readable

    def test_the_thinker_and_the_dreamer(self):
        fp = FieldPulse(_Srv())
        fp.mind("dreamer", goal="membrane::goal:g1")
        fp.dreamt("membrane::goal:g1", "keep the battery read")
        m = fp.minds()
        self.assertEqual(m["thinker"][0]["about"], "battery")
        self.assertEqual(m["dreamer"][0]["title"], "keep the battery read")
        for f in fp.frames():
            self.assertNotIn("keep the battery read", str(f))


if __name__ == "__main__":
    unittest.main()
