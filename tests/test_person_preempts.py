"""A PERSON SPOKE (2026-10-10, the operator: "interrupt the chat loop whenever
there is a message"): a person's message to a busy session cuts the running
turn and runs next; loops and goal pulses still wait their turn."""
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


def _runner(log, hold=3.0):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                      None, None, system="", model="m")
    r.busy_wait_s = 15
    r.queue_depth_max = 8

    def fake_inner(sid, text, user_info=None, user_parts=None):
        t0 = time.time()
        log.append(("start", text))
        while time.time() - t0 < hold:            # rounds; the abort flag is read between them
            if sid in r._aborting:
                log.append(("cut", text, round(time.time() - t0, 2)))
                return {"info": {"id": "a", "role": "assistant"}, "parts": [], "text": "cut"}
            time.sleep(0.05)
        log.append(("done", text))
        return {"info": {"id": "a", "role": "assistant"}, "parts": [], "text": text}
    r._run_inner = fake_inner
    r._turn_watchdog = lambda *a, **k: None
    return r


class TestPersonPreempts(unittest.TestCase):
    def _two(self, first, second, gap=0.3):
        log = []
        r = _runner(log)
        a = threading.Thread(target=r.run, args=("s1", first), daemon=True)
        a.start()
        time.sleep(gap)
        t0 = time.time()
        b = threading.Thread(target=r.run, args=("s1", second), daemon=True)
        b.start()
        a.join(10); b.join(10)
        return log, time.time() - t0

    def test_a_person_cuts_a_running_goal_turn(self):
        log, waited = self._two("goal still open (the board's pulse): x", "[HA user: Dmitrii]\nhey Argus")
        self.assertEqual(log[1][0], "cut")
        self.assertEqual(log[2], ("start", "[HA user: Dmitrii]\nhey Argus"))
        self.assertLess(log[1][2], 1.0)            # cut within a round, not after the 3 s turn

    def test_a_loop_message_still_waits(self):
        log, _ = self._two("[HA user: Dmitrii]\nfirst", "goal still open (the board's pulse): y")
        self.assertEqual(log[1], ("done", "[HA user: Dmitrii]\nfirst"))
        self.assertEqual(log[2][0], "start")

    def test_the_speaker_may_ride_its_own_part(self):
        self.assertTrue(MS._from_person("hey", [{"type": "text", "text": "[HA user: Rudy]", "synthetic": True}]))
        self.assertFalse(MS._from_person("[membrane voice] hello", []))
        self.assertFalse(MS._from_person("goal still open", None))

    def test_preempt_without_a_running_turn_is_nothing(self):
        r = _runner([])
        self.assertFalse(r.preempt("idle-session"))
        self.assertNotIn("idle-session", r._aborting)


if __name__ == "__main__":
    unittest.main()


class TestTheCutTurnTakesNoOnesWords(unittest.TestCase):
    """2026-10-11, "I don't see the answer": the cut turn's next round used to
    absorb the person's queued message as an interjection and then stop --
    consumed, never answered. Through the REAL turn loop: it must run as its
    own turn, and the cut turn's never-run tool call must close."""

    def test_the_person_is_answered_and_the_cut_call_closes(self):
        calls = []
        first = threading.Event()

        def ladder(body):
            msgs = body.get("messages") or []
            last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
            calls.append(last_user)
            if len(calls) == 1:                       # the running turn's round 1: slow, ends in TWO tool calls
                first.set()
                time.sleep(0.8)
                return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "find", "arguments": "{}"}},
                    {"id": "t2", "type": "function", "function": {"name": "find", "arguments": "{}"}}]},
                    "finish_reason": "tool_calls"}]}
            return {"choices": [{"message": {"role": "assistant", "content": f"answer to: {last_user[-30:]}"},
                                 "finish_reason": "stop"}]}

        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a, **k: "ok", system="", model="m", tools=[])
        r.busy_wait_s = 15
        r._turn_watchdog = lambda *a, **k: None
        sid = r.sessions.create()["id"] if hasattr(r.sessions, "create") else "s1"
        a = threading.Thread(target=r.run, args=(sid, "goal still open (the board's pulse): x"), daemon=True)
        a.start()
        first.wait(5)
        b = threading.Thread(target=r.run, args=(sid, "[HA user: Dmitrii]\nlet me know what you think"),
                             daemon=True)
        b.start()
        a.join(15); b.join(15)
        self.assertTrue(any("let me know what you think" in c for c in calls[1:]), calls)
        rows = r.messages.list(sid)
        texts = ["".join(p.get("text") or "" for p in row.get("parts") or [] if p.get("type") == "text")
                 for row in rows if (row.get("info") or {}).get("role") == "assistant"]
        self.assertTrue(any("let me know what you think" in t for t in texts), texts)
        pending = [p for row in rows for p in row.get("parts") or []
                   if p.get("type") == "tool" and (p.get("state") or {}).get("status") in ("pending", "running")]
        self.assertEqual(pending, [])
