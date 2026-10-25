"""#460: Blurp's mode switch reads on / off, not asleep / awake.

The operator, 2026-10-22: the panel's 💤 / 🌱 control is Blurp's on/off
switch (off = the loop stopped, energy frozen), but 💤 read as 'sleeping'.
Real sleep is automatic (energy runs out) and has no button. So the switch
says on / off (⏻, no sleep icon), and real sleep keeps its own 💤 display
(the sheet's energy fact and its Sleep block). Same endpoint, same states.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _st(mode):
    return {"self": {"mode": mode, "rung": 2, "bar": 0.1,
                     "ladder": [{"rung": n, "name": "x", "state": "passed"} for n in range(3)]}}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestTheSwitchReadsOnOff(unittest.TestCase):
    def test_off(self):
        v = _js("L.selfView(%s)" % json.dumps(_st("off")))
        self.assertFalse(v["on"])
        self.assertEqual(v["icon"], "⏻")
        self.assertEqual(v["label"], "Blurp: off")
        self.assertEqual(v["status"], "⏻ off")
        self.assertIn("switch on", v["title"])
        for word in ("sleep", "wake", "💤"):
            self.assertNotIn(word, v["title"] + v["label"] + v["status"])

    def test_on(self):
        v = _js("L.selfView(%s)" % json.dumps(_st("on")))
        self.assertTrue(v["on"])
        self.assertEqual(v["icon"], "⏻")
        self.assertEqual(v["label"], "Blurp: on")
        self.assertIn("switch off", v["title"])
        for word in ("sleep", "wake", "💤"):
            self.assertNotIn(word, v["title"] + v["label"])

    def test_the_exp_bar_says_off(self):
        x = _js("L.selfExp(%s)" % json.dumps(_st("off")))
        self.assertEqual(x["label"], "off")
        self.assertEqual(x["level"], "⏻ off")
        self.assertNotIn("asleep", x["caption"] + x["title"])

    def test_real_sleep_keeps_its_own_display(self):
        h = _js('L.selfSheetHtml({header: {level: 1}, energy: {energy: 0.2, asleep: true}}, {})')
        self.assertIn("asleep 💤", h)


class TestThePagesSayOnOff(unittest.TestCase):
    def test_home_switch(self):
        js = (UI / "home.js").read_text()
        self.assertIn('b.textContent = v.icon + " " + (v.on ? "on" : "off");', js)
        self.assertIn('"switch it off" : "switch it on"', js)
        head = js.split('class="spsleep"', 1)[1][:200]
        self.assertNotIn("💤", head)
        self.assertNotIn("asleep", head)
        self.assertNotIn("wake it (💤", js)

    def test_panel_switch(self):
        js = (UI / "panel.js").read_text()
        head = js.split('class="btn icon selfsleep"', 1)[1][:200]
        self.assertNotIn("💤", head)
        self.assertNotIn("asleep", head)
        self.assertIn('["selfsleep", SELF_NAME + ": off"]', js)
        self.assertIn('"switch it off" : "switch it on"', js)
        self.assertIn('got.on ? "✓ " + SELF_NAME + " is on" : "✓ " + SELF_NAME + " is off (nothing erased)"', js)
        self.assertNotIn("tap 💤", js)
        self.assertNotIn("(💤 wakes it)", js)


if __name__ == "__main__":
    unittest.main()
