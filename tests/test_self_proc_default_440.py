"""#440 part 5: Blurp's own process is the default. MEMBRANE_SELF_PROC unset
means on; MEMBRANE_SELF_PROC=0 keeps the in-process loop. A child spawned
with the seat's own environment always carries the flag it checks."""
import os
from unittest import mock

import pytest

from membrane import self_proc as SP
from membrane import self_super as SUP


def test_unset_means_the_own_process(monkeypatch):
    monkeypatch.delenv(SUP.FLAG, raising=False)
    assert SUP.enabled() is True


def test_zero_keeps_the_loop_in_the_seat(monkeypatch):
    monkeypatch.setenv(SUP.FLAG, "0")
    assert SUP.enabled() is False


def test_one_still_means_the_own_process(monkeypatch):
    monkeypatch.setenv(SUP.FLAG, "1")
    assert SUP.enabled() is True


def test_a_child_spawned_with_the_seat_environment_carries_the_flag(monkeypatch, tmp_path):
    monkeypatch.delenv(SUP.FLAG, raising=False)
    seen = {}

    def fake_spawn(path, argv, env, file_actions=None):
        seen["env"] = dict(env)
        raise OSError("not really spawning")

    monkeypatch.setattr(os, "posix_spawn", fake_spawn)
    with pytest.raises(Exception):
        SP.spawn("http://127.0.0.1:9", tmp_path, env=None, log_path=tmp_path / "log")
    assert seen["env"].get(SP.FLAG) == "1"
