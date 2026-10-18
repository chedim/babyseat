"""BACKGROUND TOKEN ACCOUNTING (#200): `membrane stats` read only the
serve-msgs store, so every model call the seat made OUTSIDE a serve turn --
the thinker's deliberation, the proxy-only watch degrade, the claim judge,
the router's boot interview, /delegate -- spent tokens nobody counted:
_ollama_chat / _openai_chat kept the text and dropped the usage, and the
thinker's model_calls counter died with the process. Every background call
now tallies its usage per lane and model into <data dir>/bg-usage.json
(persisted, survives restarts), and `membrane stats` prints it beside the
foreground accounting."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from membrane import bgusage
from membrane.rag import _model_chat, _ollama_chat, _openai_chat


def _serve(payload: dict):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            out = json.dumps(payload).encode()
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


_OLLAMA = {"message": {"content": "ok"},
           "prompt_eval_count": 120, "eval_count": 30}
_OPENAI = {"choices": [{"message": {"content": "ok"}}],
           "usage": {"prompt_tokens": 200, "completion_tokens": 50,
                     "completion_tokens_details": {"reasoning_tokens": 20}}}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    return tmp_path


def _row(data_dir, lane, model):
    doc = json.loads((data_dir / "bg-usage.json").read_text())
    return doc["lanes"][lane][model]


def test_ollama_chat_tallies_its_usage(data_dir):
    url, srv = _serve(_OLLAMA)
    try:
        assert _ollama_chat(url, "qwen3:8b", [{"role": "user", "content": "x"}],
                            lane="thinker") == "ok"
    finally:
        srv.shutdown()
    assert _row(data_dir, "thinker", "qwen3:8b") == {
        "calls": 1, "input": 120, "output": 30, "reasoning": 0}


def test_openai_chat_tallies_its_usage(data_dir):
    url, srv = _serve(_OPENAI)
    try:
        assert _openai_chat(url, {}, "glm:cloud",
                            [{"role": "user", "content": "x"}],
                            lane="watch") == "ok"
    finally:
        srv.shutdown()
    assert _row(data_dir, "watch", "glm:cloud") == {
        "calls": 1, "input": 200, "output": 50, "reasoning": 20}


def test_model_chat_carries_the_lane(data_dir):
    url, srv = _serve(_OLLAMA)
    try:
        fake = type("S", (), {})()
        fake.backend = url
        _model_chat(fake)(url, "qwen3:8b", [{"role": "user", "content": "x"}],
                          lane="thinker")
    finally:
        srv.shutdown()
    assert _row(data_dir, "thinker", "qwen3:8b")["calls"] == 1


def test_an_unlabelled_call_still_counts(data_dir):
    url, srv = _serve(_OLLAMA)
    try:
        _ollama_chat(url, "qwen3:8b", [{"role": "user", "content": "x"}])
    finally:
        srv.shutdown()
    assert _row(data_dir, "background", "qwen3:8b")["input"] == 120


def test_a_failed_call_spends_nothing(data_dir):
    assert _ollama_chat("http://127.0.0.1:1", "qwen3:8b",
                        [{"role": "user", "content": "x"}], lane="thinker") == ""
    assert not (data_dir / "bg-usage.json").exists()


def test_the_tally_survives_a_restart(data_dir):
    bgusage.record("thinker", "qwen3:8b", input=10, output=2)
    bgusage.record("thinker", "qwen3:8b", input=5, output=1, reasoning=1)
    # a new process reads the same file: the totals keep accumulating
    doc = bgusage.load(data_dir / "bg-usage.json")
    assert doc["lanes"]["thinker"]["qwen3:8b"] == {
        "calls": 2, "input": 15, "output": 3, "reasoning": 1}
    assert doc["since"] <= doc["updated"]


def test_without_a_data_dir_nothing_lands_on_disk(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMBRANE_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    bgusage.record("thinker", "m", input=1)
    assert list(tmp_path.iterdir()) == []


def test_the_claim_judge_tallies(data_dir):
    from membrane.claims import OllamaClaimJudge
    url, srv = _serve(_OLLAMA)
    try:
        assert OllamaClaimJudge("qwen3:8b", base_url=url).ask("claim?") == "ok"
    finally:
        srv.shutdown()
    assert _row(data_dir, "claim-judge", "qwen3:8b")["output"] == 30


def test_the_thinker_call_is_on_the_thinker_lane(data_dir, monkeypatch):
    from membrane import thinker as th
    seen = {}

    def fake_model_chat(srv):
        def chat(bg, model, messages, temperature=0.2, timeout=120,
                 lane="background"):
            seen["lane"] = lane
            return "hi"
        return chat

    monkeypatch.setattr("membrane.rag._model_chat", fake_model_chat)
    t = th.Thinker.__new__(th.Thinker)
    t.srv = type("S", (), {"backend": "", "idle_cortex_model": "m"})()
    t._model_ts = []
    t.stats = {"model_calls": 0}
    assert t._model_call([{"role": "user", "content": "x"}]) == "hi"
    assert seen["lane"] == "thinker"


# ---- membrane stats ---------------------------------------------------------

def _stats_dir(tmp_path):
    (tmp_path / "serve-msgs.json").write_text(json.dumps({"s": [
        {"info": {"id": "a1", "sessionID": "s", "role": "assistant",
                  "tokens": {"total": 1100, "input": 1000, "output": 100,
                             "reasoning": 0,
                             "cache": {"read": 0, "write": 0}}},
         "parts": []}]}))
    return tmp_path / "serve-msgs.json"


def test_stats_prints_the_background_beside_the_store(tmp_path, monkeypatch,
                                                      capsys):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    store = _stats_dir(tmp_path)
    bgusage.record("thinker", "qwen3:8b", input=300, output=60)
    from membrane.stats import main
    assert main(["--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "background model calls" in out
    assert "thinker" in out and "qwen3:8b" in out
    assert "300" in out


def test_stats_json_carries_the_background(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    store = _stats_dir(tmp_path)
    bgusage.record("claim-judge", "m", input=7, output=3)
    from membrane.stats import main
    assert main(["--store", str(store), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["background"]["lanes"]["claim-judge"]["m"]["input"] == 7


def test_stats_says_when_nothing_was_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MEMBRANE_DATA_DIR", raising=False)
    store = _stats_dir(tmp_path)
    from membrane.stats import main
    assert main(["--store", str(store)]) == 0
    assert "background model calls: not recorded" in capsys.readouterr().out
