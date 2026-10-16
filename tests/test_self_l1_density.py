"""#161 RUNG 3 IN THE LAB: how much motor channel does a body need?

Rung 2 measured a live store and found no motor channel to speak of (53
script-authored events in ten hours, 163 pulses). That says the seat's body is
not in this data; it does not say how much motion a seat would have to record
before the ledger could read a body in it. This file pins the instrument's
sensitivity: a REAL act stream with a PLANTED answer -- a power analysis, never
a claim that the live seat has a body.

The planted answer is machinery (a script-authored event owning a pulse), so it
must survive the replay's own rule that neither the act's footprint nor the
seat's narration counts as an answer.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "self_l1"))

import density as D  # noqa: E402
import replay as R  # noqa: E402


def _acts_only(n=40, kind="entity_read", dt=10.0, t0=1000.0):
    """An act stream like the seat's own: tool calls, each pulsing only its own
    footprint, so no change survives the replay's filter (no channel at all)."""
    ev, dl = [], []
    for i in range(n):
        t = t0 + dt * i
        ev.append((f"v:a{i}", {"stream": "observation", "role": "tool",
                               "tool": kind, "ts": t, "src": "live"}))
        dl.append({"eid": f"tool:{kind}", "event": f"v:a{i}", "ts": t})
    return ev, dl


class TestThePlantedChannelIsMachinery(unittest.TestCase):
    def test_planting_does_not_touch_the_history_it_was_given(self):
        ev, dl = _acts_only()
        kev, kdl = len(ev), len(dl)
        D.plant_motor(ev, dl, q=0.5)
        self.assertEqual((len(ev), len(dl)), (kev, kdl),
                         "the planted channel rides a copy, not the history")

    def test_only_a_fraction_of_the_acts_are_answered(self):
        ev, dl = D.plant_motor(*_acts_only(), q=1.0)
        ch = R.pulses(ev, dl, footprint=R.act_keys(ev, R.is_agent_act))
        self.assertEqual(len(ch), 40)
        self.assertEqual({e for _, e in ch}, {D.PLANT_EID})

    def test_the_planted_answer_is_not_the_seats_voice(self):
        # it must survive the rule that wrote rung 2 -- owned by an event that
        # is neither the act itself nor the seat's narration
        ev0, dl0 = _acts_only()
        ev, dl = D.plant_motor(ev0, dl0, q=1.0)
        planted = dict(ev)["v:motor:0"]
        self.assertFalse(R.is_narration(planted))
        self.assertTrue(R.is_machine_act(planted))
        own = dict(R.authors(ev, dl))
        self.assertEqual(own.get("the seat's machinery"), 40)

    def test_the_answers_land_inside_the_window(self):
        _ev, dl = D.plant_motor(*_acts_only(), q=1.0, delay=0.3)
        d = {x["event"]: x["ts"] for x in dl if x["eid"] == D.PLANT_EID}
        self.assertAlmostEqual(d["v:motor:0"] - 1000.0, 0.3)


class TestWhatDensityTheLedgerCanSee(unittest.TestCase):
    def setUp(self):
        self.ev, self.dl = D.plant_motor(*_acts_only(), q=1.0)

    def test_an_answer_to_every_act_is_a_body(self):
        row = D.detectability(self.ev, self.dl, densities=(1.0,))[0]
        self.assertEqual(row["body"], [D.PLANT_EID])
        self.assertGreaterEqual(row["p_act"], 0.8)
        self.assertLessEqual(row["lat_mad"], 0.75)
        self.assertEqual(row["null_body"], [])

    def test_an_occasional_answer_is_not_a_body(self):
        ev, dl = D.plant_motor(*_acts_only(), q=0.05)
        row = D.detectability(ev, dl, densities=(0.05,))[0]
        self.assertEqual(row["body"], [])
        self.assertNotIn(D.PLANT_EID, row["other"])

    def test_the_threshold_shows_up_as_other_before_it_shows_up_as_body(self):
        # half the acts answered: the ledger sees something that responds, but
        # unreliably -- an "other", not a body part
        ev, dl = D.plant_motor(*_acts_only(), q=0.5)
        row = D.detectability(ev, dl, densities=(0.5,))[0]
        self.assertEqual(row["body"], [])
        self.assertIn(D.PLANT_EID, row["other"])

    def test_the_shuffled_null_never_finds_the_planted_body(self):
        rows = D.detectability(self.ev, self.dl, densities=(0.1, 0.5, 0.95),
                               shuffle_seed=1)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["null_body"], [],
                             f"a shuffled history found a body at q={row['q']}")

    def test_a_history_with_no_channel_at_all_finds_nothing(self):
        row = D.detectability(*_acts_only(), densities=(0.0,))[0]
        self.assertEqual((row["motor_events"], row["changes"]), (0, 0))
        self.assertEqual((row["body"], row["null_body"]), ([], []))


class TestWhichActKindsAreEvenEligible(unittest.TestCase):
    def test_only_act_kinds_the_seat_repeats_are_eligible(self):
        # the real log's 48 act kinds, most of them a handful of calls: a motor
        # channel for a kind the seat performs five times can never be read
        ev, dl = _acts_only(n=40, kind="entity_read")
        ev2, dl2 = _acts_only(n=5, kind="entity_edit", t0=2000.0)
        ev, dl = ev + ev2, dl + dl2
        self.assertEqual(D.eligible_kinds(ev), [("entity_read", 40)])


class TestTheCurveIsTheInjectionAlone(unittest.TestCase):
    """The planted entity must be one the live store never moves: otherwise the
    curve reads the seat's own contingency (rung 3 first ran with
    `m:component:field:vitals`, which the job path already moves, so q=0.00
    came back as an "other" -- the control was measuring the seat)."""

    def test_the_history_untouched_has_no_contingency_for_the_planted_entity(self):
        row = D.detectability(*_acts_only(), densities=(0.0,))[0]
        self.assertIsNone(row["p_act"])
        self.assertEqual((row["body"], row["other"]), ([], []))

    def test_the_measured_rate_is_the_planted_rate(self):
        row = D.detectability(*D.plant_motor(*_acts_only(), q=0.5),
                              densities=(0.5,))[0]
        self.assertAlmostEqual(row["p_act"], 0.5, delta=0.15)


class TestTheLiveChannelIsReadAsAnOther(unittest.TestCase):
    """The one thing the seat's machinery already moves: a job lands and the
    component that ordered it moves (rung 2's `consequence` path). It answers a
    third of the acts -- visible to the ledger, but an "other", not a body."""

    def _job_path(self, n=60, every=3):
        ev, dl = _acts_only(n=n)
        for i in range(0, n, every):
            t = 1000.0 + 10 * i + 2.0
            ev.append((f"v:job{i}", {"stream": "observation", "role": "",
                                     "tool": "consequence", "ts": t, "src": "live"}))
            dl.append({"eid": D.LIVE_EID, "event": f"v:job{i}", "ts": t})
        return ev, sorted(dl, key=lambda d: d["ts"])

    def test_a_job_path_that_answers_a_third_of_the_acts_is_not_a_body(self):
        ev, dl = self._job_path()
        row = D.live_row(ev, dl, window=5.0)
        self.assertEqual(row["cls"], "other")
        self.assertAlmostEqual(row["p_act"], 1 / 3, delta=0.05)
        self.assertEqual(row["machinery_pulses"], 20)

    def test_the_busiest_entity_of_the_machinery_channel_is_named(self):
        ev, dl = self._job_path()
        self.assertEqual(D.busiest_machinery(ev, dl)[0], (D.LIVE_EID, 20))


class TestTheRowIsAboutThePlantedEntity(unittest.TestCase):
    """The curve's body/other columns must be the PLANTED entity's class, not
    the whole map's: the live history has others of its own (rung 2's `tool:shell`,
    `c:head`), and reading those as the planted answer made the q=0.00 control
    row claim an answer when nothing had been planted."""

    def _live_other_plus_no_channel(self):
        """A history with a real "other" of its own (machinery-owned pulses on
        `tool:shell` after a third of the acts) and NO planted channel."""
        ev, dl = _acts_only(n=60)
        for i in range(0, 60, 3):
            t = 1000.0 + 10 * i + 2.0
            ev.append((f"v:x{i}", {"stream": "observation", "role": "",
                                   "tool": "consequence", "ts": t, "src": "live"}))
            dl.append({"eid": "tool:shell", "event": f"v:x{i}", "ts": t})
        return ev, sorted(dl, key=lambda d: d["ts"])

    def test_the_control_row_does_not_borrow_the_live_maps_others(self):
        row = D.detectability(*self._live_other_plus_no_channel(), densities=(0.0,))[0]
        self.assertFalse(row["planted_body"])
        self.assertFalse(row["planted_other"])
        self.assertIn("tool:shell", row["other"])      # the map still has it
        self.assertIsNone(row["p_act"])

    def test_the_planted_entity_is_the_one_flagged(self):
        row = D.detectability(*D.plant_motor(*_acts_only(), q=0.5),
                              densities=(0.5,))[0]
        self.assertTrue(row["planted_other"])
        self.assertFalse(row["planted_body"])


class TestTheBaselineIsPrintedHonestly(unittest.TestCase):
    def test_a_zero_baseline_is_a_zero_and_a_floored_one_is_not(self):
        # the planted channel moves only inside act windows: p_base is exactly
        # 0 there, and the ledger's own floor is what makes z finite
        self.assertEqual(D._p(0.0), "0")
        self.assertEqual(D._p(None), "--")
        self.assertEqual(D._p(2.75e-5), "2.8e-05")

    def test_the_curve_prints_the_zero_row_without_pretending(self):
        row = D.detectability(*_acts_only(n=60), densities=(0.0,))[0]
        self.assertEqual((row["planted_body"], row["planted_other"]), (False, False))
        self.assertIsNone(row["p_act"])


class TestTheWriteUpIsHonest(unittest.TestCase):
    def test_each_window_gets_its_own_curve(self):
        # rung 2 read its one body call at 20 s, so a 5 s-only curve would not
        # be comparable with it: both windows are reported
        ev, dl = D.plant_motor(*_acts_only(n=60), q=1.0)
        txt = D.report((ev, dl), densities=(0.5, 0.95), windows=(5.0, 20.0))
        self.assertIn("window 5s", txt)
        self.assertIn("window 20s", txt)

    def test_the_report_says_it_is_a_power_analysis(self):
        ev, dl = D.plant_motor(*_acts_only(), q=1.0)
        txt = D.report((ev, dl), densities=(0.1, 0.95))
        self.assertIn("power analysis", txt.lower())
        self.assertNotIn("the seat's body is", txt.lower())


if __name__ == "__main__":
    unittest.main()
