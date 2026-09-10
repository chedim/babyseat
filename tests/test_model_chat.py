"""THE MODEL-AWARE CHAT DISPATCHER (the compactor's 404 fix): the
background lanes (compactor / idle synthesis / night) called _ollama_chat
with the judge's model name -- a CLOUD rung (e.g. glm-5.3-flash:cloud on
openrouter) 404s on Ollama's /api/chat forever, so every compact cycle
logged "[compact] llm call failed (HTTP Error 404)" and distilled
NOTHING. _model_chat(srv) resolves the rung: a cloud model rides the
registered provider's OpenAI-compatible chat path with its memory-only
auth headers (the same wire the chat lane uses); a local model falls
back to Ollama /api/chat."""

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from membrane.rag import _model_chat, _ollama_chat


class _Rec:
    def __init__(self, backend: str):
        self.backend = backend


class _Router:
    def __init__(self, records: dict):
        self.records = records


def _serve(handler_cls) -> tuple[str, threading.Thread]:
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{srv.server_port}", srv


class _OpenAIHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible shape + records the auth header it received."""
    seen_auth = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _OpenAIHandler.seen_auth = self.headers.get("Authorization")
        out = json.dumps({"choices": [{"message": {
            "content": "distilled note (cloud)"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _OllamaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/api/chat"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"message": {
            "content": f"distilled note (local, model={body['model']})"
        }}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_cloud_rung_rides_provider_path():
    """A cloud model's call goes to the PROVIDER's endpoint with auth --
    never to Ollama (the 404 lane)."""
    url, srv = _serve(_OpenAIHandler)
    try:
        fake = type("S", (), {})()
        fake.router = type("R", (), {})()
        fake.router.records = {
            "glm-5.3-flash:cloud": _Rec("openrouter")}
        fake.backend = "http://ollama.example:11434"
        fake.cloud_keys = {"openrouter": "sk-or-test"}

        # point the openrouter base at the local test server
        from membrane import auth as auth_mod
        orig_base = auth_mod.PROVIDERS["openrouter"]["base"]
        auth_mod.PROVIDERS["openrouter"]["base"] = url
        try:
            chat = _model_chat(fake)
            note = chat("http://ollama.example:11434",
                        "glm-5.3-flash:cloud",
                        [{"role": "user", "content": "distill"}])
        finally:
            auth_mod.PROVIDERS["openrouter"]["base"] = orig_base
        assert note == "distilled note (cloud)"
        assert _OpenAIHandler.seen_auth == "Bearer sk-or-test"
    finally:
        srv.shutdown()


def test_local_rung_stays_ollama():
    """A local model (no backend on its record) rides /api/chat."""
    url, srv = _serve(_OllamaHandler)
    try:
        fake = type("S", (), {})()
        fake.router = type("R", (), {})()
        fake.router.records = {"granite3.3:8b": _Rec("")}
        fake.backend = url
        fake.cloud_keys = {}
        note = _model_chat(fake)(url, "granite3.3:8b",
                                 [{"role": "user", "content": "distill"}])
        assert note == "distilled note (local, model=granite3.3:8b)"
    finally:
        srv.shutdown()


def test_routerless_srv_falls_back():
    """No router -> _ollama_chat semantics (the boot-before-router path)."""
    url, srv = _serve(_OllamaHandler)
    try:
        fake = type("S", (), {})()
        fake.backend = url
        assert not hasattr(fake, "router", ) or fake.router is None \
            if hasattr(fake, "router") else True
        chat = _model_chat(fake)
        note = chat(url, "granite3.3:8b",
                    [{"role": "user", "content": "distill"}])
        assert "granite3.3:8b" in note
    finally:
        srv.shutdown()


def test_cloud_failure_returns_empty_not_404_forever():
    """A cloud call that fails (bad key/endpoint) returns '' -- the lane
    degrades, it never wedges the compactor."""
    fake = type("S", (), {})()
    fake.router = type("R", (), {})()
    fake.router.records = {"glm-5.3-flash:cloud": _Rec("openrouter")}
    fake.backend = "http://127.0.0.1:1"          # nothing listens here
    fake.cloud_keys = {"openrouter": "sk-or-test"}
    from membrane import auth as auth_mod
    orig_base = auth_mod.PROVIDERS["openrouter"]["base"]
    auth_mod.PROVIDERS["openrouter"]["base"] = "http://127.0.0.1:1"
    try:
        note = _model_chat(fake)("http://127.0.0.1:1",
                                 "glm-5.3-flash:cloud",
                                 [{"role": "user", "content": "distill"}])
    finally:
        auth_mod.PROVIDERS["openrouter"]["base"] = orig_base
    assert note == ""


def test_ollama_chat_signature_unchanged():
    """The injected chat shape (backend, model, messages) is a public
    seam for entity_synthesis/night -- it must stay positional."""
    import inspect
    sig = inspect.signature(_ollama_chat)
    assert list(sig.parameters)[:3] == ["backend", "model", "messages"]
