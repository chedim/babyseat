"""THE DROPPED STREAM (2026-10-11, "aaaand it is stuck"): the model's stream
was cut mid-generation; the seat read a silent end, counted a 'stop' with
no words, the operator's question got an empty reply and auto-chat
stalled on it for good. Now a stream that ends without its finish is
truncated -- asked again when nothing usable came; words already given are
kept, a half-built tool call never runs -- and auto-chat continues from
Argus's last real words after an empty turn (twice at most in a row)."""
import http.server
import json
import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402
from membrane import voice as V  # noqa: E402


def _serve(scripts):
    """Each POST plays the next script: a list of chunks, then [DONE] unless 'cut'."""
    calls = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            chunks, cut = scripts[min(len(calls), len(scripts) - 1)]
            calls.append(1)
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close"); self.end_headers()
            for c in chunks:
                self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
            if not cut:
                self.wfile.write(b"data: [DONE]\n\n")
            self.close_connection = True
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, calls


REASON = {"choices": [{"delta": {"reasoning": "thinking..."}}]}
WORDS = {"choices": [{"delta": {"content": "Going fine."}}]}
FIN = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
HALF_TOOL = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "entity", "arguments": '{"act'}}]}}]}


class TestDroppedStream(unittest.TestCase):
    def _call(self, scripts):
        srv, calls = _serve(scripts)
        try:
            ev = []
            out = MS.loopback_ladder(srv.server_address[1]).stream({"messages": []},
                                                                   lambda k, v: ev.append((k, v)) and False)
            return out, calls, ev
        finally:
            srv.shutdown()

    def test_a_drop_with_nothing_usable_is_asked_again(self):
        out, calls, ev = self._call([([REASON], True), ([REASON, WORDS, FIN], False)])
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["choices"][0]["message"]["content"], "Going fine.")
        self.assertTrue(any("stream dropped" in str(v) for k, v in ev if k == "reasoning"))

    def test_a_half_tool_call_never_runs(self):
        out, calls, _ = self._call([([HALF_TOOL], True), ([WORDS, FIN], False)])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("tool_calls", out["choices"][0]["message"])

    def test_words_already_given_are_kept(self):
        out, calls, _ = self._call([([WORDS], True)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["choices"][0]["message"]["content"], "Going fine.")
        self.assertEqual(out["choices"][0]["finish_reason"], "length")

    def test_it_gives_up_loudly(self):
        with self.assertRaises(ConnectionError):
            self._call([([REASON], True)])

    def test_a_whole_stream_is_untouched(self):
        out, calls, _ = self._call([([WORDS, FIN], False)])
        self.assertEqual((len(calls), out["choices"][0]["finish_reason"]), (1, "stop"))


class TestAutoChatAfterAnEmptyTurn(unittest.TestCase):
    def test_last_words_skip_the_empty_rows(self):
        ms = MS.MessageStore()
        for i, (role, text) in enumerate([("assistant", "real words"), ("assistant", "")]):
            ms.append("s", {"id": f"m{i}", "sessionID": "s", "role": role, "time": {"created": i}},
                      [{"id": f"p{i}", "type": "text", "text": text}] if text else [])
        r = type("R", (), {"messages": ms})()
        self.assertEqual(V._last_words(r, "s"), "real words")


if __name__ == "__main__":
    unittest.main()
