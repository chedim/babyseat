"""#655: busy() must see a running chat turn (TurnRunner.status needs a sid)."""
import types
from unittest import mock

from membrane import restarts as RS
from membrane.server import TurnRunner


def _srv(busy_sids=(), all_sids=("s1", "s2")):
    r = TurnRunner(None, types.SimpleNamespace(sessions=list(all_sids)),
                   None, None, None, system="", model="m")
    for s in busy_sids:
        r._busy[s] = {"type": "busy"}
    return types.SimpleNamespace(
        _loop_runner=r, loops=types.SimpleNamespace(running=lambda: 0))


def test_busy_lists_running_turn():
    assert RS.busy(_srv(["s2"])) == ["turn s2"]


def test_busy_idle_is_empty():
    assert RS.busy(_srv()) == []


def test_busy_keeps_loops():
    s = _srv(["s1"])
    s.loops = types.SimpleNamespace(running=lambda: 2)
    assert RS.busy(s) == ["turn s1", "2 loop(s)"]


def test_hold_waits_on_real_turn():
    s = _srv(["s1"])
    calls = []

    def sleep(_):
        calls.append(1)
        s._loop_runner._busy.clear()
    with mock.patch.object(RS.time, "sleep", side_effect=sleep):
        assert RS.hold_until_idle(s) == ["turn s1"]
    assert calls
