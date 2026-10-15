"""A FAKE OLLAMA for tests that boot a real seat (2026-10-15). The door-visibility
tests booted a seat against the operator's LIVE ollama with real models under 30 s
timeouts: whether a stored-and-announced user message passed depended on model
latency and on everything else using ollama at that moment (flaky in every full run,
green solo). What they test -- the door storing and announcing a message -- never
depended on what a model says. This answers instantly: the model list, chat
(streamed or not, ollama and OpenAI shapes), embeddings; an unknown route answers
404 and is recorded in .unknown so a test can see what the seat reached for."""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

REPLY = "pong"
MODELS = ["granite4:latest", "mxbai-embed-large:latest"]
DIM = 1024


def _vec(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256((text or "").encode()).digest()[:8], "big")
    v = np.random.default_rng(seed).standard_normal(DIM)
    return (v / np.linalg.norm(v)).round(6).tolist()


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        p = self.path.split("?")[0]
        self.server.served.append(("GET", p))
        if p == "/api/tags":
            return self._json({"models": [{"name": m, "model": m, "details": {}} for m in MODELS]})
        if p in ("/v1/models", "/models"):
            return self._json({"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
        if p in ("/", "/api/version"):
            return self._json({"version": "0.0.0-fake"})
        self.server.unknown.append(("GET", p))
        self._json({"error": "not found"}, 404)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]
        self.server.served.append(("POST", p))
        b = self._body()
        model = b.get("model") or MODELS[0]
        if p == "/api/show":
            return self._json({"modelfile": "", "details": {}, "model_info": {}, "capabilities": ["completion", "tools"]})
        if p == "/api/embed":
            inp = b.get("input")
            inp = inp if isinstance(inp, list) else [inp]
            return self._json({"model": model, "embeddings": [_vec(str(x)) for x in inp]})
        if p == "/api/embeddings":
            return self._json({"embedding": _vec(str(b.get("prompt") or ""))})
        if p in ("/api/chat", "/api/generate"):
            msg = {"role": "assistant", "content": REPLY}
            if not b.get("stream", True):
                return self._json({"model": model, "message": msg, "response": REPLY, "done": True,
                                   "done_reason": "stop", "prompt_eval_count": 1, "eval_count": 1})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk, done in ((REPLY, False), ("", True)):
                self.wfile.write((json.dumps({"model": model, "message": {"role": "assistant", "content": chunk},
                                              "response": chunk, "done": done,
                                              **({"done_reason": "stop", "eval_count": 1} if done else {})})
                                  + "\n").encode())
                self.wfile.flush()
            return
        if p in ("/v1/chat/completions", "/chat/completions"):
            if not b.get("stream"):
                return self._json({"id": "fake", "object": "chat.completion", "model": model,
                                   "choices": [{"index": 0, "finish_reason": "stop",
                                                "message": {"role": "assistant", "content": REPLY}}],
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, fin in (({"role": "assistant", "content": REPLY}, None), ({}, "stop")):
                ev = {"id": "fake", "object": "chat.completion.chunk", "model": model,
                      "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if p in ("/v1/embeddings", "/embeddings"):
            inp = b.get("input")
            inp = inp if isinstance(inp, list) else [inp]
            return self._json({"data": [{"index": i, "embedding": _vec(str(x))} for i, x in enumerate(inp)]})
        self.server.unknown.append(("POST", p))
        self._json({"error": "not found"}, 404)


class FakeOllama:
    def __enter__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.srv.unknown = []
        self.srv.served = []
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        return self

    @property
    def unknown(self):
        return self.srv.unknown

    @property
    def served(self):
        return self.srv.served

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
