"""#915: a turn posted as 'build' onto a planner:/worker: loop session (live: planner ses_c3a7,
goal tag ses_e257) must not turn it into a person chat -- else latest_chat picks it and
goal-ended lands there."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane.server import TurnRunner  # noqa: E402


class _Sessions:
    def __init__(self):
        self.sessions = {"ses_c3a7": {"id": "ses_c3a7", "agent": "planner: goal", "title": "planner: goal"},
                         "ses_e257": {"id": "ses_e257", "agent": "build", "title": "chat"}}


class _R:
    _turn_agent = TurnRunner._turn_agent

    def __init__(self):
        self.sessions = _Sessions()
        self._agents = {"ses_c3a7": "planner: goal", "ses_e257": "build"}


def test_build_turn_on_planner_session_stays_loop():
    r = _R()
    agent = r._turn_agent("ses_c3a7", {"agent": "build"})     # the worker's post carries build
    r._agents["ses_c3a7"] = agent                             # as _quota_hold / the turn does
    assert CIO._is_loop(r, "ses_c3a7"), agent
    assert not CIO._is_loop(r, "ses_e257")


def test_person_turn_still_build():
    r = _R()
    assert r._turn_agent("ses_e257", {"agent": "build"}) == "build"


# ---- through the real HTTP door: the body carries agent "build" (PR 917 review) ----------

def _post_build_through_door(clear_agents):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from membrane import server as NS
    from tests.test_server import _CFG, _store

    sessions = _store()
    sid = sessions.create(title="planner: goal")["id"]
    sessions.sessions[sid]["agent"] = "planner: goal"

    class _Runner:
        _turn_agent = TurnRunner._turn_agent

        def __init__(self):
            self.sessions = sessions
            self._agents = {sid: "planner: goal"}

        def run(self, sid_, text, user_info=None, user_parts=None):
            self._agents[sid_] = self._turn_agent(sid_, user_info)
            return {"info": {"id": "m"}, "parts": []}

    runner = _Runner()
    handler = NS.make_handler(hub=NS.SSEHub(), sessions=sessions, messages=NS.MessageStore(),
                              config=_CFG, runner=runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/session/{sid}/message",
            data=json.dumps({"agent": "build", "parts": [{"type": "text", "text": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=20).read()
    finally:
        httpd.shutdown()
    live = CIO._is_loop(runner, sid)
    if clear_agents:
        runner._agents.clear()
    return live, CIO._is_loop(runner, sid), sessions.sessions[sid]["agent"]


def test_door_build_body_keeps_planner_session_loop_live():
    live, _after, row_agent = _post_build_through_door(False)
    assert live and row_agent == "planner: goal"


def test_door_build_body_keeps_planner_session_loop_after_restart():
    _live, after, row_agent = _post_build_through_door(True)
    assert after and row_agent == "planner: goal"
