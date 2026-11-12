"""Entity methods are being retired in favor of cliffhanger scripts (the operator, 2026-11-12): creating or editing one is
refused (the tool and the route), and every method call, each and inspect carries a note telling the model to use cliffhanger
getters and setters instead. Calling an existing method still works."""
from membrane import mcp_server as M

NOTE = M.METHODS_DEPRECATED


def _stub(monkeypatch, out):
    calls = []
    monkeypatch.setattr(M, "_http", lambda port, method, path, body=None, **k: (calls.append((method, path)), out)[1])
    return calls


def test_the_note_points_at_getters_and_setters():
    assert "DEPRECATED" in NOTE and "getter" in NOTE and "setter" in NOTE and "help('cliffhanger')" in NOTE


def test_defining_a_method_is_refused_and_nothing_is_sent(monkeypatch):
    calls = _stub(monkeypatch, {"ok": True})
    r = M.tool_method_define({"on": "Lamp", "method": "toggle", "command": "echo hi"}, 1)
    assert r.startswith("method_define is disabled") and NOTE in r
    assert calls == []


def test_every_way_to_define_is_refused(monkeypatch):
    calls = _stub(monkeypatch, {"ok": True})
    from membrane import toolset as T
    name, args = T.resolve("exec", {"action": "define", "on": "Lamp", "method": "m", "command": "echo"})
    assert name == "method_define"
    assert M.TOOL_IMPLS["method_define"]({"on": "Lamp", "method": "m"}, 1).startswith("method_define is disabled")
    assert calls == []


def test_a_method_call_still_runs_and_carries_the_note(monkeypatch):
    out = {"object": "Lamp:desk", "status": "ok", "method": "toggle", "via": "Lamp", "mro": ["Lamp"], "rc": 0, "outcome": "ok", "head": "done"}
    calls = _stub(monkeypatch, out)
    r = M.tool_method_call({"object": "Lamp:desk", "method": "toggle", "args": {}}, 1)
    assert "rc=0" in r and r.endswith(NOTE)
    assert calls == [("POST", "/membrane/method/call")]
    r2 = M.tool_method_call({"object": "Lamp:desk", "method": "toggle", "args": ["x"]}, 1)
    assert r2.endswith(NOTE)


def test_a_refused_call_carries_it_too(monkeypatch):
    _stub(monkeypatch, {"status": "blocked", "error": "no such method"})
    assert M.tool_method_call({"object": "x", "method": "y"}, 1).endswith(NOTE)


def test_each_and_inspect_carry_it(monkeypatch):
    _stub(monkeypatch, {"status": "ok", "method": "m", "over": "Lamp", "n": 1, "n_ok": 1, "ran": 1, "results": [{"object": "Lamp:a", "status": "ok"}]})
    assert M.tool_method_each({"over": "Lamp", "method": "m"}, 1).endswith(NOTE)
    _stub(monkeypatch, {"resolved": "Lamp"})
    assert M.tool_method_inspect({"object": "Lamp", "method": "m"}, 1).endswith(NOTE)


def test_a_method_reached_through_call_carries_it(monkeypatch):
    _stub(monkeypatch, {"ok": True, "status": "ok", "object": "Lamp:desk", "resolved": {"object": "Lamp:desk", "level": "chat"}, "rc": 0, "outcome": "ok", "head": "x"})
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    assert M.tool_dunder_call({"method": "toggle", "args": {}}, 1).endswith(NOTE)


import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
from membrane import lanes as L  # noqa: E402
from tests.test_jwt_auth import _jwt_env  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402
from tests.test_restart_while_paused_388 import _seat  # noqa: E402


class TestTheRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(self.tmp.name, "seat-token"), MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        self.srv = _seat(self.httpd, self.tmp.name)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def test_the_define_route_refuses_and_defines_nothing(self):
        code, b, _ = _req(self.httpd.server_address[1], "/membrane/method/define", "POST",
                          {"on": "Lamp", "method": "toggle", "command": "echo hi"})
        out = json.loads(b or b"{}")
        self.assertFalse(out.get("ok"), out)
        self.assertIn("method_define is disabled", out.get("error", ""))
        self.assertIn("getter", out.get("error", ""))

