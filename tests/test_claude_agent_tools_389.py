"""THE CLAUDE-AGENT RUNG, TAKE TWO (issue #389): Claude's replies are learned
like any other model's, and Claude gets membrane's own tools inside the
SDK's agent loop.

- the SDK options name ONLY membrane's tools (mcp__membrane__*) in
  allowed_tools, switch every Claude Code built-in off, read no user MCP
  config and no settings files, and cap the loop at MAX_TURNS;
- the tools are membrane's MCP surface (mcp_server.TOOLS / TOOL_IMPLS),
  served in-process: a tool call in the SDK's loop reaches the real tool
  function, and a write lands in the seat (a test entity store here);
- the proxy learns the reply (on_turn with the reply text) and Claude's
  tool calls and results, like any model's; the transcript shows the
  tool calls marked claude-agent;
- background lanes still refuse the rung.

The SDK is a stand-in throughout: the real `claude` CLI is never spawned and
no login is needed.
"""
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

from membrane import claude_agent as CA  # noqa: E402
from membrane import mcp_server as MCP  # noqa: E402
from membrane import server as MS  # noqa: E402

BUILTINS = ("Bash", "Read", "Edit", "Write", "MultiEdit", "Glob", "Grep",
            "WebFetch", "WebSearch", "Task", "NotebookEdit", "TodoWrite",
            "BashOutput", "KillShell")


# ---- the stubbed SDK: same class names as claude_agent_sdk's -----------------

class TextBlock:
    def __init__(self, text):
        self.text = text


class ToolUseBlock:
    def __init__(self, id, name, input):
        self.id, self.name, self.input = id, name, input


class ToolResultBlock:
    def __init__(self, tool_use_id, content=None, is_error=None):
        self.tool_use_id, self.content, self.is_error = tool_use_id, content, is_error


class AssistantMessage:
    def __init__(self, content, model="claude-sonnet", error=None):
        self.content, self.model, self.error = content, model, error


class UserMessage:
    def __init__(self, content):
        self.content = content


class ResultMessage:
    def __init__(self):
        self.subtype, self.is_error, self.result = "success", False, None
        self.usage = {"input_tokens": 20, "output_tokens": 5}


class StreamEvent:
    def __init__(self, event):
        self.event = event


def fake_sdk(call=None, words="Done."):
    """A stand-in claude_agent_sdk. `call` = (bare tool name, args): the fake
    agent loop calls that tool through the MCP server it was handed (the way
    the real SDK would), then answers `words`."""
    mod = types.ModuleType("claude_agent_sdk")
    mod.calls = []

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.kw = kw

    class SdkMcpTool:
        def __init__(self, name, description, input_schema, handler):
            self.name, self.description = name, description
            self.input_schema, self.handler = input_schema, handler

    def tool(name, description, input_schema):
        def deco(fn):
            return SdkMcpTool(name, description, input_schema, fn)
        return deco

    def create_sdk_mcp_server(name, version="1.0.0", tools=None):
        return {"type": "sdk", "name": name, "tools": list(tools or [])}

    async def query(*, prompt, options=None, transport=None):
        mod.calls.append({"prompt": prompt, "options": options})
        if call is not None:
            name, args = call
            full = f"mcp__membrane__{name}"
            assert full in options.allowed_tools, options.allowed_tools
            srv = options.mcp_servers["membrane"]
            t = next(t for t in srv["tools"] if t.name == name)
            yield AssistantMessage([TextBlock("Let me write that down. "),
                                    ToolUseBlock("toolu_1", full, args)])
            res = await t.handler(args)
            yield UserMessage([ToolResultBlock("toolu_1", res.get("content"),
                                               res.get("is_error"))])
        if getattr(options, "include_partial_messages", False):
            yield StreamEvent({"type": "content_block_delta", "index": 0,
                               "delta": {"type": "text_delta", "text": words}})
        yield AssistantMessage([TextBlock(words)])
        yield ResultMessage()

    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.SdkMcpTool = SdkMcpTool
    mod.tool = tool
    mod.create_sdk_mcp_server = create_sdk_mcp_server
    mod.query = query
    return mod


class _SDKCase(unittest.TestCase):
    call = None

    def setUp(self):
        self.sdk = fake_sdk(self.call)
        self._p = [mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()


# ---- the options: membrane's tools only --------------------------------------

class TestOptions(_SDKCase):
    def _opts(self):
        list(CA.iter_reply([{"role": "user", "content": "hi"}],
                           "claude-agent:sonnet", stream=False, port=9))
        return self.sdk.calls[0]["options"].kw

    def test_max_turns_is_the_named_constant(self):
        self.assertEqual(CA.MAX_TURNS, 4)
        self.assertEqual(self._opts()["max_turns"], CA.MAX_TURNS)

    def test_allowed_tools_are_exactly_membranes(self):
        kw = self._opts()
        want = sorted(f"mcp__membrane__{t['name']}" for t in MCP.TOOLS)
        self.assertEqual(sorted(kw["allowed_tools"]), want)
        self.assertTrue(want)
        self.assertTrue(all(n.startswith("mcp__membrane__") for n in kw["allowed_tools"]))
        for b in BUILTINS:
            self.assertNotIn(b, kw["allowed_tools"])

    def test_builtins_and_outside_config_stay_off(self):
        kw = self._opts()
        self.assertEqual(kw["tools"], [])                  # no Claude Code built-ins
        self.assertEqual(set(kw["mcp_servers"]), {"membrane"})
        self.assertTrue(kw["strict_mcp_config"])           # not the user's MCP servers
        self.assertEqual(kw["setting_sources"], [])        # no settings, no CLAUDE.md
        self.assertNotEqual(kw.get("permission_mode"), "bypassPermissions")

    def test_the_server_serves_every_membrane_tool_with_its_schema(self):
        srv = self._opts()["mcp_servers"]["membrane"]
        by = {t.name: t for t in srv["tools"]}
        self.assertEqual(set(by), {t["name"] for t in MCP.TOOLS})
        learn = next(t for t in MCP.TOOLS if t["name"] == "learn")
        self.assertEqual(by["learn"].input_schema, learn["inputSchema"])
        self.assertEqual(by["learn"].description, learn["description"])


# ---- a tool call in the loop reaches the real tool function -----------------

def _seat():
    """The real proxy's route table on a test entity store (RAM only)."""
    from membrane import opencode as OC
    from membrane.entities import EntityStore
    from membrane.intent import GoalSelection
    est = EntityStore(bucket="test-claude-agent-389", shared=False)

    class _Board:
        selection = GoalSelection(est)

    class _Membrane:
        embedder = None
        store = None
        gate_notes = []

    srv = ThreadingHTTPServer(("127.0.0.1", 0), OC.MembraneOpenAIHandler)
    srv.membrane = _Membrane()
    srv.entity_store = est
    srv._intent_board = _Board()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, est


class TestToolReachesTheSeat(_SDKCase):
    call = ("intend", {"text": "keep the boat's battery above 50%"})

    def setUp(self):
        super().setUp()
        self.seat, self.est = _seat()
        self.addCleanup(self.seat.shutdown)

    def test_a_write_lands_in_the_seat(self):
        got = list(CA.iter_reply([{"role": "user", "content": "plant it"}],
                                 "claude-agent:sonnet", stream=False,
                                 port=self.seat.server_address[1]))
        goals = [e for e in self.est.docs.values()
                 if "battery above 50%" in str(e.properties.get("name") or "")]
        self.assertEqual(len(goals), 1, list(self.est.docs))
        tools = [v for k, v in got if k == "tool"]
        self.assertEqual(len(tools), 1)
        t = tools[0]
        self.assertEqual(t["name"], "intend")
        self.assertEqual(t["id"], "toolu_1")
        self.assertEqual(t["input"], {"text": "keep the boat's battery above 50%"})
        self.assertIn(goals[0].eid, t["output"])
        self.assertFalse(t["is_error"])
        self.assertIn("Done.", "".join(v for k, v in got if k == "text"))

    def test_a_tool_error_is_handed_back_not_raised(self):
        self.seat.shutdown()
        self.seat.server_close()
        got = list(CA.iter_reply([{"role": "user", "content": "plant it"}],
                                 "claude-agent:sonnet", stream=False,
                                 port=self.seat.server_address[1]))
        t = [v for k, v in got if k == "tool"][0]
        self.assertTrue(t["is_error"])
        self.assertIn("membrane tool error", t["output"])


# ---- the proxy turn: learned like any model, tools shown --------------------

class _Cap:
    def __init__(self):
        self.turns, self.calls, self.results = [], [], []

    def on_turn(self, query, prose="", thought="", **k):
        self.turns.append((query, prose, thought))

    def on_calls(self, query, calls, **k):
        self.calls.append((query, calls))

    def on_tool_result(self, call_id, name, args, result, query="", **k):
        self.results.append((call_id, name, args, result))


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


def _proxy_srv(port):
    from tests.test_claude_agent_384 import _registry
    mem = types.SimpleNamespace(embedder=None, store=None, efield=None)
    return types.SimpleNamespace(membrane=mem, router=_registry(), capturer=_Cap(),
                                 think=None, backend="http://127.0.0.1:9",
                                 cloud_keys={}, gate_monologue=False,
                                 listen_port=port)


PERSON = "remember to keep the battery above 50%"


class TestProxyLearns(_SDKCase):
    call = ("intend", {"text": "keep the boat's battery above 50%"})

    def setUp(self):
        super().setUp()
        self.seat, self.est = _seat()
        self.addCleanup(self.seat.shutdown)

    def _deliver(self, stream):
        srv = _proxy_srv(self.seat.server_address[1])
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": stream,
              "messages": [{"role": "user", "content": PERSON}]}
        h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        return srv, h.wfile.getvalue()

    def _check_learned(self, srv):
        self.assertEqual(len(srv.capturer.turns), 1)
        q, prose, _ = srv.capturer.turns[0]
        self.assertEqual(q, PERSON)
        self.assertIn("Done.", prose)                      # the reply IS learned
        self.assertEqual(len(srv.capturer.calls), 1)       # its tool call too
        calls = srv.capturer.calls[0][1]
        c = list(calls.values())[0]
        self.assertEqual(c["function"]["name"], "intend")
        self.assertEqual(json.loads(c["function"]["arguments"]),
                         {"text": "keep the boat's battery above 50%"})
        self.assertEqual(len(srv.capturer.results), 1)     # and its result
        self.assertEqual(srv.capturer.results[0][0], "toolu_1")
        self.assertEqual(srv.capturer.results[0][1], "intend")

    def test_streamed_turn_learns_the_reply_and_shows_the_tool(self):
        srv, raw = self._deliver(stream=True)
        self._check_learned(srv)
        body = raw.split(b"\r\n\r\n", 1)[1]
        chunks = [json.loads(ln[5:]) for ln in body.split(b"\n")
                  if ln.startswith(b"data:") and ln[5:].strip() != b"[DONE]"]
        tools = [c[CA.TOOL_KEY] for c in chunks if c.get(CA.TOOL_KEY)]
        self.assertEqual([t["name"] for t in tools], ["intend"])
        self.assertTrue(all(c.get(CA.RESP_KEY) == CA.PROVIDER for c in chunks))

    def test_buffered_turn_learns_the_reply_and_shows_the_tool(self):
        srv, raw = self._deliver(stream=False)
        self._check_learned(srv)
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertEqual([t["name"] for t in body[CA.TOOLS_KEY]], ["intend"])
        self.assertIn("Done.", body["choices"][0]["message"]["content"])

    def test_the_reply_reaches_the_field_events(self):
        """Claims and the night read the field's events: a Claude turn now
        leaves a model event with its words, like any model's."""
        from membrane.capture import Capturer
        seen = []
        fld = types.SimpleNamespace(write_events=lambda evs, session=None: seen.extend(evs) or [],
                                    process=lambda: None)
        cap = Capturer(fld, mode="shadow", start=False)
        srv = _proxy_srv(self.seat.server_address[1])
        srv.capturer = cap
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": False,
              "messages": [{"role": "user", "content": PERSON}]}
        h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        cap.flush()
        self.assertTrue([e for e in seen if "Done." in (getattr(e, "text", "") or "")], seen)


# ---- the transcript: tool parts marked claude-agent -------------------------

def _ladder(resp, tools):
    def ladder(body):
        return dict(resp, **{CA.TOOLS_KEY: tools})

    def stream(body, on_event):
        for t in tools:
            on_event("tool", t)
        on_event("text", resp["choices"][0]["message"]["content"])
        return dict(resp, **{CA.TOOLS_KEY: tools})
    ladder.stream = stream
    return ladder


TOOL = {"id": "toolu_1", "name": "intend", "input": {"text": "x"},
        "output": "Goal active (created + pinned): goal:abc", "is_error": False}


class TestTranscript(unittest.TestCase):
    def _run(self, stream):
        resp = {"choices": [{"message": {"content": "Done."}, "finish_reason": "stop"}],
                CA.RESP_KEY: CA.PROVIDER}
        lad = _ladder(resp, [TOOL])
        if not stream:
            del lad.stream
        executed = []
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), lad,
                          lambda *a: executed.append(a) or "ok", system="", model="m")
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, PERSON)
        return r, sid, executed

    def _check(self, stream):
        r, sid, executed = self._run(stream)
        asst = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]
        self.assertTrue(CA.is_claude_made(asst["info"]))
        tparts = [p for p in asst["parts"] if p.get("type") == "tool" and p.get("tool") == "intend"]
        self.assertEqual(len(tparts), 1, asst["parts"])
        p = tparts[0]
        self.assertEqual(p.get(CA.MARK_KEY), CA.PROVIDER)
        self.assertEqual(p["callID"], "toolu_1")
        self.assertEqual(p["state"]["status"], "completed")
        self.assertEqual(p["state"]["input"], {"text": "x"})
        self.assertIn("goal:abc", p["state"]["output"])
        self.assertEqual(executed, [])        # ran in Claude's loop, never again here

    def test_streamed_tool_part(self):
        self._check(stream=True)

    def test_buffered_tool_part(self):
        self._check(stream=False)

    def test_an_errored_tool_shows_as_an_error(self):
        bad = dict(TOOL, is_error=True, output="membrane tool error: nope")
        resp = {"choices": [{"message": {"content": "Sorry."}, "finish_reason": "stop"}],
                CA.RESP_KEY: CA.PROVIDER}
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                          _ladder(resp, [bad]), lambda *a: "ok", system="", model="m")
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, PERSON)
        asst = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"][-1]
        p = [p for p in asst["parts"] if p.get("tool") == "intend"][0]
        self.assertEqual(p["state"]["status"], "error")
        self.assertIn("nope", p["state"]["error"])


class TestLoopbackCarriesTheTools(unittest.TestCase):
    def test_stream_hands_tool_events_and_returns_them(self):
        from http.server import BaseHTTPRequestHandler
        chunks = [{"choices": [], CA.RESP_KEY: CA.PROVIDER, CA.TOOL_KEY: TOOL},
                  {"choices": [{"delta": {"content": "Done."}}], CA.RESP_KEY: CA.PROVIDER},
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
        seen = []
        try:
            out = MS.loopback_ladder(s.server_address[1]).stream(
                {"model": "claude-agent:sonnet", "messages": []},
                lambda kind, val: seen.append((kind, val)) and False)
        finally:
            s.shutdown()
        self.assertIn(("tool", TOOL), seen)
        self.assertEqual(out[CA.TOOLS_KEY], [TOOL])
        self.assertEqual(out["choices"][0]["message"]["content"], "Done.")


# ---- background lanes still refuse -----------------------------------------

class TestBackgroundStillRefuses(_SDKCase):
    call = ("learn", {"lesson": "never"})

    def test_model_chat_makes_no_call(self):
        from membrane.rag import _model_chat
        from tests.test_claude_agent_384 import _registry
        srv = types.SimpleNamespace(router=_registry(), backend="http://x", think=None)
        self.assertEqual(_model_chat(srv)("", "claude-agent:sonnet",
                                          [{"role": "user", "content": "x"}]), "")
        self.assertEqual(self.sdk.calls, [])

    def test_a_loop_lane_falls_back(self):
        from tests.test_claude_agent_384 import _registry
        srv = types.SimpleNamespace(router=_registry())
        for lane in ("watch:x", "thinker", "loop-3", "field-call"):
            self.assertEqual(CA.guard_turn_model(srv, "claude-agent:opus", lane, "m"), "m")


if __name__ == "__main__":
    unittest.main()
