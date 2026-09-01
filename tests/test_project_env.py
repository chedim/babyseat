"""Tests for the per-project env layer (membrane/project_env.py).

Contract under test:
- slugs are stable per absolute path and collision-free across siblings;
- project env files export ONLY MEMBRANE_/COUCHBASE_ keys, and a real env
  var always wins (setdefault, not stomp);
- onboarding never asks when non-interactive, already configured, already
  asked+declined, or a local cluster auto-detects; a YES writes the 600-mode
  env file and shares this launch; a NO records the decline (no MEMBRANE_STORE).
- secrets never land in the repo: the file is always under $HOME.

Plain-python runnable: .venv/bin/python -m tests.test_project_env
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import project_env as PE


class _FakeTTY:
    """is_tty -> True; feeds scripted answers to input()/getpass()."""
    def __init__(self, answers: list[str]):
        self.answers = list(answers)

    def is_tty(self):
        return True

    def input(self, prompt: str = "") -> str:
        if not self.answers:
            raise EOFError("no scripted answers left")
        return self.answers.pop(0)

    def getpass(self, prompt: str = "") -> str:
        if not self.answers:
            raise EOFError("no scripted answers left")
        return self.answers.pop(0)


class TestSlugAndPaths(unittest.TestCase):
    def test_slug_stable_and_distinct(self):
        a = PE.project_slug("/tmp/w/a")
        a2 = PE.project_slug("/tmp/w/a")
        b = PE.project_slug("/tmp/w/a/b")
        c = PE.project_slug("/tmp/w/b")
        self.assertEqual(a, a2)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertRegex(a, r"^[a-z0-9-]+-[0-9a-f]{8}$")

    def test_env_path_under_home_not_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            proj = Path(td) / "leaky-repo"
            proj.mkdir()
            p = PE.project_env_path(proj, home)
            self.assertTrue(str(p).startswith(str(home)))
            self.assertNotIn(str(proj), str(p.parent))     # repo never hosts it
            self.assertEqual(p.suffix, ".env")


class TestLoadProjectEnv(unittest.TestCase):
    def test_exports_only_namespaced_keys_and_env_wins(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            path = PE.project_env_path("/tmp/w/proj", home)
            path.parent.mkdir(parents=True)
            path.write_text(
                "COUCHBASE_BUCKET=team\n"
                "MEMBRANE_STORE=couchbase\n"
                "EVIL_KEY=1\n"
                "# comment\n"
                "garbage line\n"
                "HOME=/attacker/controlled\n")
            env = {"COUCHBASE_BUCKET": "operator-wins"}
            applied = PE.load_project_env("/tmp/w/proj", home, environ=env)
            self.assertEqual(env["COUCHBASE_BUCKET"], "operator-wins")
            self.assertEqual(env["MEMBRANE_STORE"], "couchbase")
            self.assertNotIn("EVIL_KEY", env)
            self.assertNotIn("HOME", env)
            self.assertIn("COUCHBASE_BUCKET", applied)
            self.assertNotIn("EVIL_KEY", applied)


class TestOnboarding(unittest.TestCase):
    def test_yes_writes_600_file_and_exports_launch_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            ft = _FakeTTY(["y", "couchbase://team", "alice", "s3cret",
                           "membrane-team", ""])
            env: dict = {}
            wrote = PE.couchbase_onboarding("/tmp/w/proj", home, env,
                                            input_fn=ft.input,
                                            getpass_fn=ft.getpass,
                                            is_tty=ft.is_tty,
                                            probe_fn=lambda: False,
                                            quiet=True)
            self.assertTrue(wrote)
            path = PE.project_env_path("/tmp/w/proj", home)
            vals = PE.parse_env_file(path)
            self.assertEqual(vals["COUCHBASE_USER"], "alice")
            self.assertEqual(vals["COUCHBASE_PASSWORD"], "s3cret")
            self.assertEqual(vals["MEMBRANE_STORE"], "couchbase")
            self.assertEqual(env["COUCHBASE_PASSWORD"], "s3cret")
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_no_records_decline_without_store(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            ft = _FakeTTY(["n"])
            env: dict = {}
            wrote = PE.couchbase_onboarding("/tmp/w/proj", home, env,
                                            input_fn=ft.input,
                                            getpass_fn=ft.getpass,
                                            is_tty=ft.is_tty,
                                            probe_fn=lambda: False,
                                            quiet=True)
            self.assertFalse(wrote)
            vals = PE.parse_env_file(PE.project_env_path("/tmp/w/proj", home))
            self.assertEqual(vals.get(PE.DECLINE_KEY), "1")
            self.assertNotIn("MEMBRANE_STORE", vals)
            self.assertNotIn("MEMBRANE_STORE", env)
            # second run: decline remembered, no prompt, no crash
            ft2 = _FakeTTY([])
            wrote2 = PE.couchbase_onboarding("/tmp/w/proj", home, env,
                                             input_fn=ft2.input,
                                             getpass_fn=ft2.getpass,
                                             is_tty=ft2.is_tty,
                                             probe_fn=lambda: False,
                                             quiet=True)
            self.assertFalse(wrote2)

    def test_skips_when_not_tty_or_env_configured(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            env: dict = {}
            # not a TTY: silent skip, nothing written
            self.assertFalse(PE.couchbase_onboarding(
                "/tmp/w/p", home, env, input_fn=iter(["y"]).__next__,
                getpass_fn=iter(["x"]).__next__, is_tty=lambda: False,
                probe_fn=lambda: False, quiet=True))
            self.assertFalse(PE.project_env_path("/tmp/w/p", home).exists())
            # env pre-configured: no prompt
            env2 = {"MEMBRANE_STORE": "couchbase"}
            self.assertFalse(PE.couchbase_onboarding(
                "/tmp/w/p", home, env2, input_fn=iter(["y"]).__next__,
                getpass_fn=iter(["x"]).__next__, is_tty=lambda: True,
                probe_fn=lambda: False, quiet=True))
            self.assertFalse(PE.project_env_path("/tmp/w/p", home).exists())
            # MEMBRANE_NO_ASK: silent skip
            env3 = {"MEMBRANE_NO_ASK": "1"}
            self.assertFalse(PE.couchbase_onboarding(
                "/tmp/w/p", home, env3, input_fn=iter(["y"]).__next__,
                getpass_fn=iter(["x"]).__next__, is_tty=lambda: True,
                quiet=True))
            self.assertFalse(PE.project_env_path("/tmp/w/p", home).exists())

    def test_store_configured_detection(self):
        self.assertTrue(PE.store_configured({"MEMBRANE_STORE": "couchbase"}))
        self.assertTrue(PE.store_configured({"COUCHBASE_USER": "u"}))
        self.assertFalse(PE.store_configured({}))
        self.assertFalse(PE.store_configured({"UNRELATED": "x"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)