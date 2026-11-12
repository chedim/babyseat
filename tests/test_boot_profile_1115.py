"""THE BOOT'S PROFILE (#1115): a seat on a big field took ~4.5 min to answer and the log named only
two of its steps (the field's rows, the word index). Each boot step's wall time now lands in ONE
`[boot]` line, timed by the same lap timer the teardown's `[teardown] <step> Ns` lines use.
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.bootprof import Laps  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TheLaps(unittest.TestCase):
    def test_steps_time_themselves_into_one_line(self):
        c = _Clock()
        laps = Laps("boot", clock=c)
        laps.step("setup")
        c.t += 1.25
        laps.step("field rows")
        self.assertEqual(laps.now, "field rows")
        c.t += 58.0
        laps.step("serve")
        c.t += 0.5
        line = laps.done()
        self.assertEqual(line, "[boot] 59.8s: setup 1.2s, field rows 58.0s, serve 0.5s")
        self.assertIsNone(laps.now)

    def test_mark_is_the_teardowns_per_step_line(self):
        c = _Clock()
        out = io.StringIO()
        laps = Laps("teardown", clock=c, echo=out)
        c.t += 2.0
        laps.mark("listeners closed")
        c.t += 0.25
        laps.mark("capture stopped")
        self.assertEqual(out.getvalue(), "[teardown] listeners closed 2.0s\n[teardown] capture stopped 0.2s\n")

    def test_the_time_before_the_first_step_is_counted(self):
        c = _Clock()
        laps = Laps("boot", clock=c)
        c.t += 3.0
        laps.step("a")
        c.t += 1.0
        self.assertEqual(laps.done(), "[boot] 4.0s: start 3.0s, a 1.0s")


class TheCpuColumn(unittest.TestCase):
    """Memo's first profile: `entities 206.3s` -- wall time alone cannot say whether the step
    computes or waits (other threads holding the GIL, the disk). With a cpu clock (the boot thread's
    own CPU time) each lap shows both."""

    def test_each_lap_shows_the_boot_threads_cpu(self):
        c, cpu = _Clock(), _Clock()
        laps = Laps("boot", clock=c, cpu=cpu)
        laps.step("entities")
        c.t += 200.0
        cpu.t += 40.0
        self.assertEqual(laps.done(), "[boot] 200.0s: entities 200.0s (cpu 40.0s)")


class TheSeatUsesThem(unittest.TestCase):
    def test_the_boot_counts_its_threads_cpu_and_splits_the_entities_step(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        self.assertIn('Laps("boot", cpu=time.thread_time)', main)
        for step in ("entity seeds", "goal board", "tracks + scripts", "loop sessions", "serve wiring"):
            self.assertIn(f'_boot_laps.step("{step}")', main)

    def test_the_boot_prints_its_profile_before_the_signals(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        line = main.index("_boot_laps.done()")
        self.assertLess(line, main.index("signal.signal(signal.SIGTERM, _term)"))
        for step in ("field rows", "entities", "serve"):
            self.assertIn(f'_boot_laps.step("{step}")', main)

    def test_the_teardown_marks_with_the_same_timer(self):
        fin = SRC[SRC.index("# THE TEARDOWN'S BOUND"):]
        self.assertIn('Laps("teardown"', fin)
        self.assertIn('_td_mark("listeners closed")', fin)


if __name__ == "__main__":
    unittest.main()
