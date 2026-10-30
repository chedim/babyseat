"""The minds' models and credentials (operator, 2026-10-23).

1. A loop turn runs on the loop runner's model (Minds -> loops, now
   ⚙ -> Background work -> Goal worker), never on a model its session
   remembers, and its reply is stamped with the model that actually ran.
   Before this, the stamp read only the session's model and fell back to a
   hardcoded 'glm-5.3-flash:cloud': the live loop-1 session showed glm
   while its turns ran on qwen3:8b. A person's chat keeps the model its
   session holds (#457).
2. The claude-agent rung on a background lane (#384 relaxed): with no
   explicit pick the guard holds (no call); an explicit pick of a Claude
   rung for a mind runs that rung with the mind's own credentials
   ("chat" = whatever the chat's rung uses, "api-key", "login"); the
   chat's credentials are unaffected.
3. The ⚙ window's serve route reads and writes the two minds through
   minds_models (one state, no copy).
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import minds_models as MM  # noqa: E402
from membrane import options as O  # noqa: E402
from membrane import server as MS  # noqa: E402

LOOP = "watch: membrane::goal:abc"
CLAUDE = "claude-agent:sonnet"


class _Srv:
    def __init__(self, d):
        self._loop_runner = None
        self.idle_cortex_model = "qwen3:8b"
        self._models_path = str(Path(d) / "models.json")
        self.default_seat_model = "qwen3:14b"
        self.router = None


def _runner(srv, model="qwen3:8b"):
    bodies = []
    resp = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    def ladder(body):
        bodies.append(json.loads(json.dumps(body)))
        return resp

    def stream(body, on_event):
        bodies.append(json.loads(json.dumps(body)))
        on_event("text", "ok")
        return resp
    ladder.stream = stream
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda *a: "ok", system="", model=model)
    r.srv = srv
    srv._loop_runner = r
    return r, bodies


def _session(r, agent, model_id):
    s = r.sessions.create(title=agent)
    s["agent"] = agent
    s["model"] = {"id": model_id, "providerID": "membrane"}
    r.sessions.sessions[s["id"]] = s
    return s["id"]


def _stamp(r, sid):
    rows = [x for x in r.messages.list(sid) if x["info"]["role"] == "assistant"]
    return rows[-1]["info"]["modelID"]


class TestTheLoopRunsOnTheRunnersModel(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv)

    def test_a_loop_session_that_remembers_another_model_runs_on_the_runners(self):
        sid = _session(self.r, LOOP, "glm-5.3-flash:cloud")
        self.r.run(sid, "work the goal")
        self.assertEqual(self.bodies[-1]["model"], "qwen3:8b")
        self.assertEqual(_stamp(self.r, sid), "qwen3:8b")

    def test_a_loop_session_with_no_model_is_stamped_with_what_ran(self):
        sid = _session(self.r, LOOP, "")
        self.r.run(sid, "work the goal")
        self.assertEqual(_stamp(self.r, sid), "qwen3:8b")     # not a hardcoded glm

    def test_changing_the_goal_worker_takes_effect_on_the_next_loop_turn(self):
        sid = _session(self.r, LOOP, "glm-5.3-flash:cloud")
        self.r.run(sid, "one")
        MM.apply(self.srv, loops="gpt-oss:20b-cloud")
        self.r.run(sid, "two")
        self.assertEqual(self.bodies[-1]["model"], "gpt-oss:20b-cloud")
        self.assertEqual(_stamp(self.r, sid), "gpt-oss:20b-cloud")

    def test_a_persons_chat_keeps_its_own_model(self):
        sid = _session(self.r, "build", "glm-5.3-flash:cloud")
        self.r.run(sid, "hello")
        self.assertEqual(self.bodies[-1]["model"], "glm-5.3-flash:cloud")
        self.assertEqual(_stamp(self.r, sid), "glm-5.3-flash:cloud")


class TestClaudeOnABackgroundLane(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv)

    def test_no_pick_the_guard_holds(self):
        self.r.model = CLAUDE                          # e.g. a boot default, never picked
        sid = _session(self.r, LOOP, "")
        self.r.run(sid, "work")
        self.assertFalse(CA.is_rung_name(self.bodies[-1]["model"]))
        self.assertNotIn(CA.AUTH_KEY, self.bodies[-1])

    def test_an_explicit_pick_runs_the_rung_with_the_minds_credentials(self):
        MM.apply(self.srv, loops=CLAUDE, loops_auth="api-key")
        sid = _session(self.r, LOOP, "")
        self.r.run(sid, "work")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertEqual(self.bodies[-1][CA.AUTH_KEY], CA.MODE_KEY)
        self.assertEqual(_stamp(self.r, sid), CLAUDE)

    def test_same_as_the_chat_sends_no_override(self):
        MM.apply(self.srv, loops=CLAUDE)               # default credentials: the chat's
        sid = _session(self.r, LOOP, "")
        self.r.run(sid, "work")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertNotIn(CA.AUTH_KEY, self.bodies[-1])

    def test_the_chat_is_unaffected_by_a_minds_credentials(self):
        MM.apply(self.srv, loops=CLAUDE, loops_auth="api-key")
        sid = _session(self.r, "build", CLAUDE)
        self.r.run(sid, "hello")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertNotIn(CA.AUTH_KEY, self.bodies[-1])


class TestTheCredentialSeam(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_default_follows_the_chats_mode(self):
        env = {"MEMBRANE_CLAUDE_AGENT": "login", "ANTHROPIC_API_KEY": "sk-ant-fixture"}
        self.assertEqual(CA.credential_env(self.home, env)[CA.KEY_ENV], "")          # login
        self.assertEqual(CA.credential_env(self.home, env, mode=None)[CA.KEY_ENV], "")
        env2 = {"ANTHROPIC_API_KEY": "sk-ant-fixture"}
        self.assertEqual(CA.credential_env(self.home, env2)[CA.KEY_ENV], "sk-ant-fixture")

    def test_an_override_uses_that_mode_for_that_call_only(self):
        env = {"MEMBRANE_CLAUDE_AGENT": "login", "ANTHROPIC_API_KEY": "sk-ant-fixture"}
        self.assertEqual(CA.credential_env(self.home, env, mode=CA.MODE_KEY)[CA.KEY_ENV],
                         "sk-ant-fixture")
        self.assertEqual(CA.credential_env(self.home, env)[CA.KEY_ENV], "")   # the chat's stays login
        env2 = {"ANTHROPIC_API_KEY": "sk-ant-fixture"}
        self.assertEqual(CA.credential_env(self.home, env2, mode=CA.MODE_LOGIN)[CA.KEY_ENV], "")

    def test_the_reply_carries_the_bodys_mode_to_the_credentials(self):
        seen = []
        with mock.patch.object(CA, "check_available", lambda *a, **k: object()), \
                mock.patch.object(CA, "credential_env", lambda *a, **k: seen.append(k.get("mode")) or {}), \
                mock.patch.object(CA, "_consume", lambda *a, **k: _noop()):
            CA.open_reply({"model": CLAUDE, "messages": [], CA.AUTH_KEY: CA.MODE_KEY}).read()
            CA.open_reply({"model": CLAUDE, "messages": []}).read()
        self.assertEqual(seen, [CA.MODE_KEY, None])


async def _noop():
    return None


class TestTheCortex(unittest.TestCase):
    def _chat(self, srv):
        from membrane import rag
        return rag._model_chat(srv)

    def test_no_pick_no_call(self):
        srv = _Srv(tempfile.mkdtemp())
        srv.idle_cortex_model = CLAUDE
        with mock.patch.object(CA, "open_reply") as op:
            out = self._chat(srv)("", CLAUDE, [{"role": "user", "content": "hi"}])
        self.assertEqual(out, "")
        op.assert_not_called()

    def test_an_explicit_pick_runs_with_the_cortex_credentials(self):
        srv = _Srv(tempfile.mkdtemp())
        MM.apply(srv, cortex=CLAUDE, cortex_auth="login")
        reply = mock.Mock()
        reply.read.return_value = json.dumps(
            {"choices": [{"message": {"content": " thought "}}]}).encode()
        with mock.patch.object(CA, "open_reply", return_value=reply) as op:
            out = self._chat(srv)("", CLAUDE, [{"role": "user", "content": "hi"}])
        self.assertEqual(out, "thought")
        body = op.call_args[0][0]
        self.assertEqual(body["model"], CLAUDE)
        self.assertEqual(body[CA.AUTH_KEY], CA.MODE_LOGIN)
        self.assertFalse(body.get("stream"))


class TestTheMindsState(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        _runner(self.srv)

    def test_credentials_default_to_the_chats_and_persist(self):
        self.assertEqual(MM.read(self.srv)["loops_auth"], "chat")
        self.assertEqual(MM.read(self.srv)["cortex_auth"], "chat")
        MM.apply(self.srv, loops_auth="api-key")
        self.assertEqual(json.load(open(self.srv._models_path))["loops_auth"], "api-key")
        fresh = _Srv(Path(self.srv._models_path).parent)
        _runner(fresh)
        MM.load(fresh)
        self.assertEqual(MM.read(fresh)["loops_auth"], "api-key")
        self.assertEqual(MM.credential_mode(fresh, "loops"), CA.MODE_KEY)
        self.assertIsNone(MM.credential_mode(fresh, "cortex"))

    def test_a_bad_credentials_choice_is_refused(self):
        with self.assertRaises(ValueError):
            MM.apply(self.srv, loops_auth="password")
        self.assertFalse(os.path.exists(self.srv._models_path))

    def test_a_saved_pick_is_an_explicit_pick_after_a_restart(self):
        MM.apply(self.srv, loops=CLAUDE)
        fresh = _Srv(Path(self.srv._models_path).parent)
        _runner(fresh)
        MM.load(fresh)
        self.assertTrue(MM.claude_allowed(fresh, "loops", CLAUDE))
        self.assertFalse(MM.claude_allowed(fresh, "cortex", CLAUDE))


class TestTheOptionsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_server import _CFG, _store
        cls.d = tempfile.mkdtemp()
        cls.srv = _Srv(cls.d)
        r, _ = _runner(cls.srv)
        handler = MS.make_handler(hub=MS.SSEHub(), sessions=_store(), messages=MS.MessageStore(),
                                  config=_CFG, runner=r)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _req(self, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/membrane/options",
                                     headers={"Content-Type": "application/json"} if data else {},
                                     method="POST" if data else "GET", data=data)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _known(self):
        return mock.patch.object(MM, "pickable", lambda srv: ["qwen3:8b", "qwen3:14b", CLAUDE])

    def test_get_has_the_background_section(self):
        with self._known():
            code, o = self._req()
        self.assertEqual(code, 200)
        bg = o["background"]
        self.assertEqual(bg["loops"], self.srv._loop_runner.model)
        self.assertEqual(bg["cortex"], self.srv.idle_cortex_model)
        self.assertIn(bg["loops_auth"], MM.AUTH_CHOICES)
        self.assertIn(CLAUDE, bg["models"])
        self.assertIn(CLAUDE, bg["claude"])
        self.assertIn(bg["chat_auth"], (CA.MODE_KEY, CA.MODE_LOGIN))

    def test_post_sets_the_goal_worker_through_minds_models(self):
        with self._known():
            code, o = self._req({"loops": "qwen3:14b", "cortex_auth": "login"})
        self.assertEqual(code, 200, o)
        self.assertEqual(self.srv._loop_runner.model, "qwen3:14b")
        self.assertEqual(o["background"]["loops"], "qwen3:14b")
        self.assertEqual(o["background"]["cortex_auth"], "login")
        saved = json.load(open(self.srv._models_path))
        self.assertEqual((saved["loops"], saved["cortex_auth"]), ("qwen3:14b", "login"))
        self.assertEqual(MM.read(self.srv)["loops"], "qwen3:14b")     # the same state /membrane/models reads

    def test_post_refuses_an_unknown_model_and_writes_nothing(self):
        before = self.srv._loop_runner.model
        with self._known():
            code, o = self._req({"loops": "not-served:1b"})
        self.assertEqual(code, 400)
        self.assertTrue(o.get("error"))
        self.assertEqual(self.srv._loop_runner.model, before)


class TestOptionsWithoutASeat(unittest.TestCase):
    def test_the_minds_keys_need_a_seat(self):
        with self.assertRaises(ValueError):
            O.apply({"loops": "qwen3:8b"})


if __name__ == "__main__":
    unittest.main()


class TestAFieldCall(unittest.TestCase):
    """A field call (an entity script asking a model, the 'plan' lane) is
    background work: it follows the loops' Minds pick, else the idle
    thinker's local model -- never a second local LLM (the seat's 14b next
    to the thinker's 8b made Ollama evict and reload, 60 s on a person's
    turn, 2026-10-29)."""

    FC = "field-call: membrane::Component:spec-suite-done"

    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv, model=CLAUDE)

    def test_follows_the_loops_claude_pick(self):
        MM.apply(self.srv, loops=CLAUDE, loops_auth="api-key")
        sid = _session(self.r, self.FC, CLAUDE)
        self.r.run(sid, "is the suite done?")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertEqual(self.bodies[-1][CA.AUTH_KEY], CA.MODE_KEY)

    def test_without_a_pick_runs_on_the_thinkers_model(self):
        sid = _session(self.r, self.FC, CLAUDE)
        self.r.run(sid, "is the suite done?")
        self.assertEqual(self.bodies[-1]["model"], "qwen3:8b")

    def test_no_thinker_model_keeps_the_seat_default(self):
        self.srv.idle_cortex_model = ""
        sid = _session(self.r, self.FC, CLAUDE)
        self.r.run(sid, "is the suite done?")
        self.assertEqual(self.bodies[-1]["model"], "qwen3:14b")


class TestATriagePass(unittest.TestCase):
    """#729: a watch triage pass runs on the seat's local idle model -- never
    on Claude, even when the loops are picked onto a Claude rung."""

    TR = "triage: membrane::Component:bilge-pump"

    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv, model=CLAUDE)

    def test_runs_on_the_thinkers_model_despite_a_claude_loops_pick(self):
        MM.apply(self.srv, loops=CLAUDE, loops_auth="api-key")
        sid = _session(self.r, self.TR, CLAUDE)
        self.r.run(sid, "a watch event")
        self.assertEqual(self.bodies[-1]["model"], "qwen3:8b")

    def test_without_a_pick_runs_on_the_thinkers_model(self):
        sid = _session(self.r, self.TR, CLAUDE)
        self.r.run(sid, "a watch event")
        self.assertEqual(self.bodies[-1]["model"], "qwen3:8b")
