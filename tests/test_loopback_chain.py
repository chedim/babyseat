"""Blurp learns from the model's writes, which it can only link by their chain.
The turn's model call is a loopback POST to this seat's /v1; the handler
thread that runs the model (and its tools) must resume the turn's chain and
writer, so the call has to carry them as headers (writes.headers)."""
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
from membrane import writes as W  # noqa: E402

_REPLY = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}


def _serve():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            seen.append(dict(self.headers))
            if body.get("stream"):
                chunk = json.dumps({"choices": [{"index": 0, "delta": {"content": "ok"},
                                                 "finish_reason": "stop"}]})
                out = f"data: {chunk}\n\ndata: [DONE]\n\n".encode()
                ctype = "text/event-stream"
            else:
                out, ctype = json.dumps(_REPLY).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


class TestLoopbackCarriesChain(unittest.TestCase):
    def setUp(self):
        self.srv, self.seen = _serve()
        self.addCleanup(self.srv.shutdown)
        self.ladder = MS.loopback_ladder(self.srv.server_address[1])
        self.body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}

    def _check(self, h, cid):
        got = {k.lower(): v for k, v in h.items()}
        self.assertEqual(json.loads(got[W.HDR_CHAIN.lower()])["id"], cid)
        self.assertEqual(got[W.HDR_WRITER.lower()], "llm")

    def test_call_carries_the_turns_chain_and_writer(self):
        with W.turn("s1"):
            cid = W.current()["id"]
            self.ladder(self.body)
        self._check(self.seen[-1], cid)

    def test_stream_carries_the_turns_chain_and_writer(self):
        with W.turn("s1"):
            cid = W.current()["id"]
            self.ladder.stream(self.body, lambda kind, d: None)
        self._check(self.seen[-1], cid)

    def test_outside_a_turn_nothing_is_claimed(self):
        self.ladder(self.body)
        got = {k.lower() for k in self.seen[-1]}
        self.assertNotIn(W.HDR_CHAIN.lower(), got)


if __name__ == "__main__":
    unittest.main()
