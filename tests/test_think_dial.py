"""THE THINK DIAL (2026-10-15, the always-reasoning card): glm cloud cards
reason INHERENTLY -- a live measure had think:false reasoning 443 chars on a
'reply ok' prompt and think:true doubling the completion tokens to 3.1k
with a 12-35s wall -- but other cards (qwen3, R1's distills) DO stop on
think:false. The seat forwards the payload verbatim today, so the operator
has no say in WHY the backend reasons. The dial: `--think on|off|auto`
(auto = the default = forward verbatim: a non-thinking card never learns a
think:true it 400s on). The dial is a DEFAULT, not an override -- the
client's own think always wins."""
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from membrane.rag import _ollama_chat, _openai_chat, _model_chat, \
    apply_think_dial  # noqa: E402


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{srv.server_port}", srv


class _CaptureHandler(BaseHTTPRequestHandler):
    seen = {}                       # path -> last body dict

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _CaptureHandler.seen[self.path] = body
        out = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _Srv:
    def __init__(self, backend="", think=None):
        self.backend = backend
        self.think = think


class _Router:
    def __init__(self):
        self.records = {}


class TestApplyThinkDial:
    """The pure injector (the turn lane's seam): default verbatim; the
    client's think wins; on/off injects."""

    def test_auto_leaves_the_body_verbatim(self):
        up = {"model": "m", "messages": []}
        assert apply_think_dial(up, None) is up

    def test_on_injects_true(self):
        out = apply_think_dial({"model": "m"}, True)
        assert out["think"] is True and out["model"] == "m"

    def test_off_injects_false(self):
        assert apply_think_dial({"model": "m"}, False)["think"] is False

    def test_the_clients_own_think_wins(self):
        up = {"model": "m", "think": False}
        assert apply_think_dial(up, True) is up
        assert up["think"] is False


class TestTheBackgroundLanes:
    """The autonomous calls (idle judge, synthesis, the night) get the
    dial from srv too."""

    def test_ollama_chat_think_on_rides_the_body(self):
        url, srv = _serve(_CaptureHandler)
        _ollama_chat(url, "glm-5.3-flash:cloud",
                     [{"role": "user", "content": "hi"}], timeout=10,
                     think=True)
        assert _CaptureHandler.seen["/api/chat"]["think"] is True

    def test_ollama_chat_default_is_verbatim(self):
        url, srv = _serve(_CaptureHandler)
        _ollama_chat(url, "m", [{"role": "user", "content": "hi"}],
                     timeout=10)
        assert "think" not in _CaptureHandler.seen["/api/chat"]

    def test_openai_chat_think_off_rides_the_body(self):
        url, srv = _serve(_CaptureHandler)
        _openai_chat(url + "/v1/chat/completions", {}, "m",
                     [{"role": "user", "content": "hi"}], timeout=10,
                     think=False)
        assert _CaptureHandler.seen["/v1/chat/completions"]["think"] is False

    def test_model_chat_reads_srv_think(self):
        url, srv = _serve(_CaptureHandler)
        fake = _Srv(backend=url, think=True)
        chat = _model_chat(fake)
        chat("", "m", [{"role": "user", "content": "hi"}], timeout=10)
        assert _CaptureHandler.seen["/api/chat"]["think"] is True


class TestTheFlag:
    """The flag surface: the mapper and the parser (they must agree)."""

    def test_think_value_maps_the_dial(self):
        from membrane.rag import think_value
        assert think_value("on") is True
        assert think_value("off") is False
        assert think_value("auto") is None
        assert think_value(None) is None
        assert think_value("") is None
        assert think_value("bogus") is None      # unrecognized = dial silent

    def test_main_parser_carries_the_dial(self):
        from membrane import opencode as OC
        ap = OC._build_ap()
        assert ap.parse_args([]).think == "auto"
        assert ap.parse_args(["--think", "off"]).think == "off"
        assert ap.parse_args(["--think", "on"]).think == "on"