"""#161: the 🌱 chooser must never switch self off by itself -- and since
#299 (the chooser gave way to acceptance) neither may the 💤/🌱 sleep toggle.

Live, 2026-10-16: 25 s after a restart the page sent {self_limit: 0} "by
operator". Every 1.5 s the chooser's options were rebuilt when they changed --
and they change once the seat's state arrives after a boot -- which can reset
an open or focused <select> to its first option ("off") and fire a change.
The guard: the chooser is disabled until the seat's real state has loaded,
its options are never rebuilt while it has focus, and a change to the value
it already holds sends nothing. #299: the toggle keeps the same guard: disabled
until the state loads, nothing sent before that or for no change.
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

    def test_with_the_ladder_it_is_loaded_and_says_awake_or_asleep(self):
        st = {"self": {"mode": "on", "rung": 2, "bar": 0.1,
                       "ladder": [{"rung": 0, "name": "sleep and wake", "state": "passed"},
                                  {"rung": 1, "name": "feel itself", "state": "passed"},
                                  {"rung": 2, "name": "first reach", "state": "passed"}]}}
        v = _view(st)
        self.assertTrue(v["loaded"])
        self.assertTrue(v["on"])
        st["self"]["mode"] = "off"
        self.assertFalse(_view(st)["on"])


class TestBothTogglesAreGuarded(unittest.TestCase):
    def test_the_panel_toggle(self):
        js = (UI / "panel.js").read_text()
        self.assertIn("this._selfSleep.disabled = !v.loaded;", js)
        self.assertIn("if (!v.loaded || !!on === v.on) return { ok: false, skipped: true };", js)   # nothing sent before load or for no change

    def test_the_home_toggle(self):
        js = (UI / "home.js").read_text()
        self.assertIn("b.disabled = !v.loaded || !!this._sleepBusy;", js)
        self.assertIn("if (!v.loaded) return;", js.split("_onSleep() {", 1)[1][:400])


# ---- the operator's verdict (2026-10-16): "doesn't show me the real status and
# no feedback that the rung was actually switched" -----------------------------

@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestTheRealStatus(unittest.TestCase):
    def _st(self, **kw):
        s = {"mode": "on", "rung": 2, "bar": 0.34, "next": 3, "waiting_on": "looks",
             "ladder": [{"rung": n, "name": "x", "state": "passed"} for n in range(3)]}
        s.update(kw)
        return {"self": s}

    def test_it_says_where_self_is(self):
        self.assertEqual(_view(self._st())["status"], "rung 2 ✓ · 34% → 3 (looks)")

    def test_it_says_when_an_accept_holds_it(self):          # #299: the limit is gone; the operator's accept is what waits
        v = _view(self._st(next=[], waiting_on="your accept", awaiting=["kinds"]))
        self.assertIn("your accept", v["status"])
        self.assertIn("awaiting your accept: kinds", v["title"])

    def test_asleep_and_held(self):
        self.assertEqual(_view(self._st(mode="off"))["status"], "🌱 asleep")
        self.assertIn("held", _view(self._st(held=True))["status"])


class TestTheFeedback(unittest.TestCase):
    def test_both_pages_have_a_live_status_line(self):
        for f in ("panel.js", "home.js"):
            self.assertIn('class="selfstat"', (UI / f).read_text())

    def test_a_switch_is_confirmed_by_the_seats_answer(self):
        js = (UI / "panel.js").read_text()
        self.assertIn('out = { ok: true, on: got.on, text: got.on ? "✓ " + SELF_NAME + " is awake"', js)   # from the response, not the click
        self.assertIn("this._selfSleep.disabled = true;           // in flight", js)
        home = (UI / "home.js").read_text()
        self.assertIn('this._statusEl.textContent = "🌱 " + text;', home)

    def test_the_options_refresh_when_a_rung_opens(self):
        js = (UI / "panel.js").read_text()
        self.assertIn("if (moved) this._loadSeat()", js)
