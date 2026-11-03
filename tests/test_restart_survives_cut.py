"""A RESTART CUT BY THE TEARDOWN LEASE STILL COMES BACK (2026-11-03: 150 queued entity saves held
the drain past the 90 s lease; it cut the process before the re-exec, and the seat stayed down
until someone started it by hand). On the cut, a requested restart starts the same command as a
fresh, detached process, then exits; a plain stop still just stops."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import restarts as RS  # noqa: E402


class RelaunchAfterCut(unittest.TestCase):
    def setUp(self):
        self._saved = dict(RS.RESTART)
        self._env = dict(os.environ)

    def tearDown(self):
        RS.RESTART.clear(); RS.RESTART.update(self._saved)
        os.environ.clear(); os.environ.update(self._env)

    def test_a_plain_stop_starts_nothing(self):
        RS.RESTART["requested"] = False
        calls = []
        self.assertFalse(RS.relaunch_after_cut(None, popen=lambda *a, **k: calls.append(a)))
        self.assertEqual(calls, [])

    def test_a_requested_restart_starts_the_same_command_detached(self):
        RS.RESTART["requested"] = True
        RS.RESTART["ports"] = (11500, 4097)
        calls = []
        self.assertTrue(RS.relaunch_after_cut(None, popen=lambda argv, **k: calls.append((argv, k))))
        [(argv, kw)] = calls
        self.assertEqual(argv[:3], [sys.executable, "-m", "membrane.opencode"])
        self.assertIn("--listen", argv)
        self.assertIn("--serve-port", argv)
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["env"].get(RS.REEXEC_ENV), "1")       # no new browser tab

    def test_the_lease_cut_calls_it(self):
        import inspect
        from membrane import opencode as OC
        src = inspect.getsource(OC)
        cut = src[src.index("def _lease_cut"):src.index("_teardown_halt = threading.Timer")]
        self.assertIn("relaunch_after_cut", cut)
        self.assertLess(cut.index("relaunch_after_cut"), cut.index("os._exit"))


if __name__ == "__main__":
    unittest.main()
