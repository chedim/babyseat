"""The web-viewer must never auto-launch (owner's 2026-10-02 call).

Default boot: zero browser spawns. Opt-in only: `--viewer` on the
launcher argv or MEMBRANE_VIEWER=on/1/true/yes. The manual open path
(GET /viewer) stays available. The launcher pins are behavioral --
we boot it with os.execv intercepted and capture the would-be argv.
"""
import importlib.util
import os
import unittest
from unittest import mock

LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, ".launcher.py")
OPENCODE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "membrane", "opencode.py")


class TestViewerGate(unittest.TestCase):
    def _boot(self, argv, env=None):
        env = dict(env or {})
        if not os.path.exists(LAUNCHER):
            self.skipTest("no local .launcher.py (gitignored operator launcher)")
        spec = importlib.util.spec_from_file_location(
            "launcher_under_test", LAUNCHER)
        mod = importlib.util.module_from_spec(spec)
        captured = {}

        def fake_execv(interp, av):
            captured["argv"] = list(av)
            raise SystemExit("execv-guard")

        with mock.patch("os.execv", side_effect=fake_execv), \
                mock.patch("sys.argv", list(argv)), \
                mock.patch.dict(os.environ):
            os.environ.pop("MEMBRANE_VIEWER", None)
            os.environ.update(env)
            with self.assertRaises(SystemExit):
                spec.loader.exec_module(mod)
        return captured["argv"]

    def test_default_boot_launches_no_viewer(self):
        argv = self._boot([".launcher.py"])
        self.assertNotIn("--viewer", argv)

    def test_env_opt_in_enables_viewer(self):
        argv = self._boot([".launcher.py"], {"MEMBRANE_VIEWER": "on"})
        self.assertIn("--viewer", argv)

    def test_flag_opt_in_enables_viewer(self):
        argv = self._boot([".launcher.py", "--viewer"])
        self.assertIn("--viewer", argv)

    def test_opencode_autopen_is_gated(self):
        src = open(OPENCODE).read()
        i = src.find("webbrowser.open")
        self.assertGreaterEqual(i, 0, "manual webbrowser site lost")
        self.assertIn("MEMBRANE_VIEWER", src, "auto-open not behind a gate")


if __name__ == "__main__":
    unittest.main()
