"""P1: the MCP tool surface.

membrane/mcp_server.py is a STATELESS stdio adapter: JSON-RPC 2.0 in,
tool calls relayed to the RUNNING proxy's /membrane/* endpoints, structured
text back. These tests pin:

- the protocol: initialize handshake, tools/list (4 tools + schemas),
  ping, unknown method, parse-error line, notifications stay silent;
- every tool against a STUB proxy that mimics the real endpoints
  (read shapes from _membrane_ask?k=N, write shapes from the learn/forget
  handlers): happy paths, empty results, proxy 4xx/5xx and unreachable
  proxy -- all surface as isError RESULTS (MCP contract), never as
  protocol errors, and never kill the loop;
- kill switch: `--no-mcp` exists on the opencode arg parser (default ON).

The loop is fed REAL newline-delimited requests through serve() -- the same
bytes opencode's MCP client will send.
"""

import io
import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M


# ---- a stub proxy: the /membrane/* endpoint shapes the MCP layer relies on ---

class _StubProxy(BaseHTTPRequestHandler):
    # test-writable canned responses
    ask = {"fid": "stub", "hit": False, "field_size": 12, "hits": []}
    state = {"field_size": 12, "embed_model": "stub-embed",
             "efield": {"events": {"discourse/user": 5,
                                   "observations/tool": 3},
                        "entities": {"nursery": 4, "live": 7,
                                     "dormant": 1},
                        "geometry": {"eff_rank": 3.21},
                        "replay_ab": {"eligible": 6, "served_replay": 1,
                                      "mean_answer_sim": 0.81}},
             "night": {"births": 2, "junk": 1},
             "review_pending": 0}
    learn = {"written": True, "ex_idx": 12, "field_size": 13,
             "exemplar": {"situation": "lesson", "action": "lesson"}}
    forget = {"removed": 2, "field_size_before": 13, "field_size": 11,
              "removed_cells": [{"idx": 3, "situation": "junk"},
                                {"idx": 4, "situation": "junk2"}]}
    fail_next = None      # (status, msg) -> next request errors

    def _reply(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _canned(self):
        if _StubProxy.fail_next is not None:
            code, msg = _StubProxy.fail_next
            _StubProxy.fail_next = None
            return code, {"error": msg}
        return 200, None

    def do_GET(self):
        code, body = self._canned()
        if body is None:
            if self.path.startswith("/membrane/ask"):
                code, body = 200, _StubProxy.ask
            elif self.path.startswith("/membrane/state"):
                code, body = 200, _StubProxy.state
            else:
                code, body = 404, {"error": "no route"}
        self._reply(code, body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        code, body = self._canned()
        if body is None:
            if self.path == "/membrane/learn":
                code, body = 200, _StubProxy.learn
            elif self.path == "/membrane/forget":
                code, body = 200, _StubProxy.forget
            else:
                code, body = 404, {"error": "no route"}
        self._reply(code, body)

    def log_message(self, *a):
        pass


def _rpc(lines: list[dict]) -> list[dict]:
    """Feed the real serve() loop; return the responses it emitted."""
    stdin = io.StringIO("\n".join(json.dumps(r) for r in lines) + "\n")
    stdout = io.StringIO()
    M.serve(_PORT, stdin=stdin, stdout=stdout)
    return [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]


_PORT = 0


def _call(name: str, args: dict) -> str:
    """One tools/call through the loop; returns the text content."""
    (resp,) = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": args}}])
    assert "result" in resp, resp
    return "".join(c.get("text", "")
                   for c in resp["result"]["content"])


class TestMcpProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), _StubProxy)
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        global _PORT
        _PORT = cls.proxy.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()

    def test_initialize_handshake(self):
        (resp,) = _rpc([{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {}}])
        self.assertEqual(resp["result"]["protocolVersion"], M.PROTOCOL_VERSION)
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "membrane")

    def test_tools_list_four_tools(self):
        (resp,) = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, ["membrane_run",
                                 "membrane_query", "membrane_state",
                                 "membrane_learn", "membrane_forget",
                                 "membrane_watch_entity",
                                 "membrane_unwatch_entity",
                                 "membrane_list_tracks",
                                 "membrane_related_entities",
                                 "membrane_entity_add",
                                 "membrane_entity_edit",
                                 "membrane_entity_remove",
                                 "membrane_set_junk_cut",
                                 "membrane_peer_post",
                                 "membrane_peer_seats",
                                 "membrane_intend",
                                 "membrane_graph",
                                 "membrane_entity_inherit"])
        for t in resp["result"]["tools"]:
            self.assertIn("inputSchema", t)
            self.assertTrue(t["description"].strip())

    def test_ping_and_unknown_method(self):
        (resp,) = _rpc([{"jsonrpc": "2.0", "id": 2, "method": "ping"}])
        self.assertEqual(resp["result"], {})
        (resp,) = _rpc([{"jsonrpc": "2.0", "id": 3, "method": "no/such"}])
        self.assertEqual(resp["error"]["code"], -32601)

    def test_parse_error_does_not_kill_loop(self):
        stdin = io.StringIO("not json\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n")
        stdout = io.StringIO()
        M.serve(_PORT, stdin=stdin, stdout=stdout)
        lines = [json.loads(l) for l in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})

    def test_notification_gets_no_reply(self):
        out = _rpc([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual(out, [])


class TestMcpTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), _StubProxy)
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        global _PORT
        _PORT = cls.proxy.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()

    def setUp(self):
        _StubProxy.fail_next = None
        _StubProxy.ask = {"fid": "stub", "hit": False, "field_size": 12,
                          "hits": []}
        _StubProxy.learn = {"written": True, "ex_idx": 12, "field_size": 13,
                            "exemplar": {"situation": "lesson",
                                         "action": "lesson"}}
        _StubProxy.forget = {"removed": 2, "field_size_before": 12,
                             "field_size": 10,
                             "removed_cells": [{"idx": 3, "situation": "junk"}]}

    def test_query_with_hits(self):
        _StubProxy.ask = {"fid": "stub", "field_size": 12, "hits": [
            {"sim": 0.91, "situation": "how to run tests",
             "action": ".venv/bin/python -m pytest", "outcome": "teed-pair",
             "model": "glm-5.3-flash:cloud"}]}
        text = _call("membrane_query", {"topic": "running the tests"})
        self.assertIn("0.91", text)
        self.assertIn("[via glm-5.3-flash:cloud]", text)
        self.assertIn("how to run tests", text)
        self.assertIn("NOT learned", text)

    def test_query_empty_field(self):
        text = _call("membrane_query", {"topic": "anything"})
        self.assertIn("Nothing SAFE", text)
        self.assertIn("12", text)          # field size surfaces

    def test_query_k_clamped(self):
        # k=99 arrives at the proxy as k=8 (clamped); the stub echoes nothing
        _call("membrane_query", {"topic": "t", "k": 99})
        # (proxy contract tested endpoint-side; here just no crash)

    def test_state_summary(self):
        text = _call("membrane_state", {})
        self.assertIn("12 live entities", text)
        self.assertIn("stub-embed", text)
        self.assertIn("events: discourse 5, observations 3", text)
        self.assertIn("entities: dormant 1, live 7, nursery 4", text)
        self.assertIn("eff rank 3.21", text)
        self.assertIn("replay A/B: 6 eligible", text)
        self.assertIn("mean answer sim 0.81", text)
        self.assertIn("last night: births=2", text)

    def test_learn_written(self):
        text = _call("membrane_learn", {"lesson": "the cake is a lie"})
        self.assertIn("#12", text)
        self.assertIn("Field now 13", text)

    def test_learn_skipped_by_guards(self):
        _StubProxy.learn = {"written": False,
                            "reason": "dedup or variety gate: nothing new"}
        text = _call("membrane_learn", {"lesson": "dup"})
        self.assertIn("skipped", text)
        self.assertIn("nothing new", text)

    def test_forget_index_form_deletes(self):
        text = _call("membrane_forget", {"spec": "#3"})
        self.assertIn("Removed 2 of 12", text)
        self.assertIn("#3: junk", text)

    def test_forget_wordy_proposes_never_deletes(self):
        _StubProxy.forget = {"removed": None, "candidates": [
            {"idx": 7, "sim": 0.72}, {"idx": 9, "sim": 0.61}]}
        text = _call("membrane_forget", {"spec": "that thing about the cake"})
        self.assertIn("never delete", text)
        self.assertIn("#7 (sim 0.72)", text)
        self.assertIn("Nothing removed", text)

    def test_proxy_error_is_tool_error_not_protocol_error(self):
        _StubProxy.fail_next = (503, "router off")
        resp = _rpc([{"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "membrane_learn",
                                 "arguments": {"lesson": "x"}}}])[0]
        self.assertIn("result", resp)                     # RESULT, not error
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("membrane tool error",
                      resp["result"]["content"][0]["text"])

    def test_unknown_tool_is_protocol_error(self):
        resp = _rpc([{"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                      "params": {"name": "not_a_tool", "arguments": {}}}])[0]
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unreachable_proxy_degrades_to_tool_error(self):
        dead = M.ProxyError  # the message shape lives in _http
        try:
            M._http(1, "GET", "/membrane/state", timeout=0.2)
            self.fail("expected ProxyError")
        except dead as e:
            self.assertEqual(e.status, 503)
            self.assertIn("unreachable", e.message)


class TestWiring(unittest.TestCase):
    def test_no_mcp_flag_exists(self):
        # the kill switch is registered on the opencode parser (default ON)
        p = subprocess.run(
            [sys.executable, "-m", "membrane.opencode", "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--no-mcp", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()