"""#753: the babyseat side of the membrama contract (chedim/membrama PR #1,
docs/BABYSEAT_CONTRACT.md). The seat probes GET /api/membrane at boot, and
every chat call it makes to its backend says who does memory: the person's
turn the seat injected carries X-Membrane-Mode: capture (the fork only
records), the seat's own background calls carry off (never stored). Plain
Ollama ignores the header, so it always rides."""
import io
import json
import os
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lanes as L  # noqa: E402
from membrane import rag as R  # noqa: E402
from membrane import router as RT  # noqa: E402

HELLO = {"impl": "ollama-membrane", "contract": 1, "capabilities": ["tee", "recall"]}


class _B(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _out(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/membrane" and self.server.membrama:
            return self._out(HELLO)
        self._out({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.seen.append((self.path, self.headers.get("X-Membrane-Mode")))
        if self.path == "/api/chat":
            return self._out({"message": {"role": "assistant", "content": "- a\n- bbbbbbbbbb"}})
        self._out({"choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "hi"}}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def _backend(membrama=False):
    b = ThreadingHTTPServer(("127.0.0.1", 0), _B)
    b.seen, b.membrama = [], membrama
    threading.Thread(target=b.serve_forever, daemon=True).start()
    return b, f"http://127.0.0.1:{b.server_address[1]}"


class _Router:
    records = {}

    def note_outage(self, *a, **k): pass
    def note_latency(self, *a, **k): pass
    def record_spend(self, *a, **k): pass


class _Cap:
    def on_turn(self, *a, **k): pass
    def on_calls(self, *a, **k): pass


def _handler(url, path="/v1/chat/completions"):
    mem = types.SimpleNamespace(embedder=None, store=None, efield=None)
    srv = types.SimpleNamespace(membrane=mem, router=_Router(), capturer=_Cap(),
                                think=None, backend=url, cloud_keys={},
                                gate_monologue=False)
    h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
    h.server, h.wfile = srv, io.BytesIO()
    h.request_version, h.command, h.path = "HTTP/1.1", "POST", path
    h.requestline = f"POST {path} HTTP/1.1"
    h.client_address, h.close_connection, h.headers = ("127.0.0.1", 0), True, {}
    return h


class TheProbe(unittest.TestCase):
    def test_a_membrama_upstream_answers_the_probe(self):
        b, url = _backend(membrama=True); self.addCleanup(b.shutdown)
        got = R.probe_membrama(url)
        self.assertEqual(got and got.get("contract"), 1)

    def test_plain_ollama_leaves_it_unset(self):
        b, url = _backend(membrama=False); self.addCleanup(b.shutdown)
        self.assertIsNone(R.probe_membrama(url))

    def test_an_unreachable_backend_leaves_it_unset(self):
        self.assertIsNone(R.probe_membrama("http://127.0.0.1:9"))


class TheModeHeader(unittest.TestCase):
    def setUp(self):
        self.b, self.url = _backend()
        self.addCleanup(self.b.shutdown)

    def test_the_proxied_turn_carries_capture(self):
        h = _handler(self.url)
        up = {"model": "local-a", "stream": False,
              "messages": [{"role": "user", "content": "hi"}]}
        h._v1_deliver(up, json.dumps(up).encode(), "hi", None,
                      {"verdict": "escalate", "route": {"route": "local-a", "kind": "local"}},
                      "WHERE WE ARE")
        self.assertIn(("/v1/chat/completions", "capture"), self.b.seen)

    def test_the_transparent_forward_carries_capture(self):
        h = _handler(self.url)
        h._send_to_backend(json.dumps({"model": "m", "messages": []}).encode())
        self.assertEqual(self.b.seen[-1][1], "capture")

    def test_a_background_api_chat_carries_off(self):
        R._ollama_chat(self.url, "m", [{"role": "user", "content": "judge"}])
        self.assertEqual(self.b.seen[-1], ("/api/chat", "off"))

    def test_a_router_interview_carries_off(self):
        reg = RT.Registry.__new__(RT.Registry)
        reg.backend = self.url
        reg.embedder = types.SimpleNamespace(embed=lambda t: (_ for _ in ()).throw(RuntimeError()))
        reg.records = {"m": types.SimpleNamespace(name="m", imprint=[], kind="cloud")}
        reg.seed_imprints()
        self.assertEqual(self.b.seen[-1], ("/api/chat", "off"))


if __name__ == "__main__":
    unittest.main()
