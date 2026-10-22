"""A REPLY CUT BY A PERSON IS NOT A FINISHED REPLY (#449): a person's message
preempts the running turn; the streamed round stops at its next chunk and its
words so far used to be stored as an ordinary reply (finish=stop, no error) --
the chat showed a few characters, cut mid-word, as if that was the answer.
The cut (and a dropped stream) is now marked on the row, and a tool call
half-built when the cut arrives never runs."""
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

WORDS = {"choices": [{"delta": {"content": "Gl"}}]}
MORE = {"choices": [{"delta": {"content": "ad to help."}}]}
FIN = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
HALF_TOOL = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                                                    "function": {"name": "entity", "arguments": '{"act'}}]}}]}
REST_TOOL = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ion": "read"}'}}]}}]}


def _serve(first, rest, cut=False):
    """One streamed reply: `first` chunks, then waits for `go`, then `rest`
    (and [DONE] unless `cut`). `sent` is set once `first` is out."""
    sent, go = threading.Event(), threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close"); self.end_headers()
            for c in first:
                self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
            self.wfile.flush()
            sent.set()
            go.wait(10)
            try:
                for c in rest:
                    self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
                if not cut:
                    self.wfile.write(b"data: [DONE]\n\n")
            except OSError:
                pass
            self.close_connection = True
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sent, go


def _turn(first, rest, preempt=True, cut=False):
    srv, sent, go = _serve(first, rest, cut=cut)
    ran = []
    try:
        cell = {}
        ladder = MS.loopback_ladder(srv.server_address[1], cell=cell)
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: ran.append(a) or "ok", system="", model="m", tools=[])
        cell["runner"] = r
        r._turn_watchdog = lambda *a, **k: None
        sid = r.sessions.create(title="t")["id"]
        out = {}
        t = threading.Thread(target=lambda: out.update(r.run(sid, "hello")), daemon=True)
        t.start()
        assert sent.wait(10)
        if preempt:
            assert r.preempt(sid)
        go.set()
        t.join(15)
        assert not t.is_alive()
        return out, ran
    finally:
        go.set()
        srv.shutdown()


def _text(out):
    return "".join(p.get("text") or "" for p in out["parts"] if p.get("type") == "text")


class TestCutReply(unittest.TestCase):
    def test_a_cut_reply_is_marked_cut_not_stop(self):
        out, _ = _turn([WORDS], [MORE, FIN])
        info = out["info"]
        self.assertTrue(_text(out).startswith("Gl"))          # the words shown stay
        self.assertNotEqual(info.get("finish"), "stop")
        self.assertEqual((info.get("error") or {}).get("name"), "MessageAbortedError")
        self.assertIn("cut", json.dumps(info["error"]))

    def test_a_half_tool_call_in_a_cut_stream_never_runs(self):
        out, ran = _turn([HALF_TOOL], [REST_TOOL, FIN])
        self.assertEqual(ran, [])
        self.assertFalse([p for p in out["parts"] if p.get("type") == "tool" and p.get("tool") == "entity"
                          and (p.get("state") or {}).get("status") == "completed"])

    def test_a_dropped_stream_with_words_is_marked(self):
        out, _ = _turn([WORDS], [], preempt=False, cut=True)
        info = out["info"]
        self.assertEqual(_text(out), "Gl")
        self.assertNotEqual(info.get("finish"), "stop")
        self.assertIn("dropped", json.dumps(info.get("error") or {}))

    def test_a_whole_reply_is_untouched(self):
        out, _ = _turn([WORDS], [MORE, FIN], preempt=False)
        self.assertEqual(_text(out), "Glad to help.")
        self.assertEqual(out["info"].get("finish"), "stop")
        self.assertNotIn("error", out["info"])


if __name__ == "__main__":
    unittest.main()
