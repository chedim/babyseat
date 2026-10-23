"""#472: an answer the turn watchdog aborts keeps the usage of the steps it
finished. Two Memo turns of 35 and 34 tool calls recorded ~2K input (the
estimate) because the usage only came on the reply's last chunk, which an
aborted reply never sends. Now the claude-agent reply streams its usage so
far after every API step (cumulative; the last chunk still carries the
whole answer's), the proxy keeps the LAST usage it saw, and a round whose
stream breaks hands the usage it had to the turn, which records it instead
of the estimate."""
import http.server
import json
import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402

import test_claude_agent_384 as T384  # noqa: E402
from test_claude_agent_steps_450 import FIRST, LATER, AssistantMessage, stepped_sdk  # noqa: E402


def _sum(*us):
    return {k: sum(u[k] for u in us) for k in us[0]}


class _Case(unittest.TestCase):
    def setUp(self):
        CA.reset_stats()
        self.addCleanup(CA.reset_stats)
        CA._HEAD_REFUSED.clear()
        self.addCleanup(CA._HEAD_REFUSED.clear)

    def patched(self, sdk):
        return [mock.patch.object(CA, "load_sdk", lambda: sdk),
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"),
                T384.key_fixture()]


class TestProgress(_Case):
    STEPS = [AssistantMessage([T384.TextBlock("a")], FIRST, "m1"),
             AssistantMessage([T384.TextBlock("b")], LATER, "m2")]

    def test_the_reply_carries_the_usage_so_far_after_each_step(self):
        sdk = stepped_sdk(self.STEPS)
        ps = self.patched(sdk)
        for p in ps:
            p.start()
        try:
            items = list(CA.iter_reply([{"role": "user", "content": "go"}],
                                       "claude-agent:haiku", stream=True, port=9))
        finally:
            for p in ps:
                p.stop()
        prog = [v for k, v in items if k == "progress"]
        self.assertEqual(prog, [CA._usage(FIRST), CA._usage(_sum(FIRST, LATER))])

    def test_the_sse_reply_streams_them_and_ends_on_the_answers_total(self):
        sdk = stepped_sdk(self.STEPS)
        ps = self.patched(sdk)
        for p in ps:
            p.start()
        try:
            raw = b"".join(CA.SSEReply([{"role": "user", "content": "go"}],
                                       "claude-agent:haiku", sdk))
        finally:
            for p in ps:
                p.stop()
        usages = [json.loads(ln[5:])["usage"] for ln in raw.split(b"\n")
                  if ln.startswith(b"data:") and ln[5:].strip() != b"[DONE]"
                  and json.loads(ln[5:]).get("usage")]
        self.assertEqual(usages[0], CA._usage(FIRST))
        self.assertEqual(usages[-1], CA._usage({"input_tokens": 3, "cache_read_input_tokens": 38000,
                                                "cache_creation_input_tokens": 9000,
                                                "output_tokens": 90}))


def _broken_stream(chunks):
    """A chunked SSE reply that breaks mid-chunk after `chunks` (the watchdog's cut)."""
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a): pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for c in chunks:
                b = ("data: " + json.dumps(c) + "\n\n").encode()
                self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
            self.wfile.write(b"400\r\npartial")          # a chunk that never completes
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestTheBrokenRound(unittest.TestCase):
    USAGE = {"prompt_tokens": 30000, "completion_tokens": 200, "total_tokens": 30200,
             "prompt_tokens_details": {"cached_tokens": 25000, "cache_write_tokens": 4000}}

    def test_a_broken_stream_hands_over_the_usage_it_had(self):
        srv = _broken_stream([{"choices": [{"delta": {"content": "working"}}]},
                              {"choices": [], "usage": self.USAGE}])
        try:
            with self.assertRaises(Exception) as cm:
                MS.loopback_ladder(srv.server_address[1]).stream({"messages": []},
                                                                 lambda k, v: False)
        finally:
            srv.shutdown()
        self.assertEqual(getattr(cm.exception, "membrane_usage", None), self.USAGE)

    def test_the_proxy_keeps_the_last_usage(self):
        import inspect
        from membrane import lanes as L
        src = inspect.getsource(L)
        self.assertNotIn("if usage_seen is None and isinstance(obj.get(\"usage\"), dict)", src)

    def test_the_turn_records_it_instead_of_the_estimate(self):
        usage = self.USAGE

        def ladder(body):
            raise AssertionError("no buffered retry once words were shown")

        def stream(body, on_event):
            on_event("text", "working")
            e = ConnectionError("the turn watchdog closed the socket")
            e.membrane_usage = usage
            raise e
        ladder.stream = stream
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="m", tools=[])
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")
        t = out["info"]["tokens"]
        self.assertEqual(t["input"], 30000)
        self.assertEqual(t["output"], 200)
        self.assertEqual(t["cache"], {"read": 25000, "write": 4000})

    def test_a_fallback_round_counts_the_stream_it_lost_too(self):
        usage = self.USAGE

        def ladder(body):
            return {"choices": [{"message": {"content": "done"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}

        def stream(body, on_event):
            e = ConnectionError("dropped before any word")
            e.membrane_usage = usage
            raise e
        ladder.stream = stream
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="m", tools=[])
        sid = r.sessions.create(title="t")["id"]
        t = r.run(sid, "go")["info"]["tokens"]
        self.assertEqual(t["input"], 30100)


if __name__ == "__main__":
    unittest.main()
