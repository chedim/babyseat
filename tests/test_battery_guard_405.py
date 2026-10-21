"""#405: the battery guard's own pause, and the person's word over it.

Decided 2026-10-21:
  - the battery's OWN pause lifts only when the battery is ABOVE
    BATTERY_RESUME (20%) AND still charging -- plugging in is not enough;
  - a person's resume while the guard holds the seat (or while the battery
    is low) disarms the guard: it does not pause the seat again until the
    machine is unplugged again (a charging -> not-charging transition),
    which re-arms it. RAM-only;
  - the battery never lifts a person's pause (#395).
Fake readings drive seat_panel._battery_guard directly.
"""
import os
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import lanes as L  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane.entities import MEMBRANE_NS, EntityStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT, IN = "DISCHARGING", "CHARGING"


def _srv():
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(tempfile.mkdtemp()) / "audit.jsonl"))
    return srv


class _Guard(unittest.TestCase):
    def setUp(self):
        self.srv, self.pp = _srv(), {}

    def read(self, pct, status):
        SP._battery_guard(self.srv, {"percentage": pct, "status": status}, self.pp)
        return self.srv.paused

    def battery_events(self):
        doc = self.srv.entity_store.docs.get(MEMBRANE_NS + "presence")
        evs = (doc.properties or {}).get("events", []) if doc else []
        return [e["kind"] for e in evs if e.get("by") == "battery"]


class TestTheMark(_Guard):
    def test_the_mark_is_a_named_constant_at_twenty(self):
        self.assertEqual(SP.BATTERY_RESUME, 20)

    def test_critical_and_unplugged_pauses(self):
        self.assertTrue(self.read(5, OUT))
        self.assertEqual(self.srv.pause_by, "battery")

    def test_critical_but_charging_does_not_pause(self):
        self.assertFalse(self.read(5, IN))

    def test_plugging_in_at_fifteen_stays_paused(self):
        self.read(5, OUT)
        self.assertTrue(self.read(15, IN))
        self.assertTrue(self.read(20, IN))                    # at the mark is not above it

    def test_charging_above_the_mark_lifts(self):
        self.read(5, OUT)
        self.read(15, IN)
        self.assertFalse(self.read(21, IN))
        self.assertIsNone(self.srv.pause_by)
        self.assertEqual(self.battery_events(), ["paused", "resumed"])

    def test_above_the_mark_but_unplugged_does_not_lift(self):
        self.read(5, OUT)
        self.read(15, IN)
        self.assertTrue(self.read(25, OUT))                   # unplugged before the lift: still held
        self.assertEqual(self.battery_events(), ["paused"])   # and not paused twice

    def test_unplugging_after_the_lift_does_not_pause_again(self):
        self.read(5, OUT)
        self.read(25, IN)
        self.assertFalse(self.read(25, OUT))                  # above critical: running stays running
        self.assertFalse(self.read(10, OUT))
        self.assertTrue(self.read(7, OUT))                    # critical again: the armed guard pauses
        self.assertEqual(self.battery_events(), ["paused", "resumed", "paused"])


class TestThePersonsResume(_Guard):
    def test_a_resume_while_the_guard_holds_is_not_undone(self):
        self.read(5, OUT)
        SP.set_pause(self.srv, False, by="operator")          # the person resumes over the battery
        self.assertFalse(self.srv.paused)
        self.assertFalse(self.read(4, OUT))                   # still critical, still unplugged: no re-pause
        self.assertFalse(self.read(3, OUT))
        self.assertFalse(self.read(4, IN))                    # plugging in does not re-arm
        self.assertFalse(self.read(5, IN))

    def test_a_typed_resume_disarms_too(self):
        self.read(5, OUT)
        L.nerve_set(self.srv, False)
        self.assertFalse(self.read(4, OUT))

    def test_a_resume_while_charging_but_held_disarms(self):
        self.read(5, OUT)
        self.read(12, IN)                                     # held: plugged in, below the mark
        SP.set_pause(self.srv, False)
        self.assertFalse(self.read(12, IN))
        self.assertFalse(self.read(12, IN))

    def test_a_resume_while_low_disarms(self):
        self.read(15, OUT)                                    # low, not yet critical
        SP.set_pause(self.srv, True)                          # the person pauses...
        SP.set_pause(self.srv, False)                         # ...and resumes, knowing it is low
        self.assertFalse(self.read(5, OUT))                   # the guard stands down

    def test_the_unplug_rearms(self):
        self.read(5, OUT)
        SP.set_pause(self.srv, False)
        self.assertFalse(self.read(4, OUT))
        self.read(9, IN)                                      # plugged in
        self.assertFalse(self.read(9, OUT))                   # unplugged above critical: re-armed, but...
        self.assertEqual(self.srv.pause_by, None)
        self.assertTrue(self.read(5, OUT))                    # ...critical and unplugged pauses again
        self.assertEqual(self.srv.pause_by, "battery")

    def test_the_unplug_rearms_even_when_already_critical(self):
        self.read(5, OUT)
        SP.set_pause(self.srv, False)
        self.read(5, IN)
        self.assertTrue(self.read(5, OUT))                    # the unplug itself is the fresh transition

    def test_a_resume_with_a_healthy_battery_keeps_the_guard_armed(self):
        self.read(80, OUT)
        SP.set_pause(self.srv, True)
        SP.set_pause(self.srv, False)
        self.assertTrue(self.read(5, OUT))                    # nothing about the battery was overridden

    def test_a_resume_before_any_reading_keeps_the_guard_armed(self):
        SP.set_pause(self.srv, True)
        SP.set_pause(self.srv, False)
        self.assertTrue(self.read(5, OUT))


class TestThePersonsPause(_Guard):
    def test_the_battery_never_lifts_the_persons_pause(self):
        SP.set_pause(self.srv, True)
        self.read(5, OUT)
        self.assertEqual(self.srv.pause_by, "person")         # the guard does not take it over
        self.assertTrue(self.read(90, IN))
        self.assertTrue(self.read(100, "FULL"))

    def test_the_person_pausing_over_the_battery_is_not_lifted(self):
        self.read(5, OUT)
        SP.set_pause(self.srv, True)                          # the person takes the pause
        self.assertTrue(self.read(50, IN))
        self.assertEqual(self.srv.pause_by, "person")


class TestTheWords(unittest.TestCase):
    def test_the_docs_say_it(self):
        doc = (ROOT / "docs" / "background-mind.md").read_text()
        self.assertIn("above 20%", doc)
        self.assertIn("unplug", doc)
        self.assertIn("#405", doc)


if __name__ == "__main__":
    unittest.main()
