"""#395: the person's pause is carried across a requested restart.

The re-exec (presence.restart, #151/#243) hands the pause to the new process
through the environment -- never through disk. Pinned here:
  - a re-exec while the person paused the seat sets the carry marker, and the
    new boot starts paused (and says so in the audit, by "restart");
  - a re-exec while running, or while only the BATTERY paused it, carries
    nothing: the battery's pause re-derives itself from the battery;
  - a cold start (no re-exec marker) starts unpaused even with a stray marker;
  - the marker is consumed at boot, so no child process inherits it;
  - the battery never lifts a pause it did not make (a carried one included);
  - the restart reply, the button's tooltip and the docs say the pause is kept.
"""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import lanes as L  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane.entities import MEMBRANE_NS, EntityStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _srv(d):
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(d) / "audit.jsonl"))
    return srv


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.pop(k, None) for k in (RS.REEXEC_ENV, RS.PAUSE_ENV, "MEMBRANE_NERVE_PAUSE")}
        RS._BOOT.clear()
        self.execs = []
        self._p = mock.patch.object(RS.os, "execv", lambda exe, argv: self.execs.append(
            {k: os.environ.get(k) for k in (RS.REEXEC_ENV, RS.PAUSE_ENV)}))
        self._p.start()
        RS.RESTART["requested"] = True

    def tearDown(self):
        self._p.stop()
        RS.RESTART["requested"] = False
        RS._BOOT.clear()
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    def _boot(self):
        """The new process's boot: a fresh seat, the carry applied."""
        new = _srv(self.tmp.name)
        RS.apply_carried_pause(new)
        return new


class TestTheCarry(_Env):
    def test_the_persons_pause_survives_a_reexec(self):
        old = _srv(self.tmp.name)
        L.nerve_set(old, True, by="operator")
        RS.reexec_if_requested(old)
        self.assertEqual(self.execs[-1], {RS.REEXEC_ENV: "1", RS.PAUSE_ENV: "1"})
        new = self._boot()
        self.assertTrue(new.paused)
        self.assertTrue(L.nerve_paused(new))
        self.assertTrue(L.person_paused(new))                # still the person's: carried again next time
        audit = [json.loads(x) for x in Path(new.membrane.audit_path).read_text().splitlines()]
        self.assertEqual(audit[-1]["op"], "pause")
        self.assertEqual(audit[-1]["by"], "restart")

    def test_the_marker_is_consumed_at_boot(self):
        old = _srv(self.tmp.name)
        L.nerve_set(old, True)
        RS.reexec_if_requested(old)
        self._boot()
        self.assertNotIn(RS.PAUSE_ENV, os.environ)           # no child tool process inherits it
        self.assertTrue(RS.was_reexec())                      # the #243 marker is untouched by the carry
        self.assertNotIn(RS.REEXEC_ENV, os.environ)

    def test_carry_after_was_reexec_still_reads(self):
        old = _srv(self.tmp.name)
        L.nerve_set(old, True)
        RS.reexec_if_requested(old)
        self.assertTrue(RS.was_reexec())                      # the browser check read first
        self.assertTrue(self._boot().paused)

    def test_a_running_seat_comes_back_running(self):
        old = _srv(self.tmp.name)
        RS.reexec_if_requested(old)
        self.assertIsNone(self.execs[-1][RS.PAUSE_ENV])
        self.assertFalse(self._boot().paused)

    def test_a_resumed_seat_comes_back_running(self):
        old = _srv(self.tmp.name)
        L.nerve_set(old, True)
        L.nerve_set(old, False)
        RS.reexec_if_requested(old)
        self.assertIsNone(self.execs[-1][RS.PAUSE_ENV])
        self.assertFalse(self._boot().paused)

    def test_the_battery_pause_is_not_carried(self):
        old = _srv(self.tmp.name)
        SP._battery_guard(old, {"percentage": 3, "status": "DISCHARGING"}, {})
        self.assertTrue(old.paused)                           # the battery paused it
        self.assertFalse(L.person_paused(old))
        RS.reexec_if_requested(old)
        self.assertIsNone(self.execs[-1][RS.PAUSE_ENV])
        new = self._boot()
        self.assertFalse(new.paused)                          # ... and re-derives on the new boot:
        SP._battery_guard(new, {"percentage": 3, "status": "DISCHARGING"}, {})
        self.assertTrue(new.paused)

    def test_the_person_pausing_over_the_battery_owns_the_pause(self):
        old = _srv(self.tmp.name)
        SP._battery_guard(old, {"percentage": 3, "status": "DISCHARGING"}, {})
        L.nerve_set(old, True, by="operator")
        self.assertTrue(L.person_paused(old))

    def test_a_cold_start_is_unpaused_even_with_a_stray_marker(self):
        os.environ[RS.PAUSE_ENV] = "1"                        # no MEMBRANE_REEXEC: not a re-exec
        self.assertFalse(self._boot().paused)
        self.assertNotIn(RS.PAUSE_ENV, os.environ)

    def test_no_seat_given_carries_nothing(self):
        RS.reexec_if_requested()
        self.assertIsNone(self.execs[-1][RS.PAUSE_ENV])

    def test_nothing_about_the_pause_is_written_to_disk(self):
        old = _srv(self.tmp.name)
        L.nerve_set(old, True)
        before = {p.name: p.read_text() for p in Path(self.tmp.name).iterdir()}
        RS.reexec_if_requested(old)
        after = {p.name: p.read_text() for p in Path(self.tmp.name).iterdir()}
        self.assertEqual(before, after)


class TestTheBatteryKeepsItsHands_Off(unittest.TestCase):
    def test_a_stale_battery_flag_never_lifts_the_persons_pause(self):
        srv = _srv(tempfile.mkdtemp())
        L.nerve_set(srv, True, by="restart")                  # the carried pause
        pp = {"_bat_paused": True}                            # a stale flag from the old process's entity
        SP._battery_guard(srv, {"percentage": 90, "status": "CHARGING"}, pp)
        self.assertTrue(srv.paused)
        self.assertFalse(pp["_bat_paused"])                   # the stale flag is cleared


class TestTheWords(unittest.TestCase):
    def test_the_restart_reply_says_the_pause_is_kept(self):
        srv = _srv(tempfile.mkdtemp())
        srv.data_dir = tempfile.mkdtemp()
        L.nerve_set(srv, True)
        with mock.patch.object(RS.threading, "Thread"), mock.patch("membrane.seat_panel.note"), \
                mock.patch.object(RS, "_dir", return_value=srv.data_dir):
            out = RS.do_restart(srv, "new code", delay_s=0)
        RS.RESTART["requested"] = False
        self.assertIn("comes back paused", out["text"])
        self.assertNotIn("unpaused", out["text"])

    def test_the_tooltip_and_docs_say_a_restart_keeps_it(self):
        js = (ROOT / "membrane/ui/home.js").read_text()
        self.assertNotIn("Not remembered: a restart resumes", js)
        self.assertNotIn("A restart resumes it too", js)
        self.assertIn("A restart keeps it", js)
        doc = (ROOT / "docs/background-mind.md").read_text()
        self.assertNotIn("a restart unpauses", doc)
        self.assertNotIn("RAM-only", L.nerve_paused.__doc__)

    def test_the_seat_boot_applies_the_carry_and_the_teardown_hands_it_on(self):
        src = (ROOT / "membrane/opencode.py").read_text()
        self.assertIn("apply_carried_pause(srv)", src)
        self.assertIn("reexec_if_requested(srv)", src)
        self.assertLess(src.index("apply_carried_pause(srv)"), src.index("srv.loops = None"))


if __name__ == "__main__":
    unittest.main()
