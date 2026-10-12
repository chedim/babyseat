"""The startup update check: source detection, changelog-in-prompt,
safe perform (dirty tree refuses), boot-hook opt-outs. Network-touching
parts are exercised against the real local repo (fetch has a timeout;
no state is mutated -- check() only fetches).

Plain unittest (repo convention, no pytest dependency):
capsys -> redirect_stderr into a StringIO; tmp_path -> tempfile.
"""
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from membrane import update


class TestCheck(unittest.TestCase):
    def test_check_finds_this_repo(self):
        info = update.check(pathlib.Path(__file__).resolve().parent.parent)
        self.assertEqual(info["mode"], "git")
        self.assertIn("available", info)

    def test_check_missing_repo_is_none(self):
        with mock.patch.object(update, "_find_repo", return_value=None), \
                mock.patch.object(update, "_pip_direct_url", return_value=None), \
                mock.patch.object(update, "_pypi_installed", return_value=None):
            info = update.check()
        self.assertEqual(info["mode"], "none")
        self.assertFalse(info["available"])

    def test_pypi_install_compares_with_pypi(self):
        # a release install (membrane-seat from PyPI) is checked too (2026-10-12)
        base = [mock.patch.object(update, "_find_repo", return_value=None),
                mock.patch.object(update, "_pip_direct_url", return_value=None),
                mock.patch.object(update, "_pypi_installed", return_value="0.3.0")]
        for p in base:
            p.start()
        try:
            with mock.patch.object(update, "_pypi_latest", return_value="0.3.1"):
                info = update.check()
            self.assertEqual((info["mode"], info["available"], info["remote"]), ("pypi", True, "0.3.1"))
            with mock.patch.object(update, "_pypi_latest", return_value="0.3.0"):
                self.assertFalse(update.check()["available"])
            with mock.patch.object(update, "_pypi_latest", return_value=None):   # offline / not published
                self.assertFalse(update.check()["available"])
        finally:
            for p in base:
                p.stop()

    def test_direct_url_mode_reported(self):
        with mock.patch.object(update, "_find_repo", return_value=None), \
                mock.patch.object(update, "_pip_direct_url",
                                  return_value="git+ssh://git@github.com/x/y"):
            info = update.check()
        self.assertEqual(info["mode"], "direct")

    def test_prompt_shows_changelog_noninteractively(self):
        info = {"available": True, "mode": "git", "repo": pathlib.Path("."),
                "branch": "main", "local": "aaa", "remote": "bbb", "behind": 2,
                "changelog": ["abc first commit", "def second commit"]}
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            update.prompt_and_update(info, interactive=False)
        err = buf.getvalue()
        self.assertIn("abc first commit", err)
        self.assertIn("def second commit", err)
        self.assertIn("update available", err)
        self.assertIn("membrane update", err)

    def test_perform_refuses_dirty_tree(self):
        dirty = os.environ.get("MEMBRANE_DIRTY_TEST")
        with mock.patch.object(update, "_git", return_value=" M x\n"):
            self.assertFalse(update.perform(
                {"mode": "git", "repo": dirty or pathlib.Path("."),
                 "branch": "main"}))

    def test_perform_ignores_non_git_modes(self):
        self.assertFalse(update.perform({"mode": "direct", "url": "x"}))
        self.assertFalse(update.perform({}))

    def test_boot_hook_opt_out(self):
        os.environ["MEMBRANE_NO_UPDATE_CHECK"] = "1"
        try:
            with mock.patch.object(update, "check") as c:
                update.maybe_check_on_boot([])
                c.assert_not_called()
        finally:
            del os.environ["MEMBRANE_NO_UPDATE_CHECK"]

    def test_boot_hook_no_tty_never_prompts(self):
        with mock.patch.object(update.sys.stdin, "isatty",
                               return_value=False), \
                mock.patch.object(update, "check") as c:
            update.maybe_check_on_boot([])
            c.assert_not_called()

    def test_fetch_remote_bad_repo_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(update._fetch_remote(pathlib.Path(td), "main"))

    def test_changelog_empty_range(self):
        self.assertEqual(update._changelog(pathlib.Path("."), "HEAD..HEAD"), [])


if __name__ == "__main__":
    unittest.main()