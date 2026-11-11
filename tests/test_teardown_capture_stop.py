"""THE TEARDOWN STOPS CAPTURE BEFORE IT CLOSES THE FIELD (2026-11-02: the main seat's teardown
hit the 90 s lease on every restart; the stack dump at the cut had the capture worker still
inserting into the field the teardown was flushing). Capturer.stop() writes what is queued
(bounded), takes no new work and ends its worker; the teardown calls it first, and times each
of its steps into the log so a slow one names itself.
"""
import os
import re
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane.capture import Capturer  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


def _cap():
    return Capturer(EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host"), mode="shadow")


class CaptureStops(unittest.TestCase):
    def test_stop_writes_what_is_queued_and_ends_the_worker(self):
        c = _cap()
        c.on_turn("alice: the words before the stop", "ok")
        c.stop(timeout=5)
        self.assertEqual(c.q.unfinished_tasks, 0)
        self.assertFalse(c._t.is_alive())

    def test_nothing_is_taken_after_stop(self):
        c = _cap()
        c.stop(timeout=5)
        c.on_turn("alice: too late", "ok")
        self.assertEqual(c.q.qsize(), 0)

    def test_stop_twice_is_harmless(self):
        c = _cap()
        c.stop(timeout=5)
        c.stop(timeout=5)


class TheTeardownOrder(unittest.TestCase):
    def _fin(self):
        return SRC[SRC.index("# THE TEARDOWN'S BOUND"):]

    def test_capture_stops_before_the_dreamer_and_the_field_flush(self):
        fin = self._fin()
        stop = re.search(r"_cap\.stop\(", fin)
        self.assertIsNotNone(stop, "the teardown never stops capture")
        self.assertLess(stop.start(), fin.index("srv.dreamer.stop()"))
        self.assertLess(stop.start(), fin.index("_fld.flush()"))

    def test_each_step_is_timed_into_the_log(self):
        # #1115: the per-step lines come from bootprof.Laps("teardown") ("[teardown] <step> Ns",
        # pinned in tests/test_boot_profile_1115.py)
        self.assertIn('Laps("teardown", echo=sys.__stderr__)', self._fin())


if __name__ == "__main__":
    unittest.main()
