"""A Claude rung's call shows as running when it STARTS (operator, 2026-10-24):
the reply's text stopped mid-word ("Output is cl") and stayed stuck until the
tool call issued right after it finished. The streamed text deltas are
batched and a batch went out only when the next event came; the reader told
the server about a call only after its result, so while the tool ran no
event came and the last words sat unpublished.

Now, as Claude Code does:
- the reader emits ("tool_start", {...}) as soon as the SDK reports the
  tool_use, before the tool runs (and a denied call is dropped);
- the server, on that start, publishes the pending words, closes the text
  part, and publishes the call's part as "running"; the result completes
  that same part (matched by the tool_use id);
- a held batch goes out after DELTA_COALESCE_S even when no event follows;
- the stored reply keeps text, call, text; the buffered path is unchanged.

The SDK is a stand-in; no CLI, no key, no paid call.
"""
import asyncio
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402
from tests.test_claude_agent_384 import key_fixture  # noqa: E402
from tests.test_claude_agent_tools_389 import (  # noqa: E402
    AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolResultBlock,
    ToolUseBlock, UserMessage, fake_sdk)

FULL = "mcp__membrane__query"


def _delta(text):
    return StreamEvent({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": text}})


class TestTheReaderSaysWhenACallStarts(unittest.TestCase):
    """The reader's items: the call's start comes before its result, while
    the tool still runs."""

    def _run(self, denied=False):
        gate = threading.Event()
        sdk = fake_sdk()

        async def query(*, prompt, options=None, transport=None):
            yield _delta("Output is cl")
            yield StreamEvent({"type": "content_block_start", "index": 1,
                               "content_block": {"type": "tool_use", "id": "toolu_1",
                                                 "name": FULL, "input": {}}})
            yield AssistantMessage([TextBlock("Output is cl"),
                                    ToolUseBlock("toolu_1", FULL, {"topic": "x"})])
            if denied:
                hooks = options.hooks["PreToolUse"][0].hooks
                await hooks[0]({"tool_use_id": "toolu_1"}, "toolu_1", None)
            await asyncio.get_running_loop().run_in_executor(None, gate.wait, 5)
            yield UserMessage([ToolResultBlock("toolu_1", "the result", False)])
            yield _delta("ear.")
            yield AssistantMessage([TextBlock("ear.")])
            yield ResultMessage()
        sdk.query = query
        items = []
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": sdk}), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), \
                key_fixture():
            if denied:
                ceiling = mock.patch.object(CA, "MAX_TOOL_CALLS_PER_ANSWER", 0)
                ceiling.start()
            try:
                for kind, val in CA.iter_reply([{"role": "user", "content": "go"}],
                                               "claude-agent:sonnet", stream=True, port=9):
                    items.append((kind, val, gate.is_set()))
                    if kind == "tool_start":
                        gate.set()           # the tool "finishes" only now
            finally:
                if denied:
                    ceiling.stop()
                gate.set()
        return items

    def test_the_start_comes_while_the_tool_runs(self):
        items = self._run()
        kinds = [k for k, _, _ in items if k not in ("step", "progress", "usage")]
        self.assertEqual(kinds[0], "text")
        self.assertEqual(kinds[1], "tool_start")
        self.assertIn("tool", kinds)
        self.assertLess(kinds.index("tool_start"), kinds.index("tool"))
        starts = [(v, done) for k, v, done in items if k == "tool_start"]
        self.assertFalse(starts[0][1], "the start came only after the tool finished")
        self.assertEqual(starts[0][0]["id"], "toolu_1")
        self.assertEqual(starts[0][0]["name"], "query")
        # the full input follows on the AssistantMessage's block
        self.assertIn({"topic": "x"}, [v.get("input") for v, _ in starts])
        self.assertEqual(kinds[-1], "text")

    def test_a_denied_call_is_dropped(self):
        items = self._run(denied=True)
        starts = [v for k, v, _ in items if k == "tool_start"]
        self.assertTrue(starts[-1].get("dropped"))
        self.assertNotIn("tool", [k for k, _, _ in items])

    def test_the_stream_carries_the_start_and_the_buffered_reply_does_not(self):
        def fake_iter(*a, **k):
            yield ("text", "Looking.")
            yield ("tool_start", {"id": "t1", "name": "query", "input": {}})
            yield ("tool", {"id": "t1", "name": "query", "input": {}, "output": "o",
                            "is_error": False})
            yield ("text", "Done.")
        with mock.patch.object(CA, "iter_reply", fake_iter):
            rep = CA.SSEReply([{"role": "user", "content": "hi"}], "m")
            chunks = [json.loads(c[5:]) for c in b"".join(rep).split(b"\n")
                      if c.startswith(b"data: {")]
            obj = json.loads(CA.JSONReply([{"role": "user", "content": "hi"}], "m").read())
        starts = [c[CA.TOOL_START_KEY] for c in chunks if c.get(CA.TOOL_START_KEY)]
        self.assertEqual(starts, [{"id": "t1", "name": "query", "input": {}}])
        self.assertEqual(rep.tools[0]["id"], "t1")
        self.assertEqual(obj["choices"][0]["message"]["content"], "Looking.Done.")
        self.assertEqual([t["at"] for t in obj[CA.TOOLS_KEY]], [8])


class TestTheLoopbackHandsTheStart(unittest.TestCase):
    def test_stream_hands_the_start_event(self):
        start = {"id": "t1", "name": "query", "input": {}}
        chunks = [{"choices": [], CA.RESP_KEY: CA.PROVIDER, CA.TOOL_START_KEY: start},
                  {"choices": [{"delta": {"content": "Done."}}], CA.RESP_KEY: CA.PROVIDER},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}], CA.RESP_KEY: CA.PROVIDER}]
        payload = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) \
            + b"data: [DONE]\n\n"

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        seen = []
        try:
            out = MS.loopback_ladder(s.server_address[1]).stream(
                {"model": "claude-agent:sonnet", "messages": []},
                lambda kind, val: seen.append((kind, val)) and False)
        finally:
            s.shutdown()
        self.assertIn(("tool_start", start), seen)
        self.assertNotIn(CA.TOOLS_KEY, out)          # a start is not a ran tool


def _runner(ladder):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda *a: "ok", system="", model="m", tools=[])
    r.events = []
    pub = r.hub.publish

    def publish(ev):
        r.events.append(json.loads(json.dumps(ev)))
        return pub(ev)
    r.hub.publish = publish
    return r


def _text_of(events, pid):
    return "".join(e["delta"] for e in events
                   if e["type"] == "message.part.delta" and e["partID"] == pid)


class TestTheServerShowsTheCallRunning(unittest.TestCase):
    def test_words_out_and_call_running_before_the_result(self):
        snap = {}

        def ladder(body):
            raise AssertionError("buffered call not used")

        def stream(body, on_event):
            on_event("text", "Output is ")
            on_event("text", "cl")               # held: inside the batch window
            on_event("tool_start", {"id": "t1", "name": "query", "input": {}})
            on_event("tool_start", {"id": "t1", "name": "query", "input": {"topic": "x"}})
            snap["events"] = list(r.events)      # the tool is running now
            on_event("tool", {"id": "t1", "name": "query", "input": {"topic": "x"},
                              "output": "the result", "is_error": False})
            on_event("text", "ear.")
            t = {"id": "t1", "name": "query", "input": {"topic": "x"},
                 "output": "the result", "is_error": False}
            return {CA.RESP_KEY: CA.PROVIDER, CA.TOOLS_KEY: [t],
                    "choices": [{"message": {"content": "Output is clear."}}],
                    "usage": {}}
        ladder.stream = stream
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")

        ev = snap["events"]
        ups = [e["part"] for e in ev if e["type"] == "message.part.updated"]
        texts = [p for p in ups if p.get("type") == "text" and p.get("text") == "Output is cl"]
        self.assertTrue(texts, "the words before the call were never published")
        pid = texts[0]["id"]
        self.assertEqual(_text_of(ev, pid), "Output is cl")
        self.assertTrue([p for p in ups if p["id"] == pid and p["time"].get("end")],
                        "the text part was not closed at the call's start")
        running = [p for p in ups if p.get("type") == "tool" and p.get("callID") == "t1"]
        self.assertTrue(running)
        self.assertEqual(running[-1]["state"]["status"], "running")
        self.assertEqual(running[-1]["state"]["input"], {"topic": "x"})

        # the same part goes running -> completed
        tool_ups = [e["part"] for e in r.events if e["type"] == "message.part.updated"
                    and e["part"].get("callID") == "t1"]
        self.assertEqual({p["id"] for p in tool_ups}, {running[0]["id"]})
        self.assertEqual(tool_ups[-1]["state"]["status"], "completed")
        self.assertEqual(tool_ups[-1]["state"]["output"], "the result")

        seq = [(p["type"], p.get("text") if p["type"] == "text" else p.get("callID"))
               for p in out["parts"] if p.get("type") in ("text", "tool")]
        self.assertEqual(seq, [("text", "Output is cl"), ("tool", "t1"), ("text", "ear.")])

    def test_a_held_batch_goes_out_without_a_next_event(self):
        snap = {}

        def ladder(body):
            raise AssertionError("buffered call not used")

        def stream(body, on_event):
            on_event("text", "Output is ")
            on_event("text", "cl")
            time.sleep(MS.DELTA_COALESCE_S * 4)   # nothing comes (a tool runs)
            snap["events"] = list(r.events)
            return {CA.RESP_KEY: CA.PROVIDER,
                    "choices": [{"message": {"content": "Output is cl"}}], "usage": {}}
        ladder.stream = stream
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "go")
        deltas = "".join(e["delta"] for e in snap["events"]
                         if e["type"] == "message.part.delta")
        self.assertEqual(deltas, "Output is cl")

    def test_a_dropped_or_unfinished_call_does_not_stay_running(self):
        def ladder(body):
            raise AssertionError("buffered call not used")

        def stream(body, on_event):
            on_event("text", "Hm.")
            on_event("tool_start", {"id": "t1", "name": "query", "input": {}})
            on_event("tool_start", {"id": "t1", "dropped": CA.CEILING_MESSAGE})
            on_event("tool_start", {"id": "t2", "name": "query", "input": {}})
            return {CA.RESP_KEY: CA.PROVIDER,
                    "choices": [{"message": {"content": "Hm."}}], "usage": {}}
        ladder.stream = stream
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")
        tools = [p for p in out["parts"] if p.get("type") == "tool"
                 and p.get("callID") in ("t1", "t2")]
        self.assertEqual(len(tools), 2)
        for p in tools:
            self.assertEqual(p["state"]["status"], "error")
            self.assertTrue(p["state"]["time"].get("end"))


if __name__ == "__main__":
    unittest.main()
