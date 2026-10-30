"""#711: a person's message that lands between the idle check and the re-exec
is never dropped -- the restart waits for its turn, and a message that still
slipped through (it came during the teardown) is answered after the boot."""
import os
import types
from unittest import mock

from membrane import restarts as RS


def _row(role, text, ms, mid="m"):
    return {"info": {"id": mid, "role": role, "time": {"created": ms}},
            "parts": [{"type": "text", "text": text}]}


class _Msgs:
    def __init__(self, rows):
        self.rows = rows

    def list(self, sid):
        return list(self.rows.get(sid) or [])


def _srv(rows, busy_sids=()):
    ran = []
    r = types.SimpleNamespace(
        sessions=types.SimpleNamespace(sessions={s: {} for s in rows}),
        messages=_Msgs(rows), _busy={s: {"type": "busy"} for s in busy_sids})
    r.status = lambda sid: r._busy.get(sid) or {"type": "idle"}
    r.run = lambda sid, text, user_info=None, **k: ran.append((sid, text, user_info["id"]))
    srv = types.SimpleNamespace(_loop_runner=r, loops=types.SimpleNamespace(running=lambda: 0))
    return srv, ran


def test_waiting_sees_person_message_after_schedule():
    srv, _ = _srv({"s1": [_row("assistant", "hi", 1000),
                          _row("user", "[user: Dima]\nhello?", 5000)]})
    assert RS.waiting_messages(srv, since_ms=4000) == ["s1"]


def test_waiting_ignores_older_answered_and_loop_messages():
    srv, _ = _srv({"old": [_row("user", "[user: Dima]\nx", 1000)],
                   "answered": [_row("user", "[user: Dima]\nx", 5000), _row("assistant", "y", 5100)],
                   "loop": [_row("user", "goal pulse", 5000)]})
    assert RS.waiting_messages(srv, since_ms=4000) == []


def test_waiting_skips_a_session_whose_turn_runs():
    srv, _ = _srv({"s1": [_row("user", "[user: Dima]\nx", 5000)]}, busy_sids=["s1"])
    assert RS.waiting_messages(srv, since_ms=4000) == []      # busy() already holds it


def test_hold_waits_for_a_message_that_arrived_in_the_grace_window():
    rows = {"s1": [_row("user", "[user: Dima]\nhello?", 5000)]}
    srv, _ = _srv(rows)
    slept = []

    def sleep(_):
        slept.append(1)
        rows["s1"].append(_row("assistant", "answer", 6000))
    with mock.patch.object(RS.time, "sleep", side_effect=sleep):
        held = RS.hold_until_idle(srv, since_ms=4000)
    assert slept and held == ["message s1"]


def test_hold_without_since_is_unchanged():
    srv, _ = _srv({"s1": [_row("user", "[user: Dima]\nhello?", 5000)]})
    with mock.patch.object(RS.time, "sleep", side_effect=AssertionError("no wait")):
        assert RS.hold_until_idle(srv) == []


def test_boot_after_reexec_answers_the_dropped_message():
    rows = {"s1": [_row("assistant", "hi", 1000), _row("user", "[user: Dima]\nhello?", 5000, "u1")],
            "s2": [_row("user", "[user: Dima]\nold", 1000)]}
    srv, ran = _srv(rows)
    with mock.patch.dict(os.environ, {RS.SINCE_ENV: "4000"}):
        n = RS.answer_dropped(srv, wait=False)
        assert RS.SINCE_ENV not in os.environ           # read once, never inherited
    assert n == 1 and ran == [("s1", "[user: Dima]\nhello?", "u1")]


def test_cold_boot_answers_nothing():
    srv, ran = _srv({"s1": [_row("user", "[user: Dima]\nhello?", 5000)]})
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(RS.SINCE_ENV, None)
        assert RS.answer_dropped(srv, wait=False) == 0
    assert ran == []


def test_reexec_hands_the_schedule_time_to_the_next_boot():
    RS.RESTART["requested"] = True
    RS.RESTART["since_ms"] = 4242
    try:
        with mock.patch.object(RS.os, "execv") as ex, mock.patch.dict(os.environ, {}):
            RS.reexec_if_requested(None)
            assert os.environ[RS.SINCE_ENV] == "4242"
        assert ex.called
    finally:
        RS.RESTART["requested"] = False
        RS.RESTART.pop("since_ms", None)
