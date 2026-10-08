"""THE LISTENERS' HANDS NEVER REACHED THEM (2026-10-07, red first).

The HA seat's operator plants an intent ("run uname -a"); a watch loop
receives it and must LINK the command to tool:executor -- the exec link
is the listeners' hand (the lane gate). On the ThinkPhone every loop
turn was refused as the OPERATOR instead (audit: lane-refused, lane
operator, tool entity_link, sid 'loop::watch: membrane::goal:...'):

  * the boot's loop-session maker read `srv.__loop_runner__` -- the
    runner lives at `srv._loop_runner` -- so it ALWAYS fell back to the
    bare 'loop::<agent>' key, a session the runner's store never knew;
  * the runner's agent registry then found no session doc and laned the
    turn 'build' -> operator -> entity_link refused -> nothing executes.

These tests drive the REAL maker and the REAL runner (the old
TestLoopSessionIds re-implemented the closure and pinned the bug).
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.opencode as OC  # noqa: E402
import membrane.server as MS  # noqa: E402

AGENT = "watch: membrane::goal:7d34fc06aff6"


def _runner():
    hub = MS.SSEHub()
    msgs = MS.MessageStore()
    sessions = MS.SessionStore()
    cell = {"runner": None}
    ladder = MS.loopback_ladder(11599, cell=cell)
    r = MS.TurnRunner(hub, sessions, msgs, ladder, None, system="",
                      model="m")
    cell["runner"] = r
    return r


class _Srv:
    def __init__(self, runner=None):
        self._loop_runner = runner
        self.entity_store = None


class TestLoopSessionMaker(unittest.TestCase):
    def test_loop_session_lands_in_the_runners_store(self):
        """With the serve runner wired, a loop session is a REAL session
        in the runner's store, carrying the loop's agent."""
        runner = _runner()
        sid = OC.loop_session(_Srv(runner), AGENT)
        self.assertIsInstance(sid, str)
        self.assertTrue(sid.startswith("ses"), sid)
        self.assertIn(sid, runner.sessions.sessions)
        self.assertEqual(runner.sessions.sessions[sid]["agent"], AGENT)

    def test_no_runner_degrades_to_the_bare_key(self):
        """Proxy-only seat (no serve runner): the degenerate key, a plain
        string (never a function object)."""
        sid = OC.loop_session(_Srv(None), AGENT)
        self.assertEqual(sid, "loop::" + AGENT)


class TestLoopTurnLane(unittest.TestCase):
    def test_loop_session_turn_is_the_listener_lane(self):
        """The runner resolves the loop session's agent -> the LISTENER
        lane, whose hands include the exec link."""
        runner = _runner()
        sid = OC.loop_session(_Srv(runner), AGENT)
        agent = runner._turn_agent(sid, None)
        self.assertEqual(agent, AGENT)
        lane = MS._lane_of(agent)
        self.assertEqual(lane, "listener")
        self.assertIsNone(MS._lane_refusal("entity_link", lane))
        self.assertIsNone(MS._lane_refusal("entity_add", lane))

    def test_bare_loop_key_never_lanes_as_operator(self):
        """THE BACKSTOP: a bare 'loop::<agent>' sid with no session doc
        resolves to its own agent, never the 'build' default."""
        runner = _runner()
        agent = runner._turn_agent("loop::" + AGENT, None)
        self.assertEqual(agent, AGENT)
        self.assertEqual(MS._lane_of(agent), "listener")

    def test_turn_agent_precedence(self):
        """The turn's own agent wins; then the session doc; plain chat
        sessions stay 'build' (the operator keeps its lane)."""
        runner = _runner()
        s = runner.sessions.create(title="chat")
        self.assertEqual(runner._turn_agent(s["id"], None), "build")
        self.assertEqual(runner._turn_agent(s["id"], {"agent": "plan"}),
                         "plan")
        self.assertEqual(MS._lane_of(runner._turn_agent(s["id"], None)),
                         "operator")
        self.assertEqual(runner._turn_agent("ses_unknown", None), "build")


class TestMintedRowAgent(unittest.TestCase):
    """THE DOOR'S ROW (the second half of the exhibit): run() mints the
    user row when the caller passes none -- the loops always do -- and
    the row's agent was hardcoded 'build'. The turn's own agent wins the
    lane registry, so every loop turn laned OPERATOR even with a correct
    session doc (audit after the first fix: lane-refused, operator,
    entity_link, sid ses_875a... whose doc said watch:)."""

    def test_loop_turn_row_carries_the_loops_agent(self):
        runner = _runner()
        sid = OC.loop_session(_Srv(runner), AGENT)
        info, parts = runner._mint_user_row(sid, "goal planted: ...")
        self.assertEqual(info["agent"], AGENT)
        self.assertEqual(MS._lane_of(runner._turn_agent(sid, info)),
                         "listener")
        self.assertEqual(parts[0]["text"], "goal planted: ...")

    def test_chat_turn_row_stays_build(self):
        runner = _runner()
        s = runner.sessions.create(title="chat")
        info, _ = runner._mint_user_row(s["id"], "hi")
        self.assertEqual(info["agent"], "build")


class TestLoopSessionDurable(unittest.TestCase):
    def test_loop_agent_is_persisted(self):
        """The agent must survive a restart: create() flushes the doc as
        'build' before the maker stamps the agent -- the stamp must be
        flushed too (exhibit: ses_c47c..., titled 'watch: ...goal', came
        back from a restart as agent build)."""
        import json
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "serve-sessions.json")
        hub = MS.SSEHub()
        sessions = MS.SessionStore(path)
        r = MS.TurnRunner(hub, sessions, MS.MessageStore(), None, None,
                          system="", model="m")
        sid = OC.loop_session(_Srv(r), AGENT)
        raw = json.load(open(path))
        docs = raw.get("sessions", raw) if isinstance(raw, dict) else raw
        doc = docs[sid] if isinstance(docs, dict) else \
            next(x for x in docs if x["id"] == sid)
        self.assertEqual(doc["agent"], AGENT)


if __name__ == "__main__":
    unittest.main()
