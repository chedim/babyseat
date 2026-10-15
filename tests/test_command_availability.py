"""ISSUE #103: a reaction decision must not land as an unrunnable step.
A goal's planted step was `exec(action=link, commands='arecord -d 5 ...')` --
`arecord` is ALSA/Linux only, so on the macOS seat it can only fail, and the
goal then kept steering idle loops. The engine now checks tool availability on
the platform BEFORE spawning a shell: a command whose executable is absent from
PATH (and not a shell builtin) is refused honestly at decision time -- rc 127,
no subprocess, a clear reason -- instead of running and dying in the shell."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder
from membrane.efield import EntityField
from membrane.efstore import MemBackend
from membrane.entities import EntityStore
from membrane import night as N

NS = "membrane::"

# a name that is not a command on ANY platform (macOS seat, Linux CI alike)
MISSING = "membrane-no-such-cmd-4f3a"


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    est = EntityStore(path=None, shared=False)
    return f, est


class TestToolAvailability(unittest.TestCase):
    def test_a_missing_executable_is_refused_before_it_runs(self):
        f, est = _world()
        sentinel = os.path.join(tempfile.mkdtemp(), "mic.wav")
        out = N.run_command_entity(f, est, None,
                                   f"{MISSING} -d 5 > {sentinel}")
        self.assertFalse(out["ok"])
        self.assertEqual(out["rc"], 127)
        self.assertIn("not found on this platform", out.get("error") or "")
        self.assertIn(MISSING, out.get("error") or "")
        self.assertFalse(Path(sentinel).exists(),
                         "the refused step must not have run")

    def test_a_missing_executable_in_a_pipeline_is_refused(self):
        # the issue's exact shape: `arecord ... && echo mic-test-done` --
        # the echo is a builtin, but the PRIMARY command is absent
        f, est = _world()
        out = N.run_command_entity(f, est, None,
                                   f"{MISSING} -d 5 && echo mic-test-done")
        self.assertFalse(out["ok"])
        self.assertEqual(out["rc"], 127)
        self.assertIn(MISSING, out.get("error") or "")

    def test_a_real_command_still_runs(self):
        f, est = _world()
        out = N.run_command_entity(f, est, None, "echo membrane-ok")
        self.assertTrue(out["ok"])

    def test_a_shell_builtin_still_runs(self):
        # a builtin has no binary in PATH (shutil.which returns None) --
        # the gate must NOT mistake it for a missing tool
        f, est = _world()
        out = N.run_command_entity(f, est, None,
                                   "unset MEMBRANE_X && echo builtin-ok")
        self.assertTrue(out["ok"])

    def test_the_missing_binary_helper_names_the_tool(self):
        self.assertEqual(N._missing_binary(f"{MISSING} -d 5"), MISSING)
        self.assertIsNone(N._missing_binary("echo ok"))
        self.assertIsNone(N._missing_binary("unset FOO && echo ok"))


if __name__ == "__main__":
    unittest.main()
