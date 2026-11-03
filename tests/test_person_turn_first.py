"""A PERSON'S TURN STAGES FIRST (the operator, 2026-11-03: "memo is very slow again"; the gate
took 86-91 s while a planner and three workers staged their own turns in the same process).

#756 made the dreamer and capture read the field as background (cblite.TurnFirst). This extends
the same door to turns: a person's turn (a chat session's) holds a foreground lane while it
stages; every other turn -- a loop's, or the seat's own call with no session -- enters as
background and parks at the door until no person's turn is staging. It wakes on the event, not a
clock; an early return or an error still releases the lane.
"""
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lanes as L  # noqa: E402


def _srv(sessions):
    store = types.SimpleNamespace(sessions=sessions)
    return types.SimpleNamespace(_loop_runner=types.SimpleNamespace(sessions=store))


SESSIONS = {"ses_me": {"agent": "build"}, "ses_w": {"agent": "worker: membrane::task:t:k1"}}


class PersonTurnFirst(unittest.TestCase):
    def setUp(self):
        L._STAGING = L._TurnFirst()

    def test_who_is_a_person(self):
        srv = _srv(SESSIONS)
        self.assertTrue(L._is_person_turn(srv, {"_turn_sid": "ses_me"}))
        self.assertFalse(L._is_person_turn(srv, {"_turn_sid": "ses_w"}))
        self.assertFalse(L._is_person_turn(srv, {}))                 # the seat's own call: no session

    def test_a_loop_turn_waits_while_a_person_turn_stages(self):
        srv = _srv(SESSIONS)
        order, person_in = [], threading.Event()

        def person():
            with L.staging_lane(srv, {"_turn_sid": "ses_me"}):
                person_in.set()
                time.sleep(0.4)
                order.append("person staged")

        def loop():
            person_in.wait(2)
            with L.staging_lane(srv, {"_turn_sid": "ses_w"}):
                order.append("loop staged")
        a, b = threading.Thread(target=person), threading.Thread(target=loop)
        a.start(); b.start(); a.join(3); b.join(3)
        self.assertEqual(order, ["person staged", "loop staged"])

    def test_a_loop_turn_runs_at_once_with_no_person_staging(self):
        srv = _srv(SESSIONS)
        t = time.monotonic()
        with L.staging_lane(srv, {"_turn_sid": "ses_w"}):
            pass
        self.assertLess(time.monotonic() - t, 0.2)

    def test_two_people_never_wait_for_each_other(self):
        srv = _srv({**SESSIONS, "ses_you": {"agent": "build"}})
        with L.staging_lane(srv, {"_turn_sid": "ses_me"}):
            t = time.monotonic()
            with L.staging_lane(srv, {"_turn_sid": "ses_you"}):
                pass
            self.assertLess(time.monotonic() - t, 0.2)

    def test_an_error_releases_the_lane(self):
        srv = _srv(SESSIONS)
        with self.assertRaises(RuntimeError):
            with L.staging_lane(srv, {"_turn_sid": "ses_me"}):
                raise RuntimeError("boom")
        t = time.monotonic()
        with L.staging_lane(srv, {"_turn_sid": "ses_w"}):
            pass
        self.assertLess(time.monotonic() - t, 0.2)

    def test_v1_chat_stages_inside_the_lane(self):
        """The request path itself holds it, released in a finally."""
        import inspect
        cls = next(c for c in vars(L).values() if isinstance(c, type) and hasattr(c, "_v1_chat_staged"))
        self.assertIn("finally", inspect.getsource(cls._v1_chat))
        self.assertIn("staging_lane(", inspect.getsource(cls._v1_chat_staged))


if __name__ == "__main__":
    unittest.main()
