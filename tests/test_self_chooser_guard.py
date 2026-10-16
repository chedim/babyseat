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
        self.assertIn("if (!v.loaded || n === v.limit) return { ok: false, skipped: true };", js)   # nothing sent before load or for no change

    def test_the_home_chooser(self):
        js = (UI / "home.js").read_text()
        self.assertIn("sel.disabled = !v.loaded", js)
        self.assertIn("sel.getRootNode().activeElement === sel", js)


# ---- the operator's verdict (2026-10-16): "doesn't show me the real status and
# no feedback that the rung was actually switched" -----------------------------

@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestTheRealStatus(unittest.TestCase):
    def _st(self, **kw):
        s = {"mode": "on", "limit": 3, "rung": 2, "bar": 0.34, "next": 3, "waiting_on": "looks",
             "ladder": [{"rung": n, "name": "x", "state": "passed"} for n in range(3)]}
        s.update(kw)
        return {"self": s}

    def test_it_says_where_self_is_not_just_the_limit(self):
        self.assertEqual(_view(self._st())["status"], "rung 2 ✓ · 34% → 3 (looks)")

    def test_it_says_when_the_limit_holds_it(self):
        self.assertIn("held at 2", _view(self._st(limit=2, waiting_on="the limit"))["status"])

    def test_asleep_and_held(self):
        self.assertEqual(_view(self._st(mode="off"))["status"], "🌱 asleep")
        self.assertIn("held", _view(self._st(held=True))["status"])


class TestTheFeedback(unittest.TestCase):
    def test_both_choosers_have_a_live_status_line(self):
        for f in ("panel.js", "home.js"):
            self.assertIn('class="selfstat"', (UI / f).read_text())

    def test_a_switch_is_confirmed_by_the_seats_answer(self):
        js = (UI / "panel.js").read_text()
        self.assertIn('"✓ may grow to rung " + got.limit', js)        # from the response, not the click
        self.assertIn("this._selfPick.disabled = true;           // in flight", js)
        home = (UI / "home.js").read_text()
        self.assertIn('this._statusEl.textContent = "🌱 " + out.text;', home)

    def test_the_options_refresh_when_a_rung_opens(self):
        js = (UI / "panel.js").read_text()
        self.assertIn("if (moved) this._loadSeat()", js)
