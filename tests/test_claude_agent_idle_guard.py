"""A claude-agent turn works like Claude Code (operator, 2026-10-23: "let's
do it as Claude. If the model loops, that's not our problem but the
model's"): no wall clock. The 300 s turn watchdog cut Claude turns that were
still working (284 s turns on 10-22): the whole SDK run is ONE round, so the
per-round touch never moved its clock. A claude-agent turn is now aborted
only after TURN_IDLE_S with no event at all (no token, no tool call, no tool
result); every event its stream publishes resets the idle clock, and there
is no absolute lifetime. Other models keep today's watchdog."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402

WINDOW = 0.3          # the test's fast clock, for both the wall clock and the idle guard


def _reply():
    return {CA.RESP_KEY: CA.PROVIDER, CA.TOOLS_KEY: [],
            "choices": [{"message": {"content": "done"}}], "usage": {}}


def _talky(body, on_event):
    for _ in range(30):                   # 1.5 s of steady events: past the window AND the old 3x cap
        on_event("text", ".")
        time.sleep(0.05)
    return _reply()


def _silent(body, on_event):
    on_event("text", "Looking.")
    time.sleep(4 * WINDOW)                # one long stretch with no event at all
    return _reply()


def _run(model, stream):
    def ladder(body):
        raise AssertionError("buffered call not used")
    ladder.stream = stream
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda *a: "ok", system="", model=model, tools=[])
    r.TURN_MAX_S = WINDOW
    r.TURN_IDLE_S = WINDOW
    errs = []
    r._stream_error_part = lambda sid, msg: errs.append(msg)
    sid = r.sessions.create(title="t")["id"]
    r.run(sid, "go")
    return errs


def test_the_idle_window_is_300s():
    assert MS.TurnRunner.TURN_IDLE_S == 300.0       # flagged for the operator's approval


def test_a_claude_turn_that_keeps_working_is_not_aborted():
    assert _run("claude-agent:sonnet", _talky) == []


def test_a_silent_claude_turn_is_aborted_after_the_idle_window():
    errs = _run("claude-agent:sonnet", _silent)
    assert len(errs) == 1, errs
    assert "no event" in errs[0] and "watchdog" in errs[0], errs


def test_another_model_keeps_the_wall_clock():
    errs = _run("m", _talky)
    assert len(errs) == 1, errs
    assert "outlived" in errs[0], errs
