"""THE OPERATOR SPEAKS INTO A RUNNING TURN (2026-10-08, red first).

A message sent while the seat was mid-turn only QUEUED: it ran as the next
turn, after the current one finished. On a 9-minute agentic turn (the
getter saga) the operator's four messages -- one of them the very hint the
turn needed ("the mqtt auth should be in the same folder") -- waited the
whole nine minutes.

Pinned: between tool rounds the running turn takes the session's queued
messages into its history (the model's next step sees them, in order);
each one is consumed -- its waiter released, never replayed as a turn of
its own; a message that arrives after the last round still queues.
"""

import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402


def _tool_call(i):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": "state", "arguments": "{}"}}]}}]}


def _final(text):
    return {"choices": [{"message": {"content": text}}]}


class TestMidTurn(unittest.TestCase):
    def _runner(self, ladder, executor):
        return MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                             executor, system="", model="m", tools=[])

    def test_a_queued_message_lands_in_the_running_turn(self):
        bodies = []
        replies = [_tool_call(1), _tool_call(2), _final("got both")]

        def ladder(body):
            bodies.append([dict(m) for m in body["messages"]])
            return replies.pop(0)

        state = {}

        def executor(name, args, call_id):
            if call_id == "c1":                       # the operator speaks mid-turn
                r = state["r"]
                for t in ("also, the mqtt auth is in the same folder", "and check the bridge"):
                    info, parts = r._mint_user_row(state["sid"], t)
                    r.messages.append(state["sid"], info, parts)
                    ev = threading.Event()
                    state.setdefault("waiters", []).append((r._enqueue(state["sid"], t, info, parts, ev), ev))
            return "ok"

        r = self._runner(ladder, executor)
        sid = r.sessions.create(title="t")["id"]
        state.update(r=r, sid=sid)
        r.run(sid, "fix the getter")
        users = [m["content"] for m in bodies[1] if m["role"] == "user"]
        self.assertIn("also, the mqtt auth is in the same folder", users[-2])
        self.assertIn("and check the bridge", users[-1])
        self.assertEqual(r._queued_depth(sid), 0)
        for qid, ev in state["waiters"]:
            self.assertTrue(ev.is_set())
            self.assertEqual((r._drain_results.get(qid) or {}).get("status"), "merged")
        self.assertEqual(len(bodies), 3)                  # no replayed turns

    def test_a_message_after_the_last_round_still_queues(self):
        r = self._runner(lambda body: _final("done"), lambda *a: "ok")
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "hi")
        info, parts = r._mint_user_row(sid, "later")
        r.messages.append(sid, info, parts)
        ev = threading.Event()
        with r._queue_lock:
            r._queue.setdefault(sid, []).append({"qid": "q1", "text": "later", "user_info": info,
                                                 "user_parts": parts, "done": ev})
        self.assertEqual(r._queued_depth(sid), 1)     # nothing running took it


if __name__ == "__main__":
    unittest.main()
