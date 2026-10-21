"""THE CLAUDE-AGENT RUNG (issue #384): Claude through the Claude Agent SDK
(the local `claude` CLI and its login), with its replies kept out of what
membrane learns.

- the provider `claude-agent` sits beside the others in auth.PROVIDERS; a
  turn routed to one of its rungs goes to the SDK's query() (stubbed here --
  the real CLI is never spawned) and comes back as an ordinary OpenAI
  response, streamed or not;
- Claude Code's own tools are off (no built-in tools, no allowed tools, no
  MCP servers, no settings files): the rung is text-only;
- opt-in: the router never escalates to it unless pinned or added to the
  ladder; background lanes never use it;
- learned: the person's side; never Claude's reply text (field capture,
  claims/night input, the voice, absorb). Shown: the transcript keeps the
  reply, marked Claude-made by provider, not by model name;
- a missing SDK or CLI is a clear error.
"""
import asyncio
import io
import json
import os
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import auth as A  # noqa: E402
from membrane import claude_agent as CA  # noqa: E402
from membrane import router as R  # noqa: E402
from membrane import server as MS  # noqa: E402


# ---- the stubbed SDK -------------------------------------------------------

class StreamEvent:
    def __init__(self, event):
        self.event = event
        self.uuid = "u"
        self.session_id = "s"


class TextBlock:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, content, model="claude-sonnet", error=None):
        self.content = content
        self.model = model
        self.error = error


class ResultMessage:
    def __init__(self, is_error=False, result=None, usage=None):
        self.subtype = "error" if is_error else "success"
        self.is_error = is_error
        self.result = result
        self.usage = usage or {"input_tokens": 11, "output_tokens": 3}


def _delta(text):
    return StreamEvent({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": text}})


def fake_sdk(words=("Hello ", "world"), error=None):
    """A stand-in claude_agent_sdk module: records every query() call."""
    mod = types.ModuleType("claude_agent_sdk")
    mod.calls = []

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.kw = kw

    class CLINotFoundError(Exception):
        pass

    async def query(*, prompt, options=None, transport=None):
        mod.calls.append({"prompt": prompt, "options": options})
        if error is not None:
            raise error
        if getattr(options, "include_partial_messages", False):
            for w in words:
                yield _delta(w)
        yield AssistantMessage([TextBlock("".join(words))])
        yield ResultMessage()

    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.CLINotFoundError = CLINotFoundError
    mod.query = query
    return mod


class _SDKCase(unittest.TestCase):
    """Every test runs against a stubbed SDK and a CLI that 'exists'."""

    words = ("Hello ", "world")

    def setUp(self):
        self.sdk = fake_sdk(self.words)
        self._p = [mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()


# ---- the provider row ------------------------------------------------------

class TestProviderRow(unittest.TestCase):
    def test_claude_agent_is_a_provider_without_an_api(self):
        self.assertNotIn("claude-agent", A.PROVIDERS)     # those are HTTPS APIs
        row = A.AGENT_PROVIDERS["claude-agent"]
        self.assertEqual(row.get("kind"), "agent-sdk")
        self.assertFalse(row.get("base"))          # never api.anthropic.com
        self.assertEqual(CA.PROVIDER, "claude-agent")

    def test_rung_names_are_provider_namespaced(self):
        names = CA.rung_names()
        self.assertIn("claude-agent:sonnet", names)
        self.assertTrue(all(CA.is_rung_name(n) for n in names))
        self.assertEqual(CA.sdk_model("claude-agent:opus"), "opus")
        self.assertFalse(CA.is_rung_name("claude-sonnet-4"))   # a name is not the provider


# ---- options: the tools are off -------------------------------------------

class TestOptions(_SDKCase):
    def test_tools_off_and_isolated(self):
        list(CA.iter_reply([{"role": "system", "content": "be brief"},
                            {"role": "user", "content": "hi"}],
                           "claude-agent:sonnet", stream=False))
        opts = self.sdk.calls[0]["options"]
        self.assertEqual(opts.kw["tools"], [])             # no built-in tools
        self.assertEqual(opts.kw["allowed_tools"], [])     # nothing auto-allowed
        self.assertEqual(opts.kw["mcp_servers"], {})       # no MCP surface of its own
        self.assertTrue(opts.kw["strict_mcp_config"])
        self.assertEqual(opts.kw["setting_sources"], [])   # no settings / CLAUDE.md
        self.assertEqual(opts.kw["max_turns"], 1)
        self.assertEqual(opts.kw["model"], "sonnet")
        self.assertIn("be brief", opts.kw["system_prompt"])

    def test_system_prompt_and_history_are_handed_over(self):
        msgs = [{"role": "system", "content": "DOCTRINE"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "system", "content": "CONTEXT BLOCK"},
                {"role": "user", "content": [{"type": "text", "text": "second question"}]}]
        system, prompt = CA.split_messages(msgs)
        self.assertIn("DOCTRINE", system)
        self.assertIn("CONTEXT BLOCK", system)
        self.assertIn("first question", prompt)
        self.assertIn("first answer", prompt)
        self.assertTrue(prompt.rstrip().endswith("second question"), prompt)

    def test_a_single_user_message_is_the_prompt(self):
        system, prompt = CA.split_messages([{"role": "user", "content": "just this"}])
        self.assertEqual(system, "")
        self.assertEqual(prompt, "just this")

    def test_stream_yields_deltas(self):
        got = list(CA.iter_reply([{"role": "user", "content": "hi"}],
                                 "claude-agent:sonnet", stream=True))
        texts = [v for k, v in got if k == "text"]
        self.assertEqual(texts, ["Hello ", "world"])     # deltas, not the whole twice
        self.assertTrue(self.sdk.calls[0]["options"].kw["include_partial_messages"])
        usage = [v for k, v in got if k == "usage"]
        self.assertEqual(usage[0]["completion_tokens"], 3)

    def test_buffered_yields_the_whole_text(self):
        got = list(CA.iter_reply([{"role": "user", "content": "hi"}],
                                 "claude-agent:sonnet", stream=False))
        self.assertEqual("".join(v for k, v in got if k == "text"), "Hello world")


# ---- missing pieces: clear errors -----------------------------------------

class TestMissing(unittest.TestCase):
    def test_missing_sdk_says_how_to_install(self):
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": None}):
            with self.assertRaises(CA.Unavailable) as cm:
                CA.check_available()
        self.assertIn("claude-agent", str(cm.exception))
        self.assertIn("pip install", str(cm.exception))

    def test_missing_cli_says_install_and_log_in(self):
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk()}), \
                mock.patch.object(CA, "find_cli", lambda: None):
            with self.assertRaises(CA.Unavailable) as cm:
                CA.check_available()
        self.assertIn("claude", str(cm.exception))
        self.assertIn("not found", str(cm.exception))

    def test_cli_status_reads_the_login(self):
        out = json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                          "subscriptionType": "max"})
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout=out, stderr=""))
        st = CA.cli_status(run=run, cli="/usr/bin/claude")
        self.assertTrue(st["installed"])
        self.assertTrue(st["logged_in"])
        self.assertEqual(st["method"], "claude.ai")
        argv = run.call_args[0][0]
        self.assertEqual(argv[1:], ["auth", "status", "--json"])

    def test_cli_status_missing_cli(self):
        with mock.patch.object(CA, "find_cli", lambda: None):
            st = CA.cli_status(run=mock.Mock(side_effect=AssertionError("never run")))
        self.assertFalse(st["installed"])
        self.assertFalse(st["logged_in"])

    def test_auth_command_checks_and_stores_nothing_secret(self):
        from membrane import cli as C
        home = Path(self._tmp())
        st = {"installed": True, "logged_in": True, "method": "claude.ai",
              "cli": "/usr/bin/claude", "error": ""}
        with mock.patch.object(CA, "cli_status", lambda **k: st), \
                mock.patch.object(Path, "home", lambda: home), \
                mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk()}):
            rc = C._auth(["claude-agent"])
        self.assertEqual(rc, 0)
        store = json.loads((home / ".membrane" / "auth.json").read_text())
        self.assertEqual(store.get("claude-agent"), CA.ENABLED_MARK)
        self.assertTrue(A.has_key("claude-agent", home=home, environ={}))

    def test_auth_command_refuses_without_login(self):
        from membrane import cli as C
        home = Path(self._tmp())
        st = {"installed": True, "logged_in": False, "method": "",
              "cli": "/usr/bin/claude", "error": ""}
        with mock.patch.object(CA, "cli_status", lambda **k: st), \
                mock.patch.object(Path, "home", lambda: home), \
                mock.patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk()}):
            rc = C._auth(["claude-agent"])
        self.assertNotEqual(rc, 0)
        self.assertFalse((home / ".membrane" / "auth.json").exists())

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d


# ---- the router: opt-in only ----------------------------------------------

def _registry(opt_in=()):
    reg = R.Registry.__new__(R.Registry)
    reg.backend = "http://x"
    reg.embedder = None
    reg.state_path = None
    reg.explore = 0.0
    reg.cloud_budget = None
    reg.prefer_local = False
    reg.max_local_gib = 12.0
    reg.spend_today, reg.spend_day = 0.0, "x"
    reg.records = {}
    reg.resident = set()
    reg._lock = threading.Lock()
    import random
    reg._rng = random.Random(0)
    reg.calibration = []
    reg._last_pressure = 0.0
    reg._seed_started = True
    reg._outages = {}
    reg.opt_in = set(opt_in)
    reg.add_cloud_models(["paid-cloud"], provider="openrouter", price=2.0)
    CA.register_rungs(reg)
    return reg


class TestRouterOptIn(unittest.TestCase):
    def test_autoselect_never_picks_claude_agent(self):
        reg = _registry()
        for r in reg.records.values():
            if r.backend == CA.PROVIDER:
                r.price_override = 0.0      # even when it looks cheapest
        rec, _ = reg.route(None, ceiling=R.AUTO_CLOUD)
        self.assertEqual(rec.name, "paid-cloud")
        names = [r.name for r in reg.ladder(ceiling=R.AUTO_CLOUD)]
        self.assertFalse(any(CA.is_rung_name(n) for n in names), names)
        names = [r.name for r in reg.ladder()]
        self.assertFalse(any(CA.is_rung_name(n) for n in names), names)

    def test_the_operator_pin_holds(self):
        reg = _registry()
        rec, info = reg.route(None, ceiling="claude-agent:sonnet")
        self.assertEqual(rec.name, "claude-agent:sonnet")
        self.assertIn("operator pick", info["reason"])

    def test_added_to_the_ladder_it_rides(self):
        reg = _registry(opt_in={"claude-agent:haiku"})
        names = [r.name for r in reg.ladder(ceiling=R.AUTO_CLOUD)]
        self.assertIn("claude-agent:haiku", names)
        self.assertNotIn("claude-agent:opus", names)

    def test_rungs_are_text_only(self):
        reg = _registry()
        rec = reg.records["claude-agent:sonnet"]
        self.assertEqual(rec.backend, CA.PROVIDER)
        self.assertNotIn("tools", rec.caps)


# ---- background lanes never use it ----------------------------------------

class TestBackgroundNever(_SDKCase):
    def test_model_chat_refuses_the_rung(self):
        from membrane.rag import _model_chat
        srv = types.SimpleNamespace(router=_registry(), backend="http://x", think=None)
        out = _model_chat(srv)("", "claude-agent:sonnet",
                               [{"role": "user", "content": "distill"}])
        self.assertEqual(out, "")
        self.assertEqual(self.sdk.calls, [])

    def test_only_a_person_lane_may_take_it(self):
        self.assertTrue(CA.person_agent("build"))
        self.assertTrue(CA.person_agent("plan"))
        self.assertTrue(CA.person_agent(None))
        self.assertFalse(CA.person_agent("watch:abc"))
        self.assertFalse(CA.person_agent("loop-3"))
        self.assertFalse(CA.person_agent("field-call"))
        self.assertFalse(CA.person_agent("thinker"))

    def test_a_loop_session_pinned_to_it_falls_back(self):
        srv = types.SimpleNamespace(router=_registry())
        self.assertEqual(CA.guard_turn_model(srv, "claude-agent:sonnet", "watch:x", "m"), "m")
        self.assertEqual(CA.guard_turn_model(srv, "claude-agent:sonnet", "build", "m"),
                         "claude-agent:sonnet")
        self.assertEqual(CA.guard_turn_model(srv, "paid-cloud", "watch:x", "m"), "paid-cloud")


# ---- the proxy turn: shown, never learned ---------------------------------

class _Cap:
    def __init__(self):
        self.turns = []
        self.calls = []

    def on_turn(self, query, prose="", thought="", **k):
        self.turns.append((query, prose, thought))

    def on_calls(self, query, calls, **k):
        self.calls.append((query, calls))


def _handler(srv):
    from membrane import lanes as L
    h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
    h.server = srv
    h.wfile = io.BytesIO()
    h.request_version = "HTTP/1.1"
    h.requestline = "POST /v1/chat/completions HTTP/1.1"
    h.command = "POST"
    h.path = "/v1/chat/completions"
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = True
    return h


def _srv():
    mem = types.SimpleNamespace(embedder=None, store=None, efield=None)
    return types.SimpleNamespace(membrane=mem, router=_registry(), capturer=_Cap(),
                                 think=None, backend="http://127.0.0.1:9",
                                 cloud_keys={}, gate_monologue=False)


PERSON = "what does the boat's battery say?"


class TestProxyTurn(_SDKCase):
    def _deliver(self, stream):
        srv = _srv()
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": stream,
              "messages": [{"role": "system", "content": "doctrine"},
                           {"role": "user", "content": PERSON}],
              "tools": [{"type": "function", "function": {"name": "exec"}}]}
        verdict = {"verdict": "escalate", "route": {"route": "claude-agent:sonnet",
                                                    "kind": "cloud"}}
        h._v1_deliver(up, b"{}", PERSON, None, verdict, None)
        return srv, h.wfile.getvalue()

    def test_streamed_turn_shows_the_reply_and_learns_only_the_person(self):
        srv, raw = self._deliver(stream=True)
        self.assertIn(b"200", raw.split(b"\r\n", 1)[0])
        body = raw.split(b"\r\n\r\n", 1)[1]
        self.assertIn(b"data: [DONE]", body)
        chunks = [json.loads(ln[5:]) for ln in body.split(b"\n")
                  if ln.startswith(b"data:") and ln[5:].strip() != b"[DONE]"]
        text = "".join((c.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                       for c in chunks)
        self.assertEqual(text, "Hello world")                  # shown
        self.assertTrue(all(c.get(CA.RESP_KEY) == CA.PROVIDER for c in chunks))
        # learned: the person's text, never the reply
        self.assertEqual(srv.capturer.turns, [(PERSON, "", "")])
        self.assertEqual(srv.capturer.calls, [])
        # the SDK got the system prompt; its own tools stayed off
        call = self.sdk.calls[0]
        self.assertIn("doctrine", call["options"].kw["system_prompt"])
        self.assertEqual(call["options"].kw["tools"], [])

    def test_buffered_turn_shows_the_reply_and_learns_only_the_person(self):
        srv, raw = self._deliver(stream=False)
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(body["choices"][0]["message"]["content"], "Hello world")
        self.assertEqual(body[CA.RESP_KEY], CA.PROVIDER)
        self.assertEqual(srv.capturer.turns, [(PERSON, "", "")])

    def test_no_reply_text_reaches_the_field_events(self):
        """Claims and the night read the field's events: a Claude turn leaves
        only the person's event there (no role 'model')."""
        from membrane.capture import Capturer
        seen = []
        fld = types.SimpleNamespace(write_events=lambda evs, session=None: seen.extend(evs) or [],
                                    process=lambda: None)
        cap = Capturer(fld, mode="shadow", start=False)
        srv = _srv()
        srv.capturer = cap
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": False,
              "messages": [{"role": "user", "content": PERSON}]}
        h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        cap.flush()
        self.assertTrue(seen)
        self.assertFalse([e for e in seen if getattr(e, "role", "") == "model"], seen)
        self.assertFalse([e for e in seen if "Hello" in (getattr(e, "text", "") or "")])

    def test_missing_cli_is_a_clear_503(self):
        srv = _srv()
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": True,
              "messages": [{"role": "user", "content": PERSON}]}
        with mock.patch.object(CA, "find_cli", lambda: None):
            h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        raw = h.wfile.getvalue()
        status = raw.split(b"\r\n", 1)[0].decode()
        self.assertIn("503", status)
        self.assertIn("claude", status)
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertIn("membrane auth claude-agent", body["error"]["message"])
        self.assertEqual(self.sdk.calls, [])

    def test_an_sdk_error_is_said_not_hidden(self):
        self.sdk = fake_sdk(error=RuntimeError("not logged in"))
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}):
            srv, raw = self._deliver(stream=False)
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertIn("not logged in", body["choices"][0]["message"]["content"])
        self.assertEqual(srv.capturer.turns, [(PERSON, "", "")])


# ---- the transcript: shown, marked by provider ----------------------------

def _ladder_returning(resp):
    def ladder(body):
        return resp
    def stream(body, on_event):
        on_event("text", resp["choices"][0]["message"]["content"])
        return resp
    ladder.stream = stream
    return ladder


class TestTranscriptMark(unittest.TestCase):
    def _run(self, resp, model="m"):
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                          _ladder_returning(resp), lambda *a: "ok", system="", model=model)
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, PERSON)
        return r, sid

    def test_a_claude_reply_row_is_marked(self):
        resp = {"choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}],
                CA.RESP_KEY: CA.PROVIDER}
        r, sid = self._run(resp)
        rows = r.messages.list(sid)
        asst = [x for x in rows if x["info"]["role"] == "assistant"][-1]
        self.assertTrue(CA.is_claude_made(asst["info"]))
        text = "".join(p.get("text") or "" for p in asst["parts"] if p.get("type") == "text")
        self.assertEqual(text, "Hello world")                       # the chat shows it
        user = [x for x in rows if x["info"]["role"] == "user"][-1]
        self.assertFalse(CA.is_claude_made(user["info"]))

    def test_another_rungs_row_is_not_marked(self):
        resp = {"choices": [{"message": {"content": "local words"}, "finish_reason": "stop"}]}
        r, sid = self._run(resp)
        asst = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]
        self.assertFalse(CA.is_claude_made(asst["info"]))

    def test_the_mark_keys_on_the_provider_not_the_name(self):
        self.assertTrue(CA.is_claude_made({"role": "assistant", CA.MARK_KEY: CA.PROVIDER,
                                           "modelID": "renamed-model"}))
        self.assertFalse(CA.is_claude_made({"role": "assistant", "modelID": "claude-sonnet"}))

    def test_the_voice_never_hears_a_claude_row(self):
        from membrane import voice as V
        rows = [{"info": {"role": "user", "id": "u1"},
                 "parts": [{"type": "text", "text": "the person speaks"}]},
                {"info": {"role": "assistant", "id": "a1", CA.MARK_KEY: CA.PROVIDER},
                 "parts": [{"type": "text", "text": "Claude speaks"}]},
                {"info": {"role": "assistant", "id": "a2"},
                 "parts": [{"type": "text", "text": "a local model speaks"}]}]
        runner = types.SimpleNamespace(
            sessions=types.SimpleNamespace(sessions={"s1": {"agent": "build", "title": "chat",
                                                            "time": {"updated": 1}}}),
            messages=types.SimpleNamespace(list=lambda sid: rows))
        heard = list(V.history_texts(runner))
        self.assertIn("the person speaks", heard)
        self.assertIn("a local model speaks", heard)
        self.assertNotIn("Claude speaks", heard)
        replies, pairs = V.history_replies(runner)
        self.assertFalse([t for _, t in replies if "Claude" in t], replies)
        self.assertFalse([p for p in pairs if "Claude" in p[1]], pairs)

    def test_the_turn_end_hands_the_voice_no_claude_reply(self):
        from membrane import voice as V
        seen = []
        resp = {"choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}],
                CA.RESP_KEY: CA.PROVIDER}
        with mock.patch.object(V, "on_turn_end",
                               lambda runner, sid, user_text, reply, lane, parts=None:
                               seen.append(reply)):
            self._run(resp)
        self.assertEqual(seen, [""])


class TestLoopbackCarriesTheProvider(unittest.TestCase):
    def test_streamed_round_reports_the_provider(self):
        chunks = [{"choices": [{"delta": {"content": "Hi"}}], CA.RESP_KEY: CA.PROVIDER},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}], CA.RESP_KEY: CA.PROVIDER}]
        payload = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) \
            + b"data: [DONE]\n\n"

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            lad = MS.loopback_ladder(s.server_address[1])
            out = lad.stream({"model": "claude-agent:sonnet", "messages": []},
                             lambda kind, val: False)
        finally:
            s.shutdown()
        self.assertEqual(out[CA.RESP_KEY], CA.PROVIDER)
        self.assertEqual(out["choices"][0]["message"]["content"], "Hi")


# ---- absorb: the fail-closed rule extends to Claude-made rows -------------

class TestAbsorb(unittest.TestCase):
    def test_a_claude_agent_rung_is_never_eligible(self):
        from membrane import absorb as AB
        self.assertTrue(AB._is_anthropic_model("claude-agent:sonnet"))
        self.assertTrue(AB.is_claude_made_row({"role": "assistant", CA.MARK_KEY: CA.PROVIDER,
                                                "modelID": "qwen3:14b"}))
        self.assertTrue(AB.is_claude_made_row({"role": "assistant", "providerID": "ollama",
                                                "modelID": "claude-agent:opus"}))
        self.assertFalse(AB.is_claude_made_row({"role": "assistant", "providerID": "ollama",
                                                 "modelID": "qwen3:14b"}))

    def test_an_opencode_session_with_a_claude_row_is_skipped_whole(self):
        import sqlite3
        import tempfile
        from membrane import absorb as AB
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        db = Path(d) / "opencode.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE session (id, directory, title, model, time_created, time_updated)")
        c.execute("CREATE TABLE message (id, session_id, time_created, data)")
        c.execute("CREATE TABLE part (id, message_id, time_created, data)")
        c.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                  ("ses1", d, "t", json.dumps({"providerID": "ollama", "modelID": "qwen3:14b"}),
                   1, 2_000_000_000_000))
        rows = [("m1", {"role": "user", "time": {"created": 1}}, "the person"),
                ("m2", {"role": "assistant", "providerID": "ollama", "modelID": "qwen3:14b",
                        CA.MARK_KEY: CA.PROVIDER, "time": {"created": 2}}, "Claude's words")]
        for i, (mid, data, text) in enumerate(rows):
            c.execute("INSERT INTO message VALUES (?,?,?,?)", (mid, "ses1", i, json.dumps(data)))
            c.execute("INSERT INTO part VALUES (?,?,?,?)",
                      (f"p{i}", mid, i, json.dumps({"type": "text", "text": text})))
        c.commit()
        c.close()
        self.assertEqual(AB.opencode_sessions(d, 0.0, db=db), [])


if __name__ == "__main__":
    unittest.main()


def test_the_install_hint_names_the_package_on_pypi():
    """The distribution is babyseat (0.7's rename); membrane is only the module."""
    import tomllib
    from pathlib import Path
    from membrane import claude_agent as CA
    root = Path(__file__).resolve().parent.parent
    name = tomllib.loads((root / "pyproject.toml").read_text())["project"]["name"]
    assert f"{name}[claude-agent]" in CA.INSTALL_HINT
    assert f"{name}[claude-agent]" in (root / "docs" / "claude-agent.md").read_text()
