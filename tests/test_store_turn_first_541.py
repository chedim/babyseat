"""#541: a person's turn must not queue behind boot/night bulk reads on the
store lock. The gate: background threads wait while any foreground read is
in flight or waiting; no pacing constant, they wake on the event."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.cblite import TurnFirst  # noqa: E402


class TurnFirstTest(unittest.TestCase):
    def test_background_waits_for_foreground_then_runs(self):
        g, order = TurnFirst(), []
        started = threading.Event()
        release = threading.Event()

        def fg():
            with g.lane():
                started.set()
                release.wait(5)
                order.append("fg")

        def bg():
            with g.background():
                with g.lane():
                    order.append("bg")

        t1 = threading.Thread(target=fg); t1.start()
        self.assertTrue(started.wait(5))
        t2 = threading.Thread(target=bg); t2.start()
        time.sleep(0.2)
        self.assertEqual(order, [])          # bg is parked behind the turn
        release.set()
        t1.join(5); t2.join(5)
        self.assertEqual(order, ["fg", "bg"])

    def test_foreground_never_waits_on_background_gate(self):
        g = TurnFirst()
        done = threading.Event()

        def bg():
            with g.background():
                with g.lane():
                    done.wait(5)             # a long bulk read in flight

        t = threading.Thread(target=bg); t.start()
        time.sleep(0.1)
        t0 = time.time()
        with g.lane():
            pass
        self.assertLess(time.time() - t0, 1.0)
        done.set(); t.join(5)

    def test_background_flag_is_per_thread(self):
        g = TurnFirst()
        with g.background():
            self.assertTrue(g.is_background())
            seen = []
            th = threading.Thread(target=lambda: seen.append(g.is_background()))
            th.start(); th.join()
            self.assertEqual(seen, [False])
        self.assertFalse(g.is_background())


if __name__ == "__main__":
    unittest.main()


class HoldingTest(unittest.TestCase):
    def test_background_holding_the_lock_is_not_parked(self):
        g = TurnFirst()
        started, release, ran = threading.Event(), threading.Event(), []

        def fg():
            with g.lane():
                started.set(); release.wait(5)

        t = threading.Thread(target=fg); t.start(); started.wait(5)
        with g.background():
            with g.lane(holding=True):
                ran.append(1)            # would deadlock if it parked
        release.set(); t.join(5)
        self.assertEqual(ran, [1])
