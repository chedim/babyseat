"""Issue #970 (red first): messages that wait during a running turn must reach
the model TOGETHER in ONE next turn, not one turn per message."""
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


def _runner(calls, hold=0.3):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                      None, None, system="", model="m")
    r.busy_wait_s = 10
    r.queue_depth_max = 8

    def fake_inner(sid, text, user_info=None, user_parts=None):
        calls.append(text)
        time.sleep(hold)
        return {"info": {"id": "a-" + text, "role": "assistant"}, "parts": [],
                "text": text}
    r._run_inner = fake_inner
    r._turn_watchdog = lambda *a, **k: None
    return r


class TestWaitingMessagesDeliveredTogether(unittest.TestCase):
    def test_three_waiting_messages_make_one_next_turn(self):
        calls = []
        r = _runner(calls, hold=0.5)
        sid = r.sessions.create(title="chat")["id"]
        ths = []
        for t in ["A", "B", "C", "D"]:      # A runs; B, C, D arrive during it
            th = threading.Thread(target=r.run, args=(sid, t), daemon=True)
            th.start()
            ths.append(th)
            time.sleep(0.05)
            if t == "A":
                for _ in range(100):
                    if calls:
                        break
                    time.sleep(0.02)
        for th in ths:
            th.join(timeout=15)
        time.sleep(1.0)
        self.assertEqual(len(calls), 2, f"expected A then ONE joint turn, got {calls}")
        for m in ("B", "C", "D"):
            self.assertIn(m, calls[1])


if __name__ == "__main__":
    unittest.main()
