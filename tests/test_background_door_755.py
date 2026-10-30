"""#755: the dreamer's cycle and capture's step read the field as background
(#541 door), so a person's turn reads first instead of queueing behind them
on the database's one lock."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.cblite import TurnFirst  # noqa: E402
from membrane.capture import Capturer  # noqa: E402
from membrane.dreamer import Dreamer  # noqa: E402


class _Field:
    def write_events(self, evs, **kw):
        return []

    def process(self):
        pass


def _parks_behind_turn(work):
    """Run `work` (which does one store read via lane()) on its own thread
    while a foreground lane() read is in flight: True when it waited."""
    g = TurnFirst()
    started, release, order = threading.Event(), threading.Event(), []

    def fg():
        with g.lane():
            started.set()
            release.wait(5)
            order.append("fg")

    t1 = threading.Thread(target=fg); t1.start()
    started.wait(5)
    t2 = threading.Thread(target=lambda: work(g, order)); t2.start()
    time.sleep(0.2)
    parked = order == []
    release.set()
    t1.join(5); t2.join(5)
    return parked and order == ["fg", "bg"]


class DreamerCycleIsBackground(unittest.TestCase):
    def test_cycle_read_parks_at_the_door(self):
        def work(g, order):
            d = Dreamer(srv=object(), idle=0.01)

            def cycle():
                with g.lane():
                    order.append("bg")
                d._stop.set()
                return 0.0
            d._cycle = cycle
            d._run()
        self.assertTrue(_parks_behind_turn(work))


class CaptureStepIsBackground(unittest.TestCase):
    def test_step_read_parks_at_the_door(self):
        def work(g, order):
            done = threading.Event()

            class C(Capturer):
                def _step(self, build, session, conv=""):
                    with g.lane():
                        order.append("bg")
                    done.set()
            c = C(_Field(), mode="on", start=True)
            c.q.put((None, "s", ""))
            done.wait(5)
        self.assertTrue(_parks_behind_turn(work))


if __name__ == "__main__":
    unittest.main()
