"""THE NATIVE SERVER (stage 3, TDD): membrane replaces opencode completely
-- `membrane serve` hosts its own opencode-compatible REST/SSE surface (the
companion app + ha-dashboard connect unchanged; opencode leaves the
picture). The server is: SessionStore + MessageStore (opencode Session/
Message/Part shapes beside the field), an SSE hub, the TURN LOOP (client
text -> the proxy's own /v1 ladder on loopback -> tool_calls executed
IN-PROCESS against the MCP tool impls -> role:'tool' results -> repeat;
every part state transition -- pending -> completed -- published as a
message.part.updated event, so the model's tool use is visible in the
client like any opencode tool), and the HTTP surface (basic auth, the
session/message/abort/event subset a chat client speaks). The proxy-side
machinery (injection, capture, banner, routing) comes free through /v1.
Deterministic fakes only: stub ladder + stub executor, an ephemeral port.
"""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as NS


def _store(dir_=None):
    return (NS.SessionStore(str(Path(dir_) / "serve-sessions.json")
                            if dir_ else None))


_CFG = {"$schema": "https://opencode.ai/config.json",
        "provider": {"membrane": {"name": "Membrane (field)",
                                  "npm": "@ai-sdk/openai-compatible",
                                  "options": {"baseURL":
                                              "http://127.0.0.1:9/v1"},
                                  "models": {"glm-x": {"name": "glm-x"}}}}}


class TestSessionStore(unittest.TestCase):
    def test_create_shape_and_persist(self):
        d = tempfile.mkdtemp()
        st = _store(d)
        s = st.create(title="hello")
        self.assertTrue(s["id"].startswith("ses_"))
        for k in ("projectID", "directory", "title", "version"):
            self.assertIn(k, s)
        self.assertEqual(s["title"], "hello")
        self.assertIn("created", s["time"])
        st.flush()
        st2 = _store(d)
        self.assertEqual(st2.get(s["id"])["title"], "hello")

    def test_list_patch_delete(self):
        st = _store()
        a = st.create(title="a")
        b = st.create()
        self.assertEqual([x["id"] for x in st.list()], [a["id"], b["id"]])
        self.assertIsNotNone(st.patch(a["id"], title="renamed"))
        self.assertEqual(st.get(a["id"])["title"], "renamed")
        self.assertTrue(st.delete(a["id"]))
        self.assertIsNone(st.get(a["id"]))
        self.assertFalse(st.delete(a["id"]))


TC = {"name": "membrane_run", "description": "run one command",
      "inputSchema": {"type": "object", "properties": {}}}
TOOLCALL = {"id": "call_1", "type": "function",
            "function": {"name": "membrane_run",
                         "arguments": json.dumps({"command": "echo hi"})}}


def _resp(content=None, calls=None):
    m = {"content": content}
    if calls:
        m["tool_calls"] = calls
    return {"choices": [{"message": m, "finish_reason":
                         "tool_calls" if calls else "stop"}]}


def _stub_ladder(responses, calls):
    """A ladder fake: records each body, answers the next canned response."""
    def call(body):
        calls.append(body)
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    return call


class TestTurnLoop(unittest.TestCase):
    def _world(self, responses):
        hub = NS.SSEHub()
        sessions = _store()
        msgs = NS.MessageStore()
        calls, exec_calls = [], []
        ladder = _stub_ladder(responses, calls)
        executor = lambda name, args, call_id: (
            exec_calls.append((name, args)) or "hi\n")
        s = sessions.create()
        t = NS.TurnRunner(hub, sessions, msgs, ladder, executor,
                          system="SYS", model="glm-x", tools=[TC])
        return dict(hub=hub, sessions=sessions, msgs=msgs, calls=calls,
                    ec=exec_calls, s=s, t=t)

    def test_plain_turn_shape(self):
        w = self._world([_resp(content="pong")])
        out = w["t"].run(w["s"]["id"], "say pong")
        self.assertEqual(out["parts"][0]["type"], "step-start")
        self.assertEqual(out["parts"][1]["type"], "text")
        self.assertEqual(out["parts"][1]["text"], "pong")
        self.assertEqual(out["parts"][-1]["type"], "step-finish")
        b = w["calls"][0]
        self.assertEqual(b["model"], "glm-x")
        self.assertEqual(b["messages"][0]["role"], "system")
        self.assertIn("SYS", b["messages"][0]["content"])
        self.assertEqual(b["messages"][-1],
                         {"role": "user", "content": "say pong"})
        self.assertTrue([t for t in b["tools"] if "function" in t])
        # the user message is recorded FIRST, before the assistant's
        self.assertEqual(w["msgs"].list(w["s"]["id"])[0]["info"]["role"],
                         "user")
        self.assertEqual(
            w["msgs"].list(w["s"]["id"])[0]["parts"][0]["text"], "say pong")

    def test_user_part_shape(self):
        w = self._world([_resp(content="ok")])
        w["t"].run(w["s"]["id"], "hi")
        u = w["msgs"].list(w["s"]["id"])[0]["info"]
        self.assertIsInstance(u["id"], str)
        self.assertTrue(u["id"].startswith("msg_"))
        self.assertEqual(u["sessionID"], w["s"]["id"])
        self.assertEqual(u["model"]["providerID"], "membrane")

    def test_turn_sse_sequence_matches_real_serve(self):
        # the REAL serve's turn sequence (captured off 1.18.34): the user
        # message announced first, then session, then busy, then the
        # assistant info BEFORE its parts, deltas mid-stream, and the
        # closing session.updated AFTER session.idle
        w = self._world([_resp(content="pong")])
        q = w["hub"].subscribe()
        q.get(timeout=2)          # server.connected
        w["t"].run(w["s"]["id"], "say pong")
        types = [q.get(timeout=2)["type"] for _ in range(18)]
        self.assertEqual(types, [
            "message.updated", "message.part.updated",
            "session.updated", "session.diff",
            "session.status",
            "message.updated", "session.updated", "session.diff",
            "message.part.updated", "message.part.updated",
            "message.part.delta", "message.part.updated",
            "message.part.updated",
            "message.updated",
            "session.status", "session.idle",
            "session.updated", "session.diff"])
        with self.assertRaises(Exception):
            q.get(timeout=0.2)    # nothing further

    def test_part_events_carry_a_top_level_time(self):
        w = self._world([_resp(content="pong")])
        q = w["hub"].subscribe()
        q.get(timeout=2)
        w["t"].run(w["s"]["id"], "say pong")
        evs = []
        for _ in range(18):
            evs.append(q.get(timeout=2))
        ups = [e for e in evs if e["type"] == "message.part.updated"]
        for e in ups:
            self.assertIsInstance(e.get("time"), int)
        # the user's part first (full text), the assistant's opens EMPTY,
        # deltas in, closes with the full text
        texts = [e["part"].get("text") for e in ups
                 if e["part"]["type"] == "text"]
        self.assertEqual(texts, ["say pong", "", "pong"])
        dl = [e for e in evs if e["type"] == "message.part.delta"]
        self.assertEqual([d["delta"] for d in dl], ["pong"])
        empty = [e["part"]["id"] for e in ups
                 if e["part"]["type"] == "text"][1]
        self.assertEqual(dl[0]["partID"], empty)
        su = [e for e in evs if e["type"] == "session.updated"]
        self.assertTrue(su and su[0]["info"].get("path"))
        self.assertEqual(su[0]["info"]["summary"],
                         {"additions": 0, "deletions": 0, "files": 0})

    def test_session_gains_path_and_summary_keys(self):
        s = NS.SessionStore().create()
        self.assertTrue(s.get("path") and not s["path"].startswith("/"))
        self.assertEqual(s["summary"],
                         {"additions": 0, "deletions": 0, "files": 0})
        self.assertEqual(s["model"]["variant"], "default")

    def test_touch_records_epoch_ms_updated(self):
        # the attached TUI's -c/continue lane sorts sessions by
        # time.updated DESC and takes the first parentLESS one -- a
        # seconds-valued updated sorted as ancient and broke the lane
        st = NS.SessionStore()
        s = st.create()
        self.assertTrue(str(s["time"]["created"]).startswith("17")
                        or s["time"]["created"] > 10**12)
        st.touch(s["id"])
        self.assertGreater(s["time"]["updated"], 10**12)
        st.patch(s["id"], "renamed")
        self.assertGreater(s["time"]["updated"], 10**12)

    def test_tool_round_trip(self):
        w = self._world([_resp(calls=[TOOLCALL]), _resp(content="done")])
        w["t"].run(w["s"]["id"], "run it")
        self.assertEqual(w["ec"][0], ("membrane_run",
                                      {"command": "echo hi"}))
        body2 = w["calls"][1]
        tool_msgs = [m for m in body2["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1")
        self.assertEqual(tool_msgs[0]["content"], "hi\n")
        last = w["msgs"].list(w["s"]["id"])[-1]
        self.assertEqual(last["info"]["role"], "assistant")
        kinds = [p["type"] for p in last["parts"]]
        self.assertEqual(kinds, ["step-start", "tool", "step-finish",
                                 "step-start", "text", "step-finish"])
        self.assertEqual(kinds[0], "step-start")
        tp = last["parts"][0]
        tp = next(p for p in last["parts"] if p["type"] == "tool")
        self.assertEqual(tp["tool"], "membrane_run")
        self.assertEqual(tp["callID"], "call_1")
        self.assertEqual(tp["state"]["status"], "completed")
        self.assertEqual(tp["state"]["output"], "hi\n")
        self.assertEqual(next(p for p in last["parts"]
                              if p["type"] == "text")["text"], "done")

    def test_ladder_carries_the_openai_tool_format(self):
        w = self._world([_resp(calls=[TOOLCALL]), _resp(content="d")])
        w["t"].run(w["s"]["id"], "x")
        for b in w["calls"]:
            self.assertEqual(b["tools"],
                             [{"type": "function",
                               "function": {"name": "membrane_run",
                                            "description": "run one command",
                                            "parameters":
                                            {"type": "object",
                                             "properties": {}}}}])

    def test_tool_error_keeps_looping(self):
        w = self._world([_resp(calls=[TOOLCALL]), _resp(content="ok")])
        w["t"].executor = lambda name, args, call_id: (_ for _ in ()).throw(
            RuntimeError("boom"))
        w["t"].run(w["s"]["id"], "go")
        tool_msgs = [m for m in w["calls"][1]["messages"]
                     if m["role"] == "tool"]
        self.assertIn("boom", tool_msgs[0]["content"])
        last = w["msgs"].list(w["s"]["id"])[-1]
        tp = next(p for p in last["parts"] if p["type"] == "tool")
        self.assertEqual(tp["state"]["status"], "error")
        self.assertIn("boom", tp["state"]["error"] or tp["state"]["output"])

    def test_events_publish_part_transitions(self):
        w = self._world([_resp(calls=[TOOLCALL]), _resp(content="done")])
        evs = _drain_hub(w["hub"], w["t"], w["s"]["id"], "run it")
        kinds = [e["type"] for e in evs]
        self.assertIn("session.status", kinds)
        self.assertIn("message.updated", kinds)
        self.assertIn("session.idle", kinds)
        # the tool part was published pending AND completed
        tool_parts = [e["part"] for e in evs
                      if e["type"] == "message.part.updated"
                      and e["part"]["type"] == "tool"]
        self.assertEqual(len(tool_parts), 2)
        self.assertEqual(tool_parts[0]["state"]["status"], "pending")
        self.assertEqual(tool_parts[1]["state"]["status"], "completed")

    def test_parts_carry_sdk_ids_and_ms_times(self):
        w = self._world([_resp(calls=[TOOLCALL]), _resp(content="done")])
        out = w["t"].run(w["s"]["id"], "run it")
        ui = out["info"]
        self.assertEqual(ui["parentID"],
                         w["msgs"].list(w["s"]["id"])[0]["info"]["id"])
        self.assertEqual(ui["finish"], "stop")
        self.assertIn("total", ui["tokens"])
        for p in out["parts"]:
            self.assertTrue(p["id"].startswith("prt_"), str(p)[:80])
            self.assertEqual(p["sessionID"], w["s"]["id"])
            self.assertEqual(p["messageID"], ui["id"])
        kinds = [p["type"] for p in out["parts"]]
        self.assertEqual(kinds, ["step-start", "tool", "step-finish",
                                 "step-start", "text", "step-finish"])
        tp = out["parts"][1]
        self.assertGreater(tp["state"]["time"]["start"], 1e9)
        sf = out["parts"][2]
        self.assertEqual(sf["reason"], "tool-calls")
        text = out["parts"][4]
        self.assertEqual(text["time"]["end"], text["time"]["start"])
        self.assertGreater(text["time"]["start"], 1e9)

    def test_user_parts_carry_sdk_ids(self):
        w = self._world([_resp(content="ok")])
        w["t"].run(w["s"]["id"], "hi")
        rows = w["msgs"].list(w["s"]["id"])
        up = rows[0]["parts"][0]
        self.assertTrue(up["id"].startswith("prt_"))
        self.assertEqual(up["sessionID"], w["s"]["id"])
        self.assertEqual(up["messageID"], rows[0]["info"]["id"])
        self.assertEqual(rows[0]["info"]["role"], "user")

    def test_abort_stops_the_loop(self):
        w = self._world([_resp(calls=[TOOLCALL])])
        w["t"].abort(w["s"]["id"])
        q = w["hub"].subscribe()
        time.sleep(0.05)
        out = w["t"].run(w["s"]["id"], "x")
        self.assertEqual(w["calls"], [])         # never even called
        self.assertTrue(out["info"].get("time", {}).get("completed"))
        evs, n = [], 0
        while n < 40:
            try:
                evs.append(q.get(timeout=0.05))
            except Exception:
                break
            n += 1
        w["hub"].unsubscribe(q)
        self.assertIn("session.idle", [e["type"] for e in evs])


class TestMessageStore(unittest.TestCase):
    def test_rows_and_persist(self):
        d = tempfile.mkdtemp()
        m1, m2 = NS.MessageStore(str(Path(d) / "m.json")), None
        sess = "ses_x"
        m1.append(sess, {"id": "a", "role": "user"}, [])
        m1.append(sess, {"id": "b", "role": "assistant"}, [])
        m1.flush()
        m2 = NS.MessageStore(str(Path(d) / "m.json"))
        self.assertEqual([r["info"]["id"] for r in m2.list(sess)], ["a", "b"])


def _drain_hub(hub, runner=None, sid=None, text=None):
    q = hub.subscribe()
    evs, alive = [], True
    evs.append(q.get(timeout=2))             # server.connected arrives first
    if runner is not None:
        th = threading.Thread(target=lambda: runner.run(sid, text),
                              daemon=True)
        th.start()
        for _ in range(200):
            try:
                evs.append(q.get(timeout=0.05))
            except Exception:
                if not th.is_alive():
                    break
        th.join(timeout=2)
    hub.unsubscribe(q)
    return evs


class TestHttpSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hub = NS.SSEHub()
        cls.sessions = _store()
        cls.msgs = NS.MessageStore()
        cls.abort_seen = {}
        stub = _StubRunner(cls.abort_seen)
        handler = NS.make_handler(hub=hub, sessions=cls.sessions,
                                  messages=cls.msgs, user="opencode",
                                  password="pw", config=_CFG, runner=stub)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever,
                         daemon=True).start()
        cls.hub = hub

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _req(self, p, method="GET", body=None, auth=True):
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{p}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        if auth:
            r.add_header("Authorization",
                         "Basic " + base64.b64encode(b"opencode:pw").decode())
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {}

    def test_auth_required(self):
        self.assertEqual(self._req("/session", auth=False)[0], 401)

    def test_health_and_session_crud(self):
        self.assertEqual(self._req("/global/health")[0], 200)
        code, s = self._req("/session", "POST", {"title": "T"})
        self.assertEqual(code, 200)
        self.assertTrue(s["id"].startswith("ses_"))
        code, lst = self._req("/session")
        self.assertIn(s["id"], [x["id"] for x in lst])
        code, one = self._req(f"/session/{s['id']}")
        self.assertEqual(one["title"], "T")
        code, one = self._req(f"/session/{s['id']}", "PATCH",
                              {"title": "R"})
        self.assertEqual(one["title"], "R")
        self.assertEqual(self._req(f"/session/{s['id']}", "DELETE")[0], 200)
        self.assertEqual(self._req(f"/session/{s['id']}")[0], 404)

    def test_bad_id_404(self):
        self.assertEqual(self._req("/session/ses_nope")[0], 404)

    def test_message_synchronous_reply(self):
        s = self.sessions.create()
        code, out = self._req(f"/session/{s['id']}/message", "POST",
                              {"parts": [{"type": "text", "text": "hi"}]})
        self.assertEqual(code, 200)
        # _StubRunner.run records the text it was handed
        self.assertEqual(out["info"], "ran:" + s["id"] + ":hi")
        rows = self.msgs.list(s["id"])
        self.assertEqual(rows[0]["info"]["role"], "user")
        self.assertEqual(rows[0]["parts"][0]["text"], "hi")

    def test_user_part_recorded_before_the_turn(self):
        s = self.sessions.create()
        self._req(f"/session/{s['id']}/message", "POST",
                  {"parts": [{"type": "text", "text": "hello there"}]})
        rows = self.msgs.list(s["id"])
        self.assertEqual(rows[0]["parts"][0]["text"], "hello there")
        # a second turn appends, not replaces
        self._req(f"/session/{s['id']}/message", "POST",
                  {"parts": [{"type": "text", "text": "again"}]})
        self.assertEqual(
            [r["parts"][0]["text"] for r in self.msgs.list(s["id"])],
            ["hello there", "again"])

    def test_bad_session_404(self):
        self.assertEqual(self._req("/session/ses_nope/message", "POST",
                                   {"parts": []})[0], 404)

    def test_empty_parts_400(self):
        s = self.sessions.create()
        self.assertEqual(self._req(f"/session/{s['id']}/message", "POST",
                                   {"parts": []})[0], 400)

    def test_abort_endpoint(self):
        s = self.sessions.create()
        self.assertEqual(self._req(f"/session/{s['id']}/abort", "POST",
                                   {})[0], 200)
        self.assertEqual(self.abort_seen.get("abort"), s["id"])

    def test_sse_first_event_server_connected(self):
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/event",
            headers={"Authorization": "Basic " + base64.b64encode(
                b"opencode:pw").decode()})
        resp = urllib.request.urlopen(r, timeout=5)
        try:
            buf = ""
            n = 0
            while 'data: {' not in buf and n < 30:
                buf += resp.readline().decode()
                n += 1
        finally:
            resp.close()
        self.assertIn("server.connected", buf)
        payload = json.loads(buf.split("data: ")[-1])
        # the real serve's envelope: the TUI crashes on a flat payload
        self.assertEqual(payload["type"], "server.connected")
        self.assertTrue(payload["id"].startswith("evt_"))
        self.assertEqual(payload["properties"], {})

    def test_config_providers_is_the_sdk_array_shape(self):
        # the ATTACHED TUI crashes on the bare config dict: opencode's own
        # serve 1.18 returns providers as an ARRAY with full model metas
        code, out = self._req("/config/providers")
        self.assertEqual(code, 200)
        ps = out["providers"]
        self.assertIsInstance(ps, list)
        self.assertEqual(len(ps), 1)
        p = ps[0]
        self.assertEqual(p["id"], "membrane")
        self.assertEqual(p["source"], "api")
        m = p["models"]["glm-x"]
        self.assertEqual(m["id"], "glm-x")
        self.assertEqual(m["providerID"], "membrane")
        self.assertEqual(m["api"]["url"], "http://127.0.0.1:9/v1")
        self.assertEqual(m["api"]["npm"], "@ai-sdk/openai-compatible")
        self.assertIs(m["capabilities"]["toolcall"], True)
        self.assertIn("cost", m)
        self.assertIn("limit", m)
        self.assertEqual(m["status"], "active")
        self.assertEqual(m["variants"], {})

    def test_provider_route_is_the_all_default_connected_shape(self):
        # /provider is a DIFFERENT endpoint from /config/providers: the
        # TUI resolves the model it submits with from `default`'s
        # providerID->modelID map; with {} it silently refuses Enter
        code, out = self._req("/provider")
        self.assertEqual(code, 200)
        self.assertIsInstance(out["all"], list)
        self.assertEqual(out["all"][0]["id"], "membrane")
        self.assertEqual(out["default"], {"membrane": "glm-x"})
        self.assertEqual(out["connected"], ["membrane"])

    def test_config_providers_default_names_a_model(self):
        code, out = self._req("/config/providers")
        self.assertEqual(out["default"], {"membrane": "glm-x"})

    def test_global_event_serves_sse(self):
        # the attached TUI polls /global/event (NOT /event); a 404 there
        # leaves it without an event stream
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/global/event",
            headers={"Authorization": "Basic " + base64.b64encode(
                b"opencode:pw").decode()})
        resp = urllib.request.urlopen(r, timeout=5)
        try:
            buf = ""
            for _ in range(30):
                buf += resp.readline().decode()
                if 'data: {' in buf:
                    break
        finally:
            resp.close()
        self.assertIn("server.connected", buf)

    def test_session_diff_route_is_an_array(self):
        # the attached TUI seeds its Modified Files sidebar state from
        # GET /session/:id/diff; the fall-through here used to return the
        # SESSION OBJECT, the client's diff getters then read undefined
        # and the sidebar crashes the TUI at wide terminals
        _, s = self._req("/session", "POST", {})
        sid = s["id"]
        self.assertEqual(self._req(f"/session/{sid}/diff")[1], [])
        self.assertEqual(self._req(f"/session/{sid}/todo")[1], [])

    def test_boot_404s_become_sdk_stubs(self):
        # the shapes real opencode serve 1.18.34 returns for the TUI's
        # boot probes (captured via curl); a 404 leaves the TUI with
        # missing client-side state
        caps = {"experimental/capabilities":
                {"backgroundSubagents": False},
                "provider/auth": {},
                "mcp": {},
                "formatter": [],
                "lsp": [],
                "experimental/resource": {},
                "experimental/workspace": [],
                "experimental/console":
                {"consoleManagedProviders": [],
                 "switchableOrgCount": 0},
                "project/global/directories": []}
        for p, body in caps.items():
            code, out = self._req("/" + p)
            self.assertEqual(code, 200, p)
            self.assertEqual(out, body, p)
        code, out = self._req("/vcs")
        self.assertEqual(code, 200)
        self.assertIn("branch", out)
        self.assertIn("default_branch", out)

    def test_agent_route_carries_a_primary_agent(self):
        # REAL probe: /agent=[] in front of the REAL opencode serve makes
        # the attached TUI refuse Enter silently (no primary agent -> no
        # model chip -> no POST at all)
        code, out = self._req("/agent")
        self.assertEqual(code, 200)
        self.assertIsInstance(out, list)
        names = [a["name"] for a in out]
        self.assertIn("build", names)
        build = next(a for a in out if a["name"] == "build")
        self.assertEqual(build["mode"], "primary")
        self.assertTrue(build.get("native"))
        self.assertIn("description", build)
        self.assertTrue(any(p.get("permission") == "*" for p in
                            build.get("permission", [])))

    def test_session_shape_matches_the_sdk(self):
        # captured from real opencode serve 1.18: slug/cost/tokens/agent/
        # model/version on the session, epoch-ms times; the attached TUI
        # crashes on missing SDK fields
        s = self.sessions.create(title="sdk")
        for k in ("slug", "cost", "tokens", "agent", "model", "version"):
            self.assertIn(k, s)
        self.assertIn("total", s["tokens"])
        self.assertEqual(s["agent"], "build")
        self.assertIn("providerID", s["model"])
        self.assertIn("id", s["model"])

    def test_agent_route_carries_a_primary_agent(self):
        # REAL probe: /agent=[] in front of the REAL opencode serve makes
        # the attached TUI refuse Enter silently (no primary agent -> no
        # model chip -> no POST at all)
        code, out = self._req("/agent")
        self.assertEqual(code, 200)
        self.assertIsInstance(out, list)
        names = [a["name"] for a in out]
        self.assertIn("build", names)
        build = next(a for a in out if a["name"] == "build")
        self.assertEqual(build["mode"], "primary")
        self.assertTrue(build.get("native"))
        self.assertIn("description", build)
        self.assertTrue(any(p.get("permission") == "*" for p in
                            build.get("permission", [])))

    def test_session_shape_matches_the_sdk(self):
        # captured from real opencode serve 1.18: slug/cost/tokens/agent/
        # model/version on the session, epoch-ms times; the attached TUI
        # crashes on missing SDK fields
        s = self.sessions.create(title="sdk")
        for k in ("slug", "cost", "tokens", "agent", "model", "version"):
            self.assertIn(k, s)
        self.assertIn("total", s["tokens"])
        self.assertEqual(s["agent"], "build")
        self.assertIn("providerID", s["model"])
        self.assertIn("id", s["model"])

    def test_path_shape(self):
        code, out = self._req("/path")
        self.assertEqual(code, 200)
        for k in ("home", "state", "config", "worktree", "directory"):
            self.assertIn(k, out)

    def test_project_current_shape(self):
        code, out = self._req("/project/current")
        self.assertEqual(code, 200)
        self.assertIn(out["vcs"], ("git", None))
        self.assertIsInstance(out["sandboxes"], list)
        self.assertIn("time", out)


class _StubRunner:
    def __init__(self, abort=None):
        self._abort = abort

    def run(self, sid, text, user_info=None, user_parts=None):
        # the REAL surface records the user turn itself; the stub gets it
        # and echoes the run contract
        return "ran:" + sid + ":" + text

    def abort(self, sid):
        if self._abort is not None:
            self._abort["abort"] = sid


if __name__ == "__main__":
    unittest.main()