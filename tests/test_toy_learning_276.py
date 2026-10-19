"""HOW WELL SELF HAS LEARNED EACH TOY (issue #276): the dock's popover
showed looks and error (earlier -> later half) but not what they mean. The
dock now carries a verdict from the same numbers, and the popover says it.

The yardstick is a RATE-ONLY guess: always predicting the toy's change
share q scores q(1-q). Below it by more than LEARN_MARGIN, self predicts
the toy better than how often it changes -- learning; within it, it knows
the rate and no more yet; above it, it has not understood the toy.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_sheet as SS  # noqa: E402

UI = ROOT / "membrane" / "ui"


def _looks(spec):
    """Answered looks from (p, changed) pairs."""
    return [{"kind": "reach", "p": p, "answer": "changed" if ch else "unchanged"} for p, ch in spec]


class TestVerdict(unittest.TestCase):
    def test_too_new(self):
        v = SS.learning(_looks([(0.5, True)] * 9))
        self.assertEqual(v["verdict"], "new")

    def test_mastered(self):
        # always changes, predicted so from the start (self's body)
        v = SS.learning(_looks([(1.0, True)] * 40))
        self.assertEqual(v["verdict"], "mastered")

    def test_rate_only(self):
        # changes half the time, self says 0.5 every time: the rate, no more
        v = SS.learning(_looks([(0.5, i % 2 == 0) for i in range(40)]))
        self.assertEqual(v["verdict"], "rate")
        self.assertAlmostEqual(v["rate_guess"], 0.25, places=3)

    def test_learning(self):
        # half the time, and self mostly knows which half
        v = SS.learning(_looks([(0.5, i % 2 == 0) for i in range(20)] +
                               [(0.7 if i % 2 == 0 else 0.3, i % 2 == 0) for i in range(20)]))
        self.assertEqual(v["verdict"], "learning")

    def test_getting_worse(self):
        # good early, then confidently wrong
        v = SS.learning(_looks([(0.7 if i % 2 == 0 else 0.3, i % 2 == 0) for i in range(20)] +
                               [(0.3 if i % 2 == 0 else 0.7, i % 2 == 0) for i in range(20)]))
        self.assertEqual(v["verdict"], "worse")

    def test_not_understood(self):
        # wrong all along, never better than the rate
        v = SS.learning(_looks([(0.2 if i % 2 == 0 else 0.8, i % 2 == 0) for i in range(40)]))
        self.assertEqual(v["verdict"], "lost")

    def test_every_verdict_has_words(self):
        for spec in ([(0.5, True)] * 3, [(1.0, True)] * 40):
            v = SS.learning(_looks(spec))
            self.assertTrue(v["text"], v)


def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestPopover(unittest.TestCase):
    def _toy(self, **kw):
        t = {"name": "toy:die", "kind": "die", "health": "ok", "reading": {"value": 3}, "looks": 40,
             "error_early": 0.21, "error_late": 0.19, "changes": {"self": 0, "world": 30}, "last": None,
             "active": None, "params": {}}
        t.update(kw)
        return t

    def test_the_popover_says_the_verdict(self):
        t = self._toy(learning={"verdict": "learning", "text": "learning: predicts it better than its rate alone"})
        html = _js("L.toyPopoverHtml(%s, null)" % json.dumps(t))
        self.assertIn("learning: predicts it better than its rate alone", html)
        self.assertIn('class="lv learning"', html)

    def test_the_tower_lists_its_blocks(self):
        t = self._toy(name="toy:tower", kind="tower", reading={"blocks": [True] * 5},
                      learning={"verdict": "learning", "text": "learning"},
                      blocks=[{"block": 1, "verdict": "mastered"}, {"block": 5, "verdict": "worse"}])
        html = _js("L.toyPopoverHtml(%s, null)" % json.dumps(t))
        self.assertIn("1 mastered", html)
        self.assertIn("5 getting worse", html)

    def test_no_verdict_no_line(self):
        html = _js("L.toyPopoverHtml(%s, null)" % json.dumps(self._toy()))
        self.assertNotIn('class="lv', html)


if __name__ == "__main__":
    unittest.main()
