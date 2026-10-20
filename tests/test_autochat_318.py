"""#318: auto-chat works WITHOUT the voice; the babble role and the LLM switch are gone.

Auto-chat (⟳ in the chat, ▶/⏸ in membrane home's 3D-viewer pane) is the
LLM's turn that just keeps going, round after round, from the conversation
as it stands -- no message of anyone's. It ran only when the voice was booted
(MEMBRANE_VOICE, opt-in since 0.5.1), so with the voice off it did nothing.

Pinned here: rounds run with the voice off (also through the HTTP route the
toggles call); Stop ends them; an old auto-state.json that carries a role
still loads and resumes; no babble role, role switch or LLM switch is left
in the code or the UI; membrane home's toggle targets the person's chat,
never a mind's read-only view.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
sys.path.insert(0, str(ROOT))

from membrane import seat_panel as SP  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import voice as V  # noqa: E402

NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
_NO_VOICE = {k: v for k, v in os.environ.items() if k != "MEMBRANE_VOICE"}


class _Ladder:
    def __init__(self):
        self.n = 0
        self.last = None

    def __call__(self, body):
        self.n += 1
        self.last = body
        return {"choices": [{"message": {"content": "The battery is at ninety percent."}}]}


class _Srv:
    pass


def _runner(d=None):
    d = d or tempfile.mkdtemp()
    srv = _Srv()
    srv._models_path = str(Path(d) / "models.json")
    lad = _Ladder()
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lad,
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    return r, srv, lad


def _seed(r, sid):
    r.messages.append(sid, {"id": "msg_u0", "sessionID": sid, "role": "user", "time": {"created": 1}},
                      [{"id": "p0", "type": "text", "text": "check the battery"}])
    r.messages.append(sid, {"id": "msg_a0", "sessionID": sid, "role": "assistant",
                            "time": {"created": 2, "completed": 3}},
                      [{"id": "p1", "type": "text", "text": "The battery is at ninety percent."}])


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.03)
    return cond()


class TestAutoChatWithoutTheVoice(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, _NO_VOICE, clear=True)
        self._env.start()
        self._pause, V.AUTO_PAUSE_S = V.AUTO_PAUSE_S, 0.02

    def tearDown(self):
        V.AUTO_PAUSE_S = self._pause
        self._env.stop()

    def test_rounds_run_with_the_voice_off_until_stopped(self):
        r, srv, lad = _runner()
        self.assertIsNone(V.of(srv))                                   # the voice is off
        sid = r.sessions.create(title="t")["id"]
        _seed(r, sid)
        st = V.auto_set(r, sid, True)
        self.assertTrue(st["on"])
        self.assertNotIn("role", st)                                   # no role any more
        self.assertTrue(_wait(lambda: lad.n >= 3), lad.n)              # rounds go on by themselves
        self.assertGreaterEqual(V.auto_state(r, sid)["rounds"], 3)
        r.abort(sid)                                                   # Stop ends it
        self.assertFalse(V.auto_on(r, sid))
        time.sleep(0.3)
        n = lad.n
        time.sleep(0.4)
        self.assertLessEqual(lad.n, n + 1)                             # at most the round in flight
        users = [m["info"]["id"] for m in r.messages.list(sid) if m["info"].get("role") == "user"]
        self.assertEqual(users, ["msg_u0"])                            # no message of anyone's
        self.assertEqual(lad.last["messages"][-1]["role"], "assistant")  # the turn just keeps going
        blob = " ".join(str(m.get("content")) for m in lad.last["messages"])
        self.assertNotIn("[membrane voice]", blob)

    def test_the_toggle_route_starts_and_stops_it(self):
        """The call both toggles make: POST /session/{sid}/auto {on, mode}."""
        r, srv, lad = _runner()
        sid = r.sessions.create(title="t")["id"]
        _seed(r, sid)
        handler = S.make_handler(hub=r.hub, sessions=r.sessions, messages=r.messages,
                                 config={}, runner=r)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]

        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/session/{sid}/auto", method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Basic " + base64.b64encode(b"opencode:pw").decode()})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        try:
            on = post({"on": True, "mode": "explore"})
            self.assertTrue(on["on"])
            self.assertNotIn("role", on)
            self.assertTrue(_wait(lambda: lad.n >= 2), lad.n)
            off = post({"on": False})
            self.assertFalse(off["on"])
            self.assertFalse(V.auto_on(r, sid))
            self.assertNotIn("role", post({"role": "babble"}))         # an old client's role is ignored
        finally:
            r.abort(sid)
            httpd.shutdown()

    def test_the_seat_control_starts_it_and_ignores_a_role(self):
        from membrane.entities import EntityStore
        from membrane.watch_agents import LoopRunner
        r, srv, lad = _runner()
        srv._loop_runner = r
        srv.entity_store = EntityStore(path=None, shared=False)
        srv.paused = False
        srv.loops = LoopRunner(max_loops=3)
        sid = r.sessions.create(title="t")["id"]
        _seed(r, sid)
        self.assertNotIn("role", SP.CONTROLS)
        out = SP.control(srv, {"session": sid, "role": "babble", "auto": True})
        try:
            self.assertNotIn("role", out["applied"])
            self.assertTrue(V.auto_on(r, sid))
            self.assertTrue(_wait(lambda: lad.n >= 1), lad.n)
        finally:
            r.abort(sid)


class TestOldStateLoads(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, _NO_VOICE, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_an_old_auto_state_with_a_role_resumes_and_is_saved_without_it(self):
        d = tempfile.mkdtemp()
        r, srv, lad = _runner(d)
        sid = r.sessions.create(title="t")["id"]
        r._busy = {sid: 1}                                             # no rounds fire in this test
        p = Path(d) / "voice" / V.STATE_FILE
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"auto": {sid: {"mode": "teach"}}, "role": {sid: "babble", "x": "predict"}}))
        got = V.restore_state(r, delay=0.0)
        self.assertEqual(got["auto"], [sid])
        self.assertTrue(_wait(lambda: V.auto_on(r, sid), 3))
        self.assertEqual(V.auto_state(r, sid)["mode"], "teach")
        self.assertTrue(_wait(lambda: "role" not in json.loads(p.read_text()), 3))   # saved without it
        saved = json.loads(p.read_text())
        self.assertEqual(saved["auto"], {sid: {"mode": "teach"}})


class TestTheBabbleRoleIsGone(unittest.TestCase):
    def test_no_role_plumbing_in_the_voice(self):
        for name in ("ROLES", "role", "role_set", "babble_move", "frame_move", "frame", "frame_explore"):
            self.assertFalse(hasattr(V, name), name)

    def test_no_babble_hooks_in_the_server(self):
        src = (ROOT / "membrane" / "server.py").read_text()
        for s in ("babble_move", "_babble_threads", "role_set"):
            self.assertNotIn(s, src)

    def test_no_role_or_llm_switch_in_the_ui(self):
        panel = (UI / "panel.js").read_text()
        for s in ("rolebtn", "llmbtn", "_llmOn", "membrane_only", "roleNext", "roleView", "_toggleRole",
                  "membrane babbles", "membrane predicts"):
            self.assertNotIn(s, panel)
        home = (UI / "home.js").read_text()
        self.assertNotIn("a.role", home)


def _js(path, body):
    js = "const M=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(path), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_the_auto_event_carries_no_role():
    out = _js(UI / "panel.js", "const st = M.chatState('s1'); M.applyEvent(st, {type: 'membrane.auto',"
              " properties: {sessionID: 's1', on: true, rounds: 2, mode: 'explore', role: 'babble'}}); out = st.auto")
    assert out == {"on": True, "rounds": 2, "mode": "explore"}


@NODE
def test_the_toggle_targets_the_persons_chat_not_a_mind():
    out = _js(UI / "panel.js", "out = [M.autoSid('ses_1', null), M.autoSid('mind:thinker', 'ses_1'),"
              " M.autoSid('mind:thinker', null), M.autoSid(null, null)]")
    assert out == ["ses_1", "ses_1", None, None]


@NODE
def test_the_home_toggle_title_names_no_role():
    on = _js(UI / "home.js", "out = M.autoTitle({on: true, rounds: 4, mode: 'explore', role: 'predict'})")
    off = _js(UI / "home.js", "out = M.autoTitle({on: false})")
    none = _js(UI / "home.js", "out = M.autoTitle(null)")
    assert "round 4" in on and "pause" in on
    assert "play" in off and off == none
    for t in (on, off):
        for w in ("predict", "babble", "membrane and the LLM talk"):
            assert w not in t
