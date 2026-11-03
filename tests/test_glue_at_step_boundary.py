"""GLUE AT THE STEP BOUNDARY: a person's message to a running turn does not
cancel its running tool call; it is enqueued into the turn and shows up in the
next model round of the SAME turn. Stop/pause/operator cancel are unchanged,
and with no turn running preempt is nothing (old path)."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402

SAID = "[HA user: Dmitrii]\nalso check the garage"


class TestGlueAtStepBoundary(unittest.TestCase):
    def test_message_mid_tool_call_joins_next_round_of_same_turn(self):
        bodies, tool_state, threads = [], {}, []
        in_tool = threading.Event()

        def ladder(body):
            threads.append(threading.current_thread().ident)
            bodies.append([m for m in (body.get("messages") or [])])
            if len(bodies) == 1:
                return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "find", "arguments": "{}"}}]},
                    "finish_reason": "tool_calls"}]}
            return {"choices": [{"message": {"role": "assistant", "content": "ok both"},
                                 "finish_reason": "stop"}]}

        def tool(*a, **k):
            in_tool.set()
            time.sleep(0.8)                     # the running call, person speaks meanwhile
            tool_state["finished"] = True
            return "ok"

        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder, tool,
                          system="", model="m", tools=[])
        r.busy_wait_s = 15
        r._turn_watchdog = lambda *a, **k: None
        sid = r.sessions.create()["id"] if hasattr(r.sessions, "create") else "s1"
        a = threading.Thread(target=r.run, args=(sid, "goal still open (the board's pulse): x"), daemon=True)
        a.start()
        self.assertTrue(in_tool.wait(5))
        b = threading.Thread(target=r.run, args=(sid, SAID), daemon=True)
        b.start()
        a.join(15); b.join(15)
        self.assertTrue(tool_state.get("finished"))                     # the call was not cancelled
        tools = [p for row in r.messages.list(sid) for p in row.get("parts") or [] if p.get("type") == "tool"]
        self.assertEqual([(p.get("state") or {}).get("status") for p in tools], ["completed"])
        self.assertEqual(len(bodies), 2, "the message must not start a second turn")
        round2 = " ".join(str(m.get("content")) for m in bodies[1] if m.get("role") == "user")
        self.assertIn("also check the garage", round2)                  # next round of the same turn
        self.assertEqual(threads[0], threads[1], "round 2 must run in the same turn, not a drained one")
        self.assertIn("t1", str(bodies[1]))                              # round 1's call + result are in its history
        self.assertNotIn("not run", str(bodies[1]))

    def test_glued_preempt_sets_no_abort(self):
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), None, None, system="", model="m")
        r._busy["s"] = {"type": "busy"}
        self.assertTrue(r.preempt("s", glue=True))
        self.assertNotIn("s", r._aborting)

    def test_other_reasons_still_cut(self):
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), None, None, system="", model="m")
        r._busy["s"] = {"type": "busy"}
        self.assertTrue(r.preempt("s"))
        self.assertIn("s", r._aborting)

    def test_no_running_turn_falls_back(self):
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), None, None, system="", model="m")
        self.assertFalse(r.preempt("idle", glue=True))
        self.assertNotIn("idle", r._aborting)


if __name__ == "__main__":
    unittest.main()
