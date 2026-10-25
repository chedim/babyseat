"""GET /membrane/diag/requests: exactly what the seat last sent to each model.

The last RING outbound model requests sit in RAM (never on disk), newest
first: when, which model, which lane (chat / loop / thinker / ...), the
upstream path, the body as sent, and its sizes per message and per section
(system, tools, history, injected blocks, latest user message, total).
Secrets are redacted on the way out. Localhost only, like the other diag
routes."""
import io
import json
import os
import sys
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from membrane import rag as RAG  # noqa: E402

import test_busy_timeout_431 as T431  # noqa: E402  (the fake backend + handler)
import test_claude_agent_384 as T384  # noqa: E402  (the stubbed SDK)
from test_diag import _req  # noqa: E402

DOCTRINE = "DOCTRINE: be brief and honest."
BANNER = "WHERE WE ARE (membrane): the backup job skips files over 2 GB"
ASK = "what does the backup job skip?"
TOOLS = [{"type": "function", "function": {"name": "recall", "parameters": {"type": "object"}}}]


def _body(model="local-a", ask=ASK):
    return {"model": model, "stream": True, "options": {"num_ctx": 8192, "temperature": 0.2},
            "tools": TOOLS,
            "messages": [{"role": "system", "content": DOCTRINE},
                         {"role": "user", "content": "first ask"},
                         {"role": "assistant", "content": "first reply"},
                         RAG.Injected(role="system", content=BANNER),
                         {"role": "user", "content": ask}]}


class _Case(unittest.TestCase):
    def setUp(self):
        DR.reset()
        self.addCleanup(DR.reset)


class TestRecorder(_Case):
    def test_a_recorded_request_comes_back_with_its_section_sizes(self):
        DR.record("local-a", "http://127.0.0.1:11434/v1/chat/completions", _body(), lane="chat")
        out = DR.view()
        self.assertEqual(len(out["requests"]), 1)
        r = out["requests"][0]
        self.assertEqual(r["model"], "local-a")
        self.assertEqual(r["lane"], "chat")
        self.assertEqual(r["path"], "/v1/chat/completions")
        self.assertIn("ts", r)
        self.assertEqual(r["body"]["options"]["num_ctx"], 8192)
        self.assertEqual(r["body"]["tools"], TOOLS)
        self.assertEqual([m["role"] for m in r["body"]["messages"]],
                         ["system", "user", "assistant", "system", "user"])
        s = r["sizes"]["sections"]
        self.assertEqual(s["system"]["chars"], len(DOCTRINE))
        self.assertEqual(s["injected"]["chars"], len(BANNER))
        self.assertEqual(s["latest_user"]["chars"], len(ASK))
        self.assertEqual(s["history"]["chars"], len("first ask") + len("first reply"))
        self.assertEqual(s["tools"]["chars"], len(json.dumps(TOOLS)))
        self.assertGreater(s["system"]["tokens"], 0)
        per = r["sizes"]["messages"]
        self.assertEqual(len(per), 5)
        self.assertTrue(per[3]["injected"])
        self.assertEqual(per[4]["chars"], len(ASK))
        self.assertGreater(r["sizes"]["total"]["chars"], sum(m["chars"] for m in per))
        self.assertGreater(r["sizes"]["total"]["tokens"], 0)

    def test_an_injection_inside_a_plain_message_counts_as_injected(self):
        body = {"model": "m", "messages": [{"role": "system", "content": DOCTRINE + "\n\n" + BANNER},
                                           {"role": "user", "content": ASK}]}
        DR.record("m", "http://x/v1/chat/completions", body, lane="chat", injected=BANNER)
        s = DR.view()["requests"][0]["sizes"]["sections"]
        self.assertEqual(s["injected"]["chars"], len(BANNER))
        self.assertEqual(s["system"]["chars"], len(DOCTRINE) + 2)

    def test_the_ring_keeps_five_newest_first(self):
        for i in range(8):
            DR.record(f"m{i}", "http://x/v1/chat/completions", _body(f"m{i}"), lane="chat")
        got = [r["model"] for r in DR.view()["requests"]]
        self.assertEqual(got, ["m7", "m6", "m5", "m4", "m3"])
        self.assertEqual(DR.RING, 5)

    def test_model_filter_and_n(self):
        for i in range(4):
            DR.record("a" if i % 2 else "b", "http://x/v1/chat/completions", _body(), lane="chat")
        self.assertEqual([r["model"] for r in DR.view(model="a")["requests"]], ["a", "a"])
        self.assertEqual(len(DR.view(n=1)["requests"]), 1)

    def test_the_body_is_not_reread_after_the_send(self):
        b = _body()
        DR.record("local-a", "http://x/v1/chat/completions", b, lane="chat")
        b["messages"].append({"role": "user", "content": "later"})   # the caller's list grows
        self.assertEqual(len(DR.view()["requests"][0]["body"]["messages"]), 5)


class TestRedaction(_Case):
    def test_headers_and_secret_values_are_redacted(self):
        with mock.patch.object(DR, "_known_secrets", lambda: ["seat-tok-123456789"]):
            body = _body()
            body["api_key"] = "sk-live-zzz"
            body["messages"][1] = {"role": "user", "content": "token is seat-tok-123456789 ok"}
            DR.record("m", "https://api.example.com/v1/chat/completions", body, lane="chat",
                      headers={"Authorization": "Bearer abcdef", "x-api-key": "k-1",
                               "Content-Type": "application/json"})
            out = json.dumps(DR.view())
        self.assertNotIn("abcdef", out)
        self.assertNotIn("k-1", out)
        self.assertNotIn("sk-live-zzz", out)
        self.assertNotIn("seat-tok-123456789", out)
        r = DR.view()["requests"][0]
        self.assertEqual(r["headers"]["Content-Type"], "application/json")
        self.assertEqual(r["body"]["options"]["num_ctx"], 8192)        # not a secret
        self.assertIn("token is", r["body"]["messages"][1]["content"])

    def test_bearer_and_anthropic_keys_in_text_are_masked(self):
        body = {"model": "m", "messages": [{"role": "user",
                                            "content": "use sk-ant-api03-AbCdEf123456 or Bearer xyz.987"}]}
        DR.record("m", "http://x/api/chat", body, lane="thinker")
        out = json.dumps(DR.view())
        self.assertNotIn("AbCdEf123456", out)
        self.assertNotIn("xyz.987", out)


class TestProxyHook(_Case):
    """The proxy's one upstream send (_open_backend) records what it sent."""

    def test_a_delivery_records_the_request_as_sent(self):
        b = T431._backend(0)
        self.addCleanup(b.shutdown)
        h = T431._handler(f"http://127.0.0.1:{b.server_address[1]}")
        verdict = {"verdict": "escalate", "route": {"route": "local-a", "kind": "local"}}
        up = {"model": "local-a", "stream": False,
              "messages": [{"role": "system", "content": DOCTRINE},
                           RAG.Injected(role="system", content=BANNER),
                           {"role": "user", "content": ASK}]}
        h._v1_deliver(up, b"{}", ASK, None, verdict, BANNER)
        reqs = DR.view()["requests"]
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual(r["model"], "local-a")
        self.assertEqual(r["lane"], "chat")
        self.assertEqual(r["path"], "/v1/chat/completions")
        self.assertEqual(r["body"]["messages"], b.seen[0]["messages"])   # exactly as sent
        self.assertEqual(r["sizes"]["sections"]["injected"]["chars"], len(BANNER))

    def test_a_loop_session_is_the_loop_lane(self):
        fp = types.SimpleNamespace(_sids={"loop-1": "ses_loop1"})
        srv = types.SimpleNamespace(_field_pulse=fp)
        self.assertEqual(DR.lane_of(srv, {"_turn_sid": "ses_loop1"}), "loop")
        self.assertEqual(DR.lane_of(srv, {"_turn_sid": "ses_person"}), "chat")
        self.assertEqual(DR.lane_of(srv, {}), "chat")


class TestBackgroundHook(_Case):
    def test_a_background_call_records_its_lane(self):
        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return json.dumps({"message": {"content": "ok"}}).encode()
        with mock.patch.object(RAG.urllib.request, "urlopen", lambda *a, **k: _R()):
            RAG._ollama_chat("http://127.0.0.1:11434", "granite", [{"role": "user", "content": "hm"}],
                             lane="thinker")
        r = DR.view()["requests"][0]
        self.assertEqual((r["model"], r["lane"], r["path"]), ("granite", "thinker", "/api/chat"))
        self.assertEqual(r["body"]["messages"], [{"role": "user", "content": "hm"}])


class TestClaudeHook(T384._SDKCase):
    def setUp(self):
        super().setUp()
        DR.reset()
        self.addCleanup(DR.reset)

    def test_the_sdk_prompt_and_tools_are_recorded(self):
        DR.context(lane="thinker")
        list(CA.iter_reply([{"role": "system", "content": DOCTRINE},
                            {"role": "user", "content": "first ask"},
                            {"role": "assistant", "content": "first reply"},
                            RAG.Injected(role="system", content=BANNER),
                            {"role": "user", "content": ASK}],
                           "claude-agent:sonnet", stream=False))
        r = DR.view()["requests"][0]
        self.assertEqual(r["model"], "claude-agent:sonnet")
        self.assertEqual(r["lane"], "thinker")
        self.assertEqual(r["path"], "claude-agent-sdk")
        sent = self.sdk.calls[0]["options"].kw
        self.assertEqual(r["body"]["system_prompt"], sent["system_prompt"])
        self.assertEqual(r["body"]["allowed_tools"], sent["allowed_tools"])
        self.assertIn(ASK, json.dumps(r["body"].get("blocks") or r["body"]["prompt"]))
        s = r["sizes"]["sections"]
        self.assertEqual(s["system"]["chars"], len(sent["system_prompt"]))
        self.assertGreater(s["tools"]["chars"], 0)
        self.assertGreater(s["prompt"]["chars"], 0)


class TestEndpoint(_Case):
    def setUp(self):
        super().setUp()
        from membrane import lanes as L
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        self.srv.backend = "http://127.0.0.1:9"
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def test_get_returns_the_ring_with_filters(self):
        for m in ("a", "b", "a"):
            DR.record(m, "http://x/v1/chat/completions", _body(m), lane="chat")
        code, out = _req(self.port, "/membrane/diag/requests")
        self.assertEqual(code, 200)
        self.assertEqual([r["model"] for r in out["requests"]], ["a", "b", "a"])
        code, out = _req(self.port, "/membrane/diag/requests?model=b")
        self.assertEqual([r["model"] for r in out["requests"]], ["b"])
        code, out = _req(self.port, "/membrane/diag/requests?n=2")
        self.assertEqual(len(out["requests"]), 2)
        self.assertIn("sections", out["requests"][0]["sizes"])

    def test_off_box_is_refused(self):
        DR.record("a", "http://x/v1/chat/completions", _body("a"), lane="chat")
        code, out = _req(self.port, "/membrane/diag/requests", headers={"X-Forwarded-For": "192.168.1.50"})
        self.assertEqual(code, 403)
        self.assertNotIn("requests", out)


if __name__ == "__main__":
    unittest.main()
