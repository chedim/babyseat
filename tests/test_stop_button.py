"""THE STOP BUTTON (2026-10-09, the operator: "the stop button is not
stopping", red first).

The main chat stays cosmetically busy, so Stop showed with nothing to stop
and pressing it changed nothing; worse, the abort flag it set lingered and
cut the NEXT turn's tools short. Pinned: Stop on an idle session reports
an honest idle and leaves no flag; Stop on a running turn sets the flag
and closes the turn's model socket (a blocked round fails now).
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402


class _Sock:
    closed = False

    def close(self):
        self.closed = True


class TestStop(unittest.TestCase):
    def _runner(self):
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                         lambda b: {"choices": [{"message": {"content": "ok"}}]},
                         lambda n, a, c: "ok", system="", model="m")
        r.cosmetic_busy = True
        return r

    def test_stop_with_nothing_running_goes_idle_and_leaves_no_flag(self):
        r = self._runner()
        sid = r.sessions.create(title="t")["id"]
        q = r.hub.subscribe()
        r.abort(sid)
        evs = []
        while not q.empty():
            evs.append(q.get())
        self.assertIn("session.idle", [e.get("type") for e in evs])
        self.assertNotIn(sid, r._aborting)

    def test_stop_mid_turn_flags_and_cuts_the_model_call(self):
        r = self._runner()
        sid = r.sessions.create(title="t")["id"]
        r._busy[sid] = {"type": "busy"}
        sock = _Sock()
        r._turn_sockets = {sid: sock}
        r.abort(sid)
        self.assertIn(sid, r._aborting)
        self.assertTrue(sock.closed)

    def test_a_stale_stop_never_cuts_the_next_turn(self):
        calls = []
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                         _TwoRounds(), lambda n, a, c: calls.append(n) or "ok",
                         system="", model="m")
        sid = r.sessions.create(title="t")["id"]
        r.abort(sid)                                   # pressed while idle
        r.run(sid, "go", user_info={"id": "msg_u1", "role": "user", "agent": "build"})
        self.assertTrue(calls)                         # the tool still ran


class _TwoRounds:
    def __init__(self):
        self.n = 0

    def __call__(self, body):
        self.n += 1
        if self.n == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "state", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"content": "done"}}]}


if __name__ == "__main__":
    unittest.main()
