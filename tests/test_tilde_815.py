"""#815: a raw command routed to a method broke `~` paths -- the bound value was shell-quoted
whole ('~/some/repo'), and a quoted tilde is never expanded. A leading ~ or ~/ stays bare, the
rest is quoted as before; ~user and a tilde anywhere else stay quoted."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lang as LG  # noqa: E402

Q = LG.QUOTE["sh"]


def _sh(cmd):
    return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True).stdout.strip()


class Tilde(unittest.TestCase):
    def test_a_home_path_expands(self):
        home = os.path.expanduser("~")
        self.assertEqual(_sh("echo " + Q("~/some/repo")), home + "/some/repo")
        self.assertEqual(_sh("echo " + Q("~")), home)

    def test_the_rest_is_still_quoted(self):
        home = os.path.expanduser("~")
        self.assertEqual(_sh("printf %s " + Q("~/my dir; rm -rf x")), home + "/my dir; rm -rf x")

    def test_another_users_tilde_and_a_tilde_inside_stay_quoted(self):
        for v in ("~ok", "~root/x", "a~b", "x ~/y"):
            self.assertEqual(_sh("printf %s " + Q(v)), v, v)


class ThroughAMethod(unittest.TestCase):
    def test_a_routed_cd_into_a_home_path_works(self):
        from test_cliffhanger import _world
        from membrane import night as N
        srv, f, est, light, *_ = _world()
        N.method_define(srv, est, f, None, light.eid, "go", "cd {{dir}} && pwd")
        out = N.method_call(srv, est, f, None, light.eid, "go", {"dir": "~"})
        self.assertEqual(out.get("rc"), 0, out)
        self.assertEqual((out.get("output") or "").strip(), os.path.realpath(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
