"""THE IDLE-CORTEX PICKER (the compactor's second 404 fix): the idle
lane's default was a hardcoded 'granite3.3:8b' -- on backends that never
had that model, every compactor cycle called /api/chat and 404'd
forever, distilling NOTHING (one dead cycle per idle period). The
picker resolves a LOCAL chat-capable model from ollama's LIVE tag list;
cloud rungs are excluded (a background seat must never burn cloud
tokens -- the idle-cortex split's covenant); non-chat models are
screened; the preferred/boot model wins when present and local."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from membrane.opencode import _pick_idle_cortex_model


def _tags_server(models: list):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            out = json.dumps({"models": models}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", srv


def _tag(name, size=4_000_000_000, caps=("completion", "tools")):
    d = {"name": name, "size": size}
    if caps is not None:
        d["capabilities"] = list(caps)
    return d


def test_prefers_granite_then_chat_rung_order():
    url, srv = _tags_server([
        _tag("qwen3:8b"), _tag("granite4:latest"), _tag("whisperx:latest")])
    try:
        assert _pick_idle_cortex_model(url, None) == "granite4:latest"
    finally:
        srv.shutdown()


def test_preferred_local_wins():
    url, srv = _tags_server([
        _tag("qwen3:8b"), _tag("glm-4:9b")])
    try:
        assert _pick_idle_cortex_model(url, "glm") == "glm-4:9b"
        assert _pick_idle_cortex_model(url, "qwen3:8b") == "qwen3:8b"
    finally:
        srv.shutdown()


def test_cloud_rungs_excluded():
    """The covenant: the idle lane NEVER lands on a cloud rung."""
    url, srv = _tags_server([
        _tag("glm-5.3-flash:cloud", size=0),
        _tag("deepseek-v4-pro:cloud", size=0, caps=None),
        _tag("granite4:latest")])
    try:
        assert _pick_idle_cortex_model(url, None) == "granite4:latest"
    finally:
        srv.shutdown()


def test_non_chat_excluded():
    url, srv = _tags_server([
        _tag("mxbai-embed-large:latest", caps=("embedding",)),
        _tag("all-minilm:latest", caps=()),
        _tag("qwen2.5-coder:7b")])
    try:
        assert _pick_idle_cortex_model(url, None) == "qwen2.5-coder:7b"
    finally:
        srv.shutdown()


def test_dead_backend_returns_fallback():
    """Ollama unreachable -> the old fallback (degrade, never raise)."""
    assert _pick_idle_cortex_model("http://127.0.0.1:1", "granite3.3:8b") \
        == "granite3.3:8b"


def test_no_candidates_returns_fallback():
    url, srv = _tags_server([
        _tag("gpt-oss:20b-cloud", size=0, caps=None)])
    try:
        assert _pick_idle_cortex_model(url, None) == "granite3.3:8b"
    finally:
        srv.shutdown()
