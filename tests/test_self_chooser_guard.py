"""#161: the 🌱 chooser must never switch self off by itself.

Live, 2026-10-16: 25 s after a restart the page sent {self_limit: 0} "by
operator". Every 1.5 s the chooser's options were rebuilt when they changed --
and they change once the seat's state arrives after a boot -- which can reset
an open or focused <select> to its first option ("off") and fire a change.
The guard: the chooser is disabled until the seat's real state has loaded,
its options are never rebuilt while it has focus, and a change to the value
it already holds sends nothing.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def _view(state):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(L.selfView(%s)))'
          % (str(UI / "panel.js"), json.dumps(state)))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestTheView(unittest.TestCase):
    def test_before_the_seat_state_loads_it_is_not_loaded(self):
        self.assertFalse(_view(None)["loaded"])
        self.assertFalse(_view({"paused": False})["loaded"])

    def test_with_the_ladder_it_is_loaded_and_holds_the_limit(self):
        st = {"self": {"mode": "on", "limit": 2, "rung": 2, "bar": 0.1,
                       "ladder": [{"rung": 0, "name": "sleep and wake", "state": "passed"},
                                  {"rung": 1, "name": "feel itself", "state": "passed"},
                                  {"rung": 2, "name": "first reach", "state": "passed"}]}}
        v = _view(st)
        self.assertTrue(v["loaded"])
        self.assertEqual(v["limit"], 2)


class TestBothChoosersAreGuarded(unittest.TestCase):
    def test_the_panel_chooser(self):
        js = (UI / "panel.js").read_text()
        self.assertIn("sel.disabled = !v.loaded", js)
        self.assertIn("sel.getRootNode().activeElement === sel", js)   # no rebuild while focused
        self.assertIn("if (!v.loaded || n === v.limit) return;", js)   # nothing sent before load or for no change

    def test_the_home_chooser(self):
        js = (UI / "home.js").read_text()
        self.assertIn("sel.disabled = !v.loaded", js)
        self.assertIn("sel.getRootNode().activeElement === sel", js)
