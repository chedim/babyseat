"""The startup update check: source detection, changelog-in-prompt,
safe perform (dirty tree refuses), boot-hook opt-outs. Network-touching
parts are exercised against the real local repo (fetch has a timeout;
no state is mutated -- check() only fetches).
"""
import os
import pathlib
from unittest import mock

import pytest

from membrane import update


def test_check_finds_this_repo():
    info = update.check(pathlib.Path(__file__).resolve().parent.parent)
    assert info["mode"] == "git"
    assert "available" in info


def test_check_missing_repo_is_none():
    with mock.patch.object(update, "_find_repo", return_value=None), \
            mock.patch.object(update, "_pip_direct_url", return_value=None):
        info = update.check()
    assert info["mode"] == "none" and not info["available"]


def test_direct_url_mode_reported():
    with mock.patch.object(update, "_find_repo", return_value=None), \
            mock.patch.object(update, "_pip_direct_url",
                              return_value="git+ssh://git@github.com/x/y"):
        info = update.check()
    assert info["mode"] == "direct"


def test_prompt_shows_changelog_noninteractively(capsys):
    info = {"available": True, "mode": "git", "repo": pathlib.Path("."),
            "branch": "main", "local": "aaa", "remote": "bbb", "behind": 2,
            "changelog": ["abc first commit", "def second commit"]}
    update.prompt_and_update(info, interactive=False)
    err = capsys.readouterr().err
    assert "abc first commit" in err and "def second commit" in err
    assert "update available" in err and "membrane update" in err


def test_perform_refuses_dirty_tree():
    dirty = os.environ.get("MEMBRANE_DIRTY_TEST")
    with mock.patch.object(update, "_git", return_value=" M x\n"):
        assert update.perform({"mode": "git", "repo": dirty or pathlib.Path("."),
                               "branch": "main"}) is False


def test_perform_ignores_non_git_modes():
    assert update.perform({"mode": "direct", "url": "x"}) is False
    assert update.perform({}) is False


def test_boot_hook_opt_out():
    os.environ["MEMBRANE_NO_UPDATE_CHECK"] = "1"
    try:
        with mock.patch.object(update, "check") as c:
            update.maybe_check_on_boot([])
            c.assert_not_called()
    finally:
        del os.environ["MEMBRANE_NO_UPDATE_CHECK"]


def test_boot_hook_no_tty_never_prompts():
    with mock.patch.object(update.sys.stdin, "isatty", return_value=False), \
            mock.patch.object(update, "check") as c:
        update.maybe_check_on_boot([])
        c.assert_not_called()


def test_fetch_remote_bad_repo_is_none(tmp_path):
    assert update._fetch_remote(tmp_path, "main") is None


def test_changelog_empty_range():
    assert update._changelog(pathlib.Path("."), "HEAD..HEAD") == []