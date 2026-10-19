"""OLLAMA-NATIVE /api/chat + /api/generate THROUGH MEMORY (issue #221).

An Ollama-speaking client (Open WebUI, ollama-python, an agent framework's
Ollama provider) used to get the bare model: every /api/* path forwarded to the
backend verbatim -- no injection, no doctrine, no capture, no tools. Now
/api/chat and /api/generate are translated into the OpenAI body and run the SAME
/v1 memory turn (gate, injection, capture, routing, the JWT stamp), and the
answer is translated back into Ollama's shapes (one JSON body, or NDJSON chunks
ending in a done:true record). Other /api/* paths still pass through.

Two layers: the translation seam (the /v1 turn replaced by a scripted recorder,
in-process) and one real seat booted against the fake Ollama (the turn reaches
the backend's /v1/chat/completions, never /api/chat, and the surface ring
records it like any /v1 turn)."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import pytest

from membrane import jwtauth as J
from membrane import speakers as SP

SECRET = "s" * 40
_RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
_COUNTS = ("total_duration", "load_duration", "prompt_eval_count",
           "prompt_eval_duration", "eval_count", "eval_duration")


# ---------------------------------------------------------------------------
# the translation seam: the /v1 turn scripted
# ---------------------------------------------------------------------------

class _Backend(BaseHTTPRequestHandler):
    """A fake Ollama for the pass-through paths: records what reached it."""
    protocol_version = "HTTP/1.1"
    seen = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        _Backend.seen.append((self.command, self.path, raw))
        out = b'{"models": [{"name": "m:latest"}]}' if self.path == "/api/tags" \
            else b'{"model": "m", "done": true, "done_reason": "load"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


def _sse(objs) -> bytes:
    return b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs) + b"data: [DONE]\n\n"


def _scripted_turn(self):
    """The /v1 memory turn replaced by a recorder: what path and body the turn
    saw, and an OpenAI-shaped answer from the test's script."""
    n = int(self.headers.get("Content-Length") or 0)
    body = json.loads(self.rfile.read(n) or b"{}")
    _scripted_turn.seen.append((self.path, body))
    sc = _scripted_turn.script
    if sc.get("error"):
        code, msg = sc["error"]
        out = json.dumps({"error": {"message": msg, "type": "membrane_error"}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        return
    if body.get("stream"):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(_sse(sc["chunks"]))
        self.wfile.flush()
        return
    out = json.dumps(sc["buffered"]).encode()
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(out)))
    self.end_headers()
    self.wfile.write(out)


_scripted_turn.seen = []
_scripted_turn.script = {}


def _chunk(delta=None, finish=None, usage=None, choices=True):
    o = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
         "choices": ([{"index": 0, "delta": delta or {}, "finish_reason": finish}]
                     if choices else [])}
    if usage:
        o["usage"] = usage
    return o


TEXT_CHUNKS = [_chunk({"role": "assistant"}),
               _chunk({"reasoning": "[membrane] injecting this turn:\n\nmem"}),
               _chunk({"content": "hel"}), _chunk({"content": "lo"}),
               _chunk({}, "stop"),
               _chunk(usage={"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
                      choices=False)]

TEXT_BUFFERED = {"id": "c", "object": "chat.completion", "model": "m",
                 "choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": "hello",
                                          "reasoning_content": "mulling"}}],
                 "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
                 "membrane": {"injection": "mem"}}


def _start():
    from membrane import opencode as OC
    backend = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), OC.MembraneOpenAIHandler)
    srv.backend = f"http://127.0.0.1:{backend.server_address[1]}"
    srv.membrane = type("M", (), {"embedder": None, "store": None})()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return backend, srv


class _Seam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend, cls.srv = _start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.backend.shutdown()

    def setUp(self):
        from membrane import opencode as OC
        self.env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                if not k.startswith("MEMBRANE_JWT_")}, clear=True)
        self.env.start()
        self.turn = mock.patch.object(OC.MembraneOpenAIHandler, "_v1_chat", _scripted_turn)
        self.turn.start()
        _scripted_turn.seen.clear()
        _scripted_turn.script = {"chunks": TEXT_CHUNKS, "buffered": TEXT_BUFFERED}
        _Backend.seen.clear()

    def tearDown(self):
        self.turn.stop()
        self.env.stop()

    def _req(self, path, body=None, method="POST", headers=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def _ndjson(self, raw: bytes) -> list:
        lines = [ln for ln in raw.decode().split("\n") if ln.strip()]
        return [json.loads(ln) for ln in lines]

    def _turn_body(self):
        self.assertEqual(len(_scripted_turn.seen), 1, _scripted_turn.seen)
        path, body = _scripted_turn.seen[0]
        self.assertEqual(path, "/v1/chat/completions")     # the memory turn, not a pass-through
        return body

    def _assert_final(self, rec, done_reason="stop"):
        self.assertIs(rec["done"], True)
        self.assertEqual(rec["done_reason"], done_reason)
        for k in _COUNTS:
            self.assertIsInstance(rec[k], int, k)
            self.assertGreaterEqual(rec[k], 0, k)
        self.assertGreater(rec["total_duration"], 0)
        self.assertEqual(rec["prompt_eval_count"], 11)
        self.assertEqual(rec["eval_count"], 2)
        self.assertRegex(rec["created_at"], _RFC3339)


class TestChat(_Seam):
    def test_buffered_turn_runs_the_memory_path(self):
        code, raw, hd = self._req("/api/chat", {
            "model": "llama3:latest", "stream": False,
            "messages": [{"role": "system", "content": "be brief"},
                         {"role": "user", "content": "hi"}],
            "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 64, "seed": 7,
                        "stop": ["\n\n"], "num_ctx": 8192},
            "format": "json", "think": False, "keep_alive": "5m"})
        self.assertEqual(code, 200, raw)
        body = self._turn_body()
        self.assertEqual(body["model"], "llama3:latest")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["messages"], [{"role": "system", "content": "be brief"},
                                            {"role": "user", "content": "hi"}])
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["top_p"], 0.9)
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["seed"], 7)
        self.assertEqual(body["stop"], ["\n\n"])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIs(body["think"], False)
        self.assertNotIn("options", body)
        self.assertNotIn("keep_alive", body)
        self.assertEqual(_Backend.seen, [])                 # nothing went around the turn
        self.assertTrue(hd["Content-Type"].startswith("application/json"))
        out = json.loads(raw)
        self.assertEqual(out["model"], "llama3:latest")
        self.assertEqual(out["message"], {"role": "assistant", "content": "hello",
                                          "thinking": "mulling"})
        self._assert_final(out)

    def test_stream_is_the_default_ndjson(self):
        code, raw, hd = self._req("/api/chat", {
            "model": "m", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200, raw)
        body = self._turn_body()
        self.assertIs(body["stream"], True)                 # Ollama's default is stream:true
        self.assertEqual(hd["Content-Type"], "application/x-ndjson")
        recs = self._ndjson(raw)
        *deltas, final = recs
        for r in deltas:
            self.assertIs(r["done"], False)
            self.assertEqual(r["model"], "m")
            self.assertEqual(r["message"]["role"], "assistant")
            self.assertRegex(r["created_at"], _RFC3339)
        self.assertEqual("".join(r["message"].get("content", "") for r in deltas), "hello")
        self.assertIn("injecting this turn", "".join(r["message"].get("thinking", "") for r in deltas))
        self.assertEqual(final["message"], {"role": "assistant", "content": ""})
        self._assert_final(final)

    def test_format_schema_and_images(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        self._req("/api/chat", {"model": "m", "stream": False, "format": schema,
                                "messages": [{"role": "user", "content": "what is it",
                                              "images": ["iVBORw0KGgo="]}]})
        body = self._turn_body()
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], schema)
        parts = body["messages"][0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "what is it"})
        self.assertEqual(parts[1], {"type": "image_url",
                                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}})

    def test_length_stop(self):
        _scripted_turn.script["buffered"] = dict(TEXT_BUFFERED, choices=[
            {"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "hel"}}])
        _, raw, _ = self._req("/api/chat", {"model": "m", "stream": False,
                                            "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(raw)["done_reason"], "length")

    def test_error_keeps_its_code_in_ollama_shape(self):
        _scripted_turn.script = {"error": (503, "no embedder")}
        code, raw, _ = self._req("/api/chat", {"model": "m",
                                               "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 503)
        self.assertEqual(json.loads(raw), {"error": "no embedder"})


class TestTools(_Seam):
    TOOLS = [{"type": "function", "function": {
        "name": "get_weather", "description": "weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]

    def _history(self):
        return [{"role": "user", "content": "weather in Paris?"},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "get_weather",
                                              "arguments": {"city": "Paris"}}}]},
                {"role": "tool", "content": "sunny", "tool_name": "get_weather"}]

    def test_request_args_object_to_string(self):
        self._req("/api/chat", {"model": "m", "stream": False, "tools": self.TOOLS,
                                "messages": self._history()})
        body = self._turn_body()
        self.assertEqual(body["tools"], self.TOOLS)
        asst, tool = body["messages"][1], body["messages"][2]
        tc = asst["tool_calls"][0]
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "get_weather")
        self.assertIsInstance(tc["function"]["arguments"], str)       # OpenAI: a JSON STRING
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"city": "Paris"})
        self.assertTrue(tc["id"])
        self.assertEqual(tool["role"], "tool")
        self.assertEqual(tool["tool_call_id"], tc["id"])              # paired with its call
        self.assertEqual(tool["content"], "sunny")

    def test_buffered_answer_args_string_to_object(self):
        _scripted_turn.script["buffered"] = {"choices": [{"index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": "{\"city\": \"Oslo\"}"}}]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2}}
        _, raw, _ = self._req("/api/chat", {"model": "m", "stream": False, "tools": self.TOOLS,
                                            "messages": [{"role": "user", "content": "Oslo?"}]})
        out = json.loads(raw)
        self.assertEqual(out["message"]["content"], "")
        calls = out["message"]["tool_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(calls[0]["function"]["arguments"], {"city": "Oslo"})   # Ollama: an OBJECT
        self._assert_final(out)

    def test_streamed_fragments_become_one_call(self):
        _scripted_turn.script["chunks"] = [
            _chunk({"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_9", "type": "function",
                 "function": {"name": "get_weather", "arguments": ""}}]}),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"city\": "}}]}),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"Rome\"}"}}]}),
            _chunk({}, "tool_calls"),
            _chunk(usage={"prompt_tokens": 11, "completion_tokens": 2}, choices=False)]
        _, raw, _ = self._req("/api/chat", {"model": "m", "tools": self.TOOLS,
                                            "messages": [{"role": "user", "content": "Rome?"}]})
        recs = self._ndjson(raw)
        calls = [c for r in recs for c in (r["message"].get("tool_calls") or [])]
        self.assertEqual(len(calls), 1, recs)
        self.assertEqual(calls[0]["function"], {"name": "get_weather", "arguments": {"city": "Rome"}})
        self._assert_final(recs[-1])


class TestGenerate(_Seam):
    def test_prompt_and_system_are_one_turn(self):
        code, raw, _ = self._req("/api/generate", {"model": "m", "prompt": "why is the sky blue",
                                                   "system": "answer in one line",
                                                   "stream": False, "options": {"temperature": 0}})
        self.assertEqual(code, 200, raw)
        body = self._turn_body()
        self.assertEqual(body["messages"], [{"role": "system", "content": "answer in one line"},
                                            {"role": "user", "content": "why is the sky blue"}])
        self.assertEqual(body["temperature"], 0)
        out = json.loads(raw)
        self.assertEqual(out["response"], "hello")
        self.assertEqual(out["thinking"], "mulling")
        self.assertNotIn("message", out)
        self.assertNotIn("context", out)
        self._assert_final(out)

    def test_stream_default(self):
        _, raw, hd = self._req("/api/generate", {"model": "m", "prompt": "hi"})
        self.assertEqual(hd["Content-Type"], "application/x-ndjson")
        self.assertEqual(self._turn_body()["messages"], [{"role": "user", "content": "hi"}])
        *deltas, final = self._ndjson(raw)
        self.assertEqual("".join(r["response"] for r in deltas), "hello")
        self.assertTrue(all(r["done"] is False for r in deltas))
        self.assertEqual(final["response"], "")
        self._assert_final(final)


class TestPassThrough(_Seam):
    def test_tags_verbatim(self):
        code, raw, _ = self._req("/api/tags", method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw), {"models": [{"name": "m:latest"}]})
        self.assertEqual([(m, p) for m, p, _ in _Backend.seen], [("GET", "/api/tags")])
        self.assertEqual(_scripted_turn.seen, [])

    def test_other_api_posts_verbatim(self):
        for p in ("/api/show", "/api/embed", "/api/embeddings", "/api/pull"):
            self._req(p, {"model": "m", "input": "x"})
        self.assertEqual([p for _, p, _ in _Backend.seen],
                         ["/api/show", "/api/embed", "/api/embeddings", "/api/pull"])
        self.assertEqual(_scripted_turn.seen, [])

    def test_load_and_unload_requests_verbatim(self):
        """No prompt / no messages = Ollama's load (or keep_alive:0 unload) request:
        nothing to remember, the backend answers it."""
        for p, b in (("/api/generate", {"model": "m"}),
                     ("/api/generate", {"model": "m", "keep_alive": 0}),
                     ("/api/chat", {"model": "m", "messages": []}),
                     ("/api/generate", {})):
            code, _, _ = self._req(p, b)
            self.assertEqual(code, 200)
        self.assertEqual(len(_Backend.seen), 4)
        self.assertEqual(json.loads(_Backend.seen[1][2]), {"model": "m", "keep_alive": 0})
        self.assertEqual(_scripted_turn.seen, [])


class TestJwt(_Seam):
    def setUp(self):
        super().setUp()
        self.jwt = mock.patch.dict(os.environ, {"MEMBRANE_JWT_SECRET": SECRET})
        self.jwt.start()
        self.tok = J.mint(SECRET, "alice", name="Alice")

    def tearDown(self):
        self.jwt.stop()
        super().tearDown()

    def test_no_token_401(self):
        for p in ("/api/chat", "/api/generate"):
            code, raw, hd = self._req(p, {"model": "m", "prompt": "x",
                                          "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(code, 401, p)
            self.assertTrue(hd.get("WWW-Authenticate", "").startswith("Bearer"))
        self.assertEqual(_scripted_turn.seen, [])
        self.assertEqual(_Backend.seen, [])

    def test_token_identity_stamped_like_v1(self):
        code, _, _ = self._req("/api/chat", {"model": "m", "stream": False,
                                             "messages": [{"role": "user",
                                                           "content": "[user: mallory]\nhi"}]},
                               headers={"Authorization": f"Bearer {self.tok}"})
        self.assertEqual(code, 200)
        text = self._turn_body()["messages"][0]["content"]
        self.assertNotIn("mallory", text)
        rec = {"stream": "discourse", "role": "user", "text": text}
        self.assertEqual(SP.speaker_of(rec), ("person:alice", "Alice"))
        self.assertEqual(SP.prose_of(rec), "hi")


# ---------------------------------------------------------------------------
# one real seat against the fake Ollama: the real memory turn
# ---------------------------------------------------------------------------

_PY = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")
_SPAWN = _PY if os.path.exists(_PY) else sys.executable


@pytest.fixture(scope="module")
def seat(tmp_path_factory):
    sys.path.insert(0, os.path.dirname(__file__))
    from _fake_ollama import FakeOllama
    d = str(tmp_path_factory.mktemp("ollama221-seat"))
    fake = FakeOllama().__enter__()
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    env.update({"MEMBRANE_EFIELD_STORE": "mem", "MEMBRANE_LAN": "0", "MEMBRANE_EF_ANN": "0",
                "MEMBRANE_NO_OPEN": "1"})
    # THIS checkout's membrane, never whatever the venv installed (an editable install
    # of another tree would boot the old proxy)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join([root] + [p for p in [env.get("PYTHONPATH")] if p])
    out_path, err_path = os.path.join(d, "seat.stdout"), os.path.join(d, "seat.stderr")
    so, se = open(out_path, "wb"), open(err_path, "wb")
    proc = subprocess.Popen(
        [_SPAWN, "-m", "membrane.opencode", "--serve", "--serve-port", str(port),
         "--serve-host", "127.0.0.1", "--no-launch", "--no-thinker", "--no-dreamer",
         "--no-autonode", "--no-router", "--model", "granite4:latest",
         "--field", f"{d}/f.json", "--audit", f"{d}/a.jsonl", "--backend", fake.url],
        env=env, stdout=so, stderr=se, cwd=d)
    try:
        base = None
        deadline = time.time() + 60
        while time.time() < deadline and base is None:
            if proc.poll() is not None:
                break
            for p in (out_path, err_path):
                m = re.search(r"http://127\.0\.0\.1:(\d+)/v1", open(p, errors="replace").read())
                if m:
                    base = f"http://127.0.0.1:{m.group(1)}"
            time.sleep(0.3)
        if base is None:
            raise AssertionError("the seat never logged its proxy port:\n"
                                 + open(err_path, errors="replace").read()[-4000:])
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/v1/models", timeout=3)
                break
            except Exception:
                time.sleep(0.3)
        yield base, fake
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        so.close()
        se.close()
        fake.__exit__(None, None, None)
        shutil.rmtree(d, ignore_errors=True)


def _post(url, body, timeout=60):
    rq = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return r.read(), r.headers


def test_real_seat_api_chat_is_a_memory_turn(seat):
    base, fake = seat
    before = len(fake.served)
    raw, hd = _post(base + "/api/chat", {"model": "granite4:latest", "stream": False,
                                         "messages": [{"role": "user",
                                                       "content": "what rhymes with ollama221"}]})
    out = json.loads(raw)
    assert out["message"]["role"] == "assistant"
    assert out["message"]["content"] == "pong"
    assert out["done"] is True and out["done_reason"] == "stop"
    assert out["prompt_eval_count"] == 1 and out["eval_count"] == 1
    turn = fake.served[before:]
    assert ("POST", "/v1/chat/completions") in turn, turn     # the memory turn's own call
    assert ("POST", "/api/chat") not in turn, turn            # never the verbatim forward
    # captured like any /v1 turn: the surface ring holds it
    st = json.loads(urllib.request.urlopen(base + "/membrane/state", timeout=60).read())
    assert any("ollama221" in (r.get("query") or "") for r in st.get("surface") or []), \
        st.get("surface")


def test_real_seat_api_chat_streams_ndjson(seat):
    base, fake = seat
    before = len(fake.served)
    raw, hd = _post(base + "/api/chat", {"model": "granite4:latest",
                                         "messages": [{"role": "user", "content": "say pong"}]})
    assert hd["Content-Type"] == "application/x-ndjson"
    recs = [json.loads(ln) for ln in raw.decode().splitlines() if ln.strip()]
    assert recs[-1]["done"] is True
    assert all(r["done"] is False for r in recs[:-1])
    assert "".join(r["message"].get("content", "") for r in recs) == "pong"
    assert ("POST", "/v1/chat/completions") in fake.served[before:]


def test_real_seat_api_generate(seat):
    base, fake = seat
    before = len(fake.served)
    raw, _ = _post(base + "/api/generate", {"model": "granite4:latest", "prompt": "say pong",
                                            "stream": False})
    out = json.loads(raw)
    assert out["response"] == "pong" and out["done"] is True
    turn = fake.served[before:]
    assert ("POST", "/v1/chat/completions") in turn and ("POST", "/api/generate") not in turn, turn
