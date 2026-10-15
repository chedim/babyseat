"""THE FOREWARNING (2026-10-15, stage 3a -- observation only).

The gate (experiments/delta_lane, rung 14, a seat's real field): when the seat
says what it is about to do and a tool result follows, recalling the 10 most
similar past moments (what it said + the previous result, OTHER sessions) and
the outcomes that followed them predicts the result's outcome BEFORE it exists:
AUC 0.650 (intent alone 0.616, previous result 0.595, random 0.523); flagged on
12% of steps, 44% of those failed vs 23% overall. (Surprise is NOT failure here:
failures are the most expected results -- they repeat.)

Stage 3a only RECORDS: the forecast rides the observation event (like surprise),
the calibration counts ride the origin stats; nothing reaches the model.

The fake embedder is bag-of-words: 'restart the proxy' moments followed by
'proxy unreachable' errors in some sessions, 'read the config' moments followed
by fine results in others."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import forewarn as FW  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

FAIL = ("restart the proxy and retry the run", "proxyerror membrane proxy unreachable timed out", "error")
FINE = ("read the config file for the ports", "config loaded fine with all ports listed", "ok")


def _field(**kw):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")
    fw = FW.Forewarn(f, min_pool=2, k=3, **kw)
    f.step_listeners.append(fw.listener)
    fw.ready()                                   # empty history: the pool is ready at once
    return f, fw


def _moment(f, session, t0, intent, result, outcome, warm="listed the files in the folder"):
    """previous result -> the seat says -> the tool result."""
    # real tool results carry their call id (the field keys observations by it;
    # without one, identical results in different sessions collapse into one event)
    evs = [ST.Event(stream="observation", tool="shell", args={"command": "ls"},
                    result_head=warm, outcome="ok", ts=t0, call_id=f"{session}-{t0}-a"),
           ST.Event(stream="discourse", role="model", text=intent, ts=t0 + 1),
           ST.Event(stream="observation", tool="shell", args={"command": "run"},
                    result_head=result, outcome=outcome, ts=t0 + 2, call_id=f"{session}-{t0}-b")]
    keys = f.write_events(evs, session=session)
    f.process()
    return keys[-1]


def _history(f):
    t = 1000.0
    for i in range(4):
        _moment(f, f"fail{i}", t, *FAIL); t += 10
        _moment(f, f"fine{i}", t, *FINE); t += 10
    return t


class TestTheForecast(unittest.TestCase):
    def test_a_moment_like_past_failures_is_forewarned(self):
        f, fw = _field()
        t = _history(f)
        k = _moment(f, "now", t, *FAIL)
        fc = f.b.get("events", k)["forecast"]
        self.assertTrue(fc["flag"])
        self.assertGreaterEqual(fc["bad"], 0.5)
        self.assertGreater(fc["lift"], 0)
        self.assertIn("unreachable", fc["like"][0]["text"])

    def test_a_moment_like_past_successes_is_not_flagged(self):
        f, fw = _field()
        t = _history(f)
        k = _moment(f, "now", t, *FINE)
        fc = f.b.get("events", k)["forecast"]
        self.assertFalse(fc["flag"])
        self.assertLess(fc["lift"], 0)

    def test_only_other_sessions_and_the_past_speak(self):
        """The first failing session has no other-session past: no forecast."""
        f, fw = _field()
        k = _moment(f, "only", 1000.0, *FAIL)
        self.assertNotIn("forecast", f.b.get("events", k))

    def test_a_result_after_a_result_is_not_a_forecast_moment(self):
        f, fw = _field()
        t = _history(f)
        evs = [ST.Event(stream="observation", tool="shell", args={"command": "ls"},
                        result_head="listed the files in the folder", outcome="ok", ts=t,
                        call_id="now-a"),
               ST.Event(stream="observation", tool="shell", args={"command": "run"},
                        result_head=FAIL[1], outcome="error", ts=t + 1, call_id="now-b")]
        k = f.write_events(evs, session="now")[-1]
        f.process()
        self.assertNotIn("forecast", f.b.get("events", k))


class TestTheRepeatsCount(unittest.TestCase):
    def test_a_result_seen_past_the_boilerplate_bar_still_reaches_it(self):
        """The field's boilerplate gate (a sentence seen > BOILER_MIN times moves
        nothing) skips the ordinary listeners -- the 9th 'proxy unreachable' is
        exactly what the forewarning must see: it rides the step listeners."""
        f, fw = _field()
        t = 1000.0
        for i in range(EF.BOILER_MIN + 2):
            _moment(f, f"fail{i}", t, *FAIL)
            t += 10
        k = _moment(f, "now", t, *FAIL)
        self.assertIn("forecast", f.b.get("events", k))


class TestTheCalibration(unittest.TestCase):
    def test_every_forecast_is_counted_against_what_happened(self):
        f, fw = _field()
        t = _history(f)
        _moment(f, "now1", t, *FAIL)
        _moment(f, "now2", t + 10, *FINE)
        cal = f._ostats["forewarn"]
        self.assertGreaterEqual(cal["n"], 2)
        self.assertGreaterEqual(cal["flagged"], 1)
        self.assertGreaterEqual(cal["flagged_bad"], 1)
        self.assertLessEqual(cal["flagged_bad"], cal["flagged"])


class TestTheHistory(unittest.TestCase):
    def test_a_new_seat_rebuilds_its_pool_from_the_field(self):
        f, fw = _field()
        t = _history(f)
        f.step_listeners.remove(fw.listener)
        fw2 = FW.Forewarn(f, min_pool=2, k=3)
        f.step_listeners.append(fw2.listener)
        fw2.bootstrap()                          # the boot's background pass, run inline
        k = _moment(f, "now", t, *FAIL)
        self.assertTrue(f.b.get("events", k)["forecast"]["flag"])

    def test_a_pooled_moment_is_never_forecast_again(self):
        """A rebuild replays events the pool already holds -- their forecasts
        would read the future; they are skipped."""
        f, fw = _field()
        _history(f)
        n = f._ostats["forewarn"]["n"]
        f.rebuild()
        self.assertEqual(f._ostats["forewarn"]["n"], n)

    def test_the_kill_switch_records_nothing(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_FOREWARN": "0"}):
            f, fw = _field()
            t = _history(f)
            k = _moment(f, "now", t, *FAIL)
        self.assertNotIn("forecast", f.b.get("events", k))
        self.assertFalse(f._ostats.get("forewarn"))


if __name__ == "__main__":
    unittest.main()
