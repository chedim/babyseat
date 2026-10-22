"""A DELIVERY TIMEOUT FAILS THE TURN VISIBLY (issue #431, option C of #429's
open question 1, the operator's 2026-10-21).

Under load the picked rung may not answer inside its budget (90 s). Before
this, `_v1_chat`'s except called `_forward_raw(raw)`: the client's bare
request went out again with no memory, and nothing said so (8 of 20 turns on
the contended probe). Now:

- the proxy answers a timed-out delivery with a clear error (buffered: HTTP
  503 type seat_busy; streamed: an SSE error event, then [DONE]), never a
  bare resend, and counts it (busy_timeouts on /membrane/diag/deliver);
- the timed-out rung is still outage-noted, so the router's next pick skips
  it; a delivery to that next rung carries the injection like any turn;
- the serve's chat marks the failed row (no buffered second call behind a
  busy streamed round) and the panel offers Retry, which re-sends the same
  message; nothing retries by itself.

The backends are local fakes; the budget is shortened for the test clock.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import diag as D  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import server as MS  # noqa: E402

FAST_BUDGET = 0.3          # the test's clock (the seat's stays RUNG_BUDGET_S)
SLOW = 1.5                 # the slow backend answers after this


class _Backend(BaseHTTPRequestHandler):
    """A fake model server: records each request's body; `delay` before it
    answers (past the budget = the loaded seat)."""
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.seen.append(body)
        time.sleep(self.server.delay)
        if body.get("stream"):
            out = (b'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
                   b"data: [DONE]\n\n")
            ctype = "text/event-stream"
        else:
            out = json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                           "message": {"role": "assistant", "content": "hi"}}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                        "total_tokens": 2}}).encode()
            ctype = "application/json"
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except OSError:
            pass                                   # the proxy gave up on us

    def log_message(self, *a):
        pass


def _backend(delay):
    b = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    b.seen, b.delay = [], delay
    threading.Thread(target=b.serve_forever, daemon=True).start()
    return b


class _Router:
    def __init__(self):
        self.records, self.outages = {}, []

    def note_outage(self, name, why, cooldown=300.0):
        self.outages.append(name)

    def note_latency(self, *a, **k):
        pass

    def record_spend(self, *a, **k):
        pass


class _Cap:
    def on_turn(self, *a, **k):
        pass

    def on_calls(self, *a, **k):
        pass


def _handler(backend_url):
    mem = types.SimpleNamespace(embedder=None, store=None, efield=None)
    srv = types.SimpleNamespace(membrane=mem, router=_Router(), capturer=_Cap(),
                                think=None, backend=backend_url, cloud_keys={},
                                gate_monologue=False)
    h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
    h.server = srv
    h.wfile = io.BytesIO()
    h.request_version = "HTTP/1.1"
    h.requestline = "POST /v1/chat/completions HTTP/1.1"
    h.command = "POST"
    h.path = "/v1/chat/completions"
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = True
    h.headers = {}
    return h


INJECTION = "WHERE WE ARE (membrane): the backup job skips files over 2 GB"
ASK = "what does the backup job skip?"


def _up(model, stream):
    return {"model": model, "stream": stream,
            "messages": [{"role": "system", "content": INJECTION},
                         {"role": "user", "content": ASK}]}


def _split(raw):
    head, body = raw.split(b"\r\n\r\n", 1)
    return head.split(b"\r\n", 1)[0].decode(), body


class _ProxyCase(unittest.TestCase):
    def setUp(self):
        self.slow = _backend(SLOW)
        self.addCleanup(self.slow.shutdown)
        p = mock.patch.object(L, "RUNG_BUDGET_S", FAST_BUDGET)
        p.start()
        self.addCleanup(p.stop)
        D.reset_deliver_stats()

    def _deliver(self, stream, model="local-a", url=None):
        h = _handler(url or f"http://127.0.0.1:{self.slow.server_address[1]}")
        h._forward_raw = mock.Mock(side_effect=AssertionError("a bare resend"))
        verdict = {"verdict": "escalate", "route": {"route": model, "kind": "local"}}
        h._v1_deliver(_up(model, stream), json.dumps({"bare": True}).encode(),
                      ASK, None, verdict, INJECTION)
        return h


class TestTheBudgetIsNamed(unittest.TestCase):
    def test_the_rung_budget_stays_90_seconds(self):
        self.assertEqual(L.RUNG_BUDGET_S, 90)


class TestBufferedTimeout(_ProxyCase):
    def test_a_timed_out_delivery_is_a_visible_503(self):
        t0 = time.monotonic()
        h = self._deliver(stream=False)
        self.assertLess(time.monotonic() - t0, SLOW)     # the budget, not the backend
        status, body = _split(h.wfile.getvalue())
        self.assertIn("503", status)
        err = json.loads(body)["error"]
        self.assertEqual(err["type"], D.SEAT_BUSY_TYPE)
        self.assertIn("too busy to answer with its memory", err["message"])
        self.assertIn("try again", err["message"])

    def test_no_bare_resend_and_the_counter_moves(self):
        h = self._deliver(stream=False)
        h._forward_raw.assert_not_called()
        time.sleep(SLOW + 0.2)
        self.assertEqual(len(self.slow.seen), 1)          # the one memory turn, nothing after
        self.assertEqual(self.slow.seen[0]["messages"][0]["content"], INJECTION)
        self.assertEqual(D.deliver_stats()["busy_timeouts"], 1)


class TestStreamedTimeout(_ProxyCase):
    def test_a_timed_out_stream_ends_with_an_error_event(self):
        h = self._deliver(stream=True)
        h._forward_raw.assert_not_called()
        status, body = _split(h.wfile.getvalue())
        self.assertIn("200", status)                       # the stream had begun
        events = [ln[5:].strip() for ln in body.split(b"\n") if ln.startswith(b"data:")]
        self.assertEqual(events[-1], b"[DONE]")
        errs = [json.loads(e)["error"] for e in events[:-1] if b'"error"' in e]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["type"], D.SEAT_BUSY_TYPE)
        self.assertIn("too busy", errs[0]["message"])
        self.assertEqual(D.deliver_stats()["busy_timeouts"], 1)


class TestDeliverFailed(_ProxyCase):
    """_v1_chat's except: a timeout is the busy error; anything else keeps
    today's path (this change is about timeouts only)."""

    def test_a_timeout_never_resends_bare(self):
        h = _handler("http://127.0.0.1:9")
        h._forward_raw = mock.Mock()
        h._deliver_failed(TimeoutError("timed out"), b"{}", {"model": "m", "stream": False})
        h._forward_raw.assert_not_called()
        self.assertIn("503", _split(h.wfile.getvalue())[0])
        self.assertEqual(D.deliver_stats()["busy_timeouts"], 1)

    def test_a_wrapped_timeout_is_a_timeout(self):
        import socket
        import urllib.error
        self.assertTrue(L._is_timeout(urllib.error.URLError(socket.timeout("timed out"))))
        self.assertTrue(L._is_timeout(TimeoutError()))
        self.assertFalse(L._is_timeout(ConnectionRefusedError()))

    def test_other_failures_keep_todays_path(self):
        h = _handler("http://127.0.0.1:9")
        h._forward_raw = mock.Mock()
        h._deliver_failed(ConnectionRefusedError("down"), b"{}", {"model": "m"})
        h._forward_raw.assert_called_once_with(b"{}")
        self.assertEqual(D.deliver_stats()["busy_timeouts"], 0)


class TestFallDownKeepsMemory(_ProxyCase):
    """The legitimate fall-down: the timed-out rung is outage-noted (the
    router's next pick skips it); the next rung's delivery carries the
    injection and answers."""

    def test_the_next_rung_gets_the_injection(self):
        fast = _backend(0)
        self.addCleanup(fast.shutdown)
        h = self._deliver(stream=False, model="local-a")
        self.assertEqual(h.server.router.outages, ["local-a"])
        h2 = self._deliver(stream=False, model="local-b",
                           url=f"http://127.0.0.1:{fast.server_address[1]}")
        status, body = _split(h2.wfile.getvalue())
        self.assertIn("200", status)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hi")
        self.assertEqual(fast.seen[0]["model"], "local-b")
        self.assertEqual(fast.seen[0]["messages"][0]["content"], INJECTION)
        self.assertEqual(D.deliver_stats()["busy_timeouts"], 1)


class TestDiagRoute(unittest.TestCase):
    def test_diag_deliver_reports_the_counter(self):
        D.reset_deliver_stats()
        D.note_busy_timeout()
        self.assertIn("/membrane/diag/deliver", D.DIAG_PATHS)
        sent = {}
        h = types.SimpleNamespace(path="/membrane/diag/deliver", command="GET",
                                  headers={}, client_address=("127.0.0.1", 1))
        with mock.patch.object(D, "_send", lambda hh, code, obj: sent.update(code=code, obj=obj)), \
                mock.patch("membrane.seatauth.is_loopback", lambda hh: True):
            self.assertTrue(D.route(h, "/membrane/diag/deliver"))
        self.assertEqual(sent["code"], 200)
        self.assertEqual(sent["obj"]["busy_timeouts"], 1)
        self.assertEqual(sent["obj"]["rung_budget_s"], L.RUNG_BUDGET_S)


class TestOllamaNativeStream(unittest.TestCase):
    """An Ollama-speaking client (/api/chat, #221) streaming NDJSON gets the
    busy error as Ollama's own error line, not a silent empty answer."""

    def test_the_error_event_becomes_an_ollama_error_line(self):
        from membrane import ollama_api as OA
        real = io.BytesIO()
        w = OA.OllamaWire(real, "chat", "m")
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
        w.write(b"data: " + json.dumps({"error": {"message": D.SEAT_BUSY_MESSAGE,
                                                  "type": D.SEAT_BUSY_TYPE}}).encode() + b"\n\n")
        w.write(b"data: [DONE]\n\n")
        w.finish()
        recs = [json.loads(ln) for ln in real.getvalue().split(b"\r\n\r\n", 1)[1].splitlines()
                if ln.strip()]
        self.assertEqual([r["error"] for r in recs if "error" in r], [D.SEAT_BUSY_MESSAGE])


# ---- the serve: the loopback call and the turn runner -------------------------

class _Proxy(BaseHTTPRequestHandler):
    """A stand-in /v1 proxy that answers every turn 'too busy'."""
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.seen.append(body)
        err = {"error": {"message": D.SEAT_BUSY_MESSAGE, "type": D.SEAT_BUSY_TYPE}}
        if body.get("stream"):
            out = (b'data: {"membrane": {"injection": "mem"}}\n\n'
                   + b"data: " + json.dumps(err).encode() + b"\n\n" + b"data: [DONE]\n\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            out = json.dumps(err).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TestLoopback(unittest.TestCase):
    def setUp(self):
        self.p = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
        self.p.seen = []
        threading.Thread(target=self.p.serve_forever, daemon=True).start()
        self.addCleanup(self.p.shutdown)
        self.call = MS.loopback_ladder(self.p.server_address[1])

    def test_buffered_busy_raises_seat_busy(self):
        with self.assertRaises(MS.SeatBusy) as cm:
            self.call({"model": "m", "messages": [{"role": "user", "content": "x"}]})
        self.assertIn("too busy", str(cm.exception))

    def test_streamed_busy_raises_once_never_asks_again(self):
        with self.assertRaises(MS.SeatBusy):
            self.call.stream({"model": "m", "messages": [{"role": "user", "content": "x"}]},
                             lambda *a: None)
        self.assertEqual(len(self.p.seen), 1)


class TestRunnerMarksTheRow(unittest.TestCase):
    def _runner(self, tmp, stream_too):
        calls = {"buffered": 0, "stream": 0}

        def ladder(body):
            calls["buffered"] += 1
            raise MS.SeatBusy(D.SEAT_BUSY_MESSAGE)

        def stream(body, on_event):
            calls["stream"] += 1
            on_event("ext", {"injection": "mem"})
            raise MS.SeatBusy(D.SEAT_BUSY_MESSAGE)

        if stream_too:
            ladder.stream = stream
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(f"{tmp}/s.json"),
                          MS.MessageStore(f"{tmp}/m.json"), ladder, lambda *a: "ok",
                          system="", model="m")
        return r, calls

    def _asst(self, r, sid):
        return [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]

    def test_a_busy_round_marks_the_row_and_never_retries(self):
        import tempfile
        for stream_too in (True, False):
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(MS, "STREAM_ROUNDS", True):
                r, calls = self._runner(tmp, stream_too)
                sid = r.sessions.create(title="t")["id"]
                r.run(sid, "what does the backup job skip?")
                row = self._asst(r, sid)
                info = row["info"]
                self.assertTrue(info.get(MS.BUSY_MARK), info)
                self.assertIn("too busy", json.dumps(info.get("error")))
                text = " ".join(p.get("text") or "" for p in row["parts"] if p.get("type") == "text")
                self.assertIn("too busy to answer with its memory", text)
                self.assertNotIn("model ladder failed", text)
                # one attempt: no buffered call behind the busy stream, no retry
                self.assertEqual(calls, {"buffered": 0, "stream": 1} if stream_too
                                 else {"buffered": 1, "stream": 0})


# ---- the chat's Retry (panel.js) ---------------------------------------------

_PANEL = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"


def _node(expr):
    js = ("const M=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(_PANEL), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@unittest.skipIf(not shutil.which("node"), "node not installed")
class TestChatRetryButton(unittest.TestCase):
    def test_a_busy_answer_says_so_and_offers_retry(self):
        html = _node("M.busyHtml(%s)" % json.dumps({"id": "msg_2", MS.BUSY_MARK: True}))
        self.assertIn("too busy", html)
        self.assertIn('data-cretry="msg_2"', html)

    def test_an_answered_turn_shows_nothing(self):
        self.assertEqual(_node("M.busyHtml(%s)" % json.dumps({"id": "msg_2"})), "")

    def test_retry_resends_the_same_message(self):
        rows = [{"info": {"id": "msg_1", "role": "user"},
                 "parts": [{"type": "text", "text": "[HA user: Alice]\nwhat does the backup job skip?"},
                           {"type": "text", "text": "injected", "synthetic": True}]},
                {"info": {"id": "msg_2", "role": "assistant", MS.BUSY_MARK: True}, "parts": []}]
        self.assertEqual(_node("M.retryText(%s, 'msg_2')" % json.dumps(rows)),
                         "what does the backup job skip?")
        self.assertEqual(_node("M.retryText(%s, 'msg_9')" % json.dumps(rows)), "")


if __name__ == "__main__":
    unittest.main()
