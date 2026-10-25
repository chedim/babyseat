"""A LOCAL MODEL'S TOOL CALLS KEEP THEIR NAMES (2026-10-25, the live seat).

A goal loop on qwen3:14b made 9 tool calls and every one came back
"the call had no tool name ('')"; a loop on glm-5.3-flash:cloud failed the
same way on 10-23. The arguments were sensible -- only the names were empty.

The cause: the serve's turn loop sent the lane's tools in the MCP shape
({name, description, inputSchema}) instead of the OpenAI chat shape
({type: function, function: {name, description, parameters}}). Ollama reads
only the OpenAI shape, so it handed the model tools with EMPTY names, and the
model called them by the only name it had: ''. (The claude-agent rung builds
its own tool list, so Claude never showed it.)

Pinned here: with the seat's own tool registry (no explicit tools), every
advertised tool rides in the OpenAI shape with its name; and a fake Ollama that
names a call only from the tools it was given -- streaming the name in the
first chunk, the arguments in later ones, reasoning interleaved (qwen3's form)
-- gets its call to the executor under its real name, on a loop's lane and on
a person's chat alike.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402


def _ollama_names(tools) -> list:
    """The names Ollama's /v1 sees: only the OpenAI shape carries one."""
    return [str(((t or {}).get("function") or {}).get("name") or "") for t in tools or []]


class _FakeOllama(BaseHTTPRequestHandler):
    """Ollama's /v1 stream: the call is named from the tools the request
    carried (a nameless tool gives a nameless call), the name in the first
    tool-call chunk, the arguments split over later ones, reasoning between."""
    bodies: list = []
    want: str = "state"
    args: dict = {"section": "changes"}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        first = len(type(self).bodies) % 2 == 1      # odd round: call, even: answer
        names = _ollama_names(body.get("tools"))
        name = type(self).want if type(self).want in names else ""
        a = json.dumps(type(self).args)
        if first:
            chunks = [
                {"reasoning": "I should check the state."},
                {"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                                 "function": {"name": name, "arguments": ""}}]},
                {"reasoning": " With the changes section."},
                {"tool_calls": [{"index": 0, "function": {"arguments": a[:7]}}]},
                {"tool_calls": [{"index": 0, "function": {"arguments": a[7:]}}]},
            ]
            finish = "tool_calls"
        else:
            chunks = [{"content": "done"}]
            finish = "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for d in chunks:
            self.wfile.write(b"data: " + json.dumps(
                {"choices": [{"index": 0, "delta": d}]}).encode() + b"\n\n")
        self.wfile.write(b"data: " + json.dumps(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class TestLocalToolNames(unittest.TestCase):
    def setUp(self):
        _FakeOllama.bodies = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def _turn(self, agent):
        seen = []
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                         S.loopback_ladder(self.srv.server_address[1]),
                         lambda name, args, call_id: seen.append((name, args)) or "ok",
                         system="", model="qwen3:14b")
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go", user_info={"id": "msg_u1", "role": "user", "agent": agent})
        tools = [p for p in (out or {}).get("parts") or [] if p.get("type") == "tool"]
        return seen, tools

    def test_the_seat_advertises_the_openai_shape(self):
        for agent in ("watch:bb7fc5249243", "build", "plan"):
            self._turn(agent)
            body = _FakeOllama.bodies[-2]
            self.assertTrue(body["tools"], agent)
            for t in body["tools"]:
                self.assertEqual(t.get("type"), "function", (agent, t))
                self.assertTrue(t["function"]["name"], (agent, t))
                self.assertIn("parameters", t["function"])
            self.assertIn("state", _ollama_names(body["tools"]))

    def test_a_loops_streamed_call_reaches_the_executor_named(self):
        seen, tools = self._turn("watch:bb7fc5249243")
        self.assertEqual(tools[0]["tool"], "state")
        self.assertNotIn("no tool name", str(tools[0]["state"].get("output")))
        self.assertEqual([n for n, _ in seen], ["state"])
        self.assertEqual(seen[0][1].get("section"), "changes")

    def test_a_persons_chat_too(self):
        seen, tools = self._turn("build")
        self.assertEqual(tools[0]["tool"], "state")
        self.assertEqual([n for n, _ in seen], ["state"])


if __name__ == "__main__":
    unittest.main()
