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
    assert MS.TurnRunner.TURN_IDLE_S == 300.0       # approved by the operator, 2026-10-23


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


# ---- a silent tool call is silence (the operator, 2026-10-24: the rung does
# not need #502's tool keep-alive). No keep-alive is streamed while a membrane
# tool runs, so a tool that prints nothing for TURN_IDLE_S ends the turn; the
# tools note tells Claude to start long commands detached and check them
# afterwards. The real chain end to end: a stand-in SDK -> SSEReply over HTTP
# -> loopback_ladder -> the turn runner's watchdog.

import asyncio  # noqa: E402
import http.server  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
from unittest import mock  # noqa: E402

from membrane import mcp_server as MCP  # noqa: E402
from tests.test_claude_agent_384 import key_fixture  # noqa: E402
from tests.test_claude_agent_tools_389 import UserMessage, fake_sdk  # noqa: E402


def _sdk(after_result_s=0.0):
    mod = fake_sdk(call=("query", {"topic": "x"}))
    orig = mod.query

    async def query(*, prompt, options=None, transport=None):
        async for m in orig(prompt=prompt, options=options, transport=transport):
            yield m
            if isinstance(m, UserMessage) and after_result_s:
                await asyncio.sleep(after_result_s)       # silence after the result
    mod.query = query
    return mod


def _seat(sdk):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for line in CA.SSEReply(body["messages"], "claude-agent:sonnet", sdk, port=9):
                self.wfile.write(line)
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run_tool(tool_s, after_result_s=0.0):
    sdk = _sdk(after_result_s)

    def slow(args, port):
        time.sleep(tool_s)                    # a silent tool: no output until it ends
        return "the result"
    patches = [mock.patch.dict(sys.modules, {"claude_agent_sdk": sdk}),
               mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"),
               mock.patch.dict(MCP.TOOL_IMPLS, {"query": slow}),
               key_fixture()]
    for p in patches:
        p.start()
    srv = _seat(sdk)
    try:
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                          MS.loopback_ladder(srv.server_address[1]),
                          lambda *a: "ok", system="", model="claude-agent:sonnet", tools=[])
        r.TURN_MAX_S = WINDOW
        r.TURN_IDLE_S = WINDOW
        errs = []
        r._stream_error_part = lambda sid, msg: errs.append(msg)
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "go")
        return errs
    finally:
        srv.shutdown()
        for p in reversed(patches):
            p.stop()


def test_a_long_silent_tool_call_is_silence():
    errs = _run_tool(tool_s=4 * WINDOW)
    assert len(errs) == 1 and "no event" in errs[0], errs


def test_after_the_result_silence_still_aborts():
    errs = _run_tool(tool_s=0.0, after_result_s=4 * WINDOW)
    assert len(errs) == 1 and "no event" in errs[0], errs


def test_no_keep_alive_is_emitted_while_a_tool_runs():
    assert not hasattr(CA, "TOOL_ALIVE_S")
    assert not hasattr(CA, "ALIVE_KEY")
    a = CA._Answer()
    got = []
    a.put = got.append                        # anything the handler might queue

    def slow(args, port):
        time.sleep(0.35)
        return "r"
    with mock.patch.dict(MCP.TOOL_IMPLS, {"query": slow}):
        res = asyncio.run(CA._tool_handler("query", 9, a)({}))
    assert res["content"][0]["text"].startswith("r")
    assert got == [], got


def test_the_tools_note_says_to_detach_long_commands():
    note = CA.TOOLS_NOTE
    for word in ("4 minutes", "detached", "nohup", "entity_link",
                 "entity script", "5 minutes"):
        assert word in note, word
