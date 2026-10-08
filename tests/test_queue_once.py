"""THE ENDLESS REPLY (2026-10-08, red first): two messages queued behind a
running turn were answered 11x and 7x, alternating, until the serve died.

The race: the POSTing thread enqueued its turn and then WAITED FOR THE
GUARD itself; the running turn's finally released the guard AND drained
the same entry into a worker. Whichever lost the race re-entered run(),
found the session busy and ENQUEUED THE SAME TURN AGAIN -- every finished
turn re-armed it. (Hidden while the dashboard disabled Send during a
turn; the v2 panel's "Queue" button exposed it.)

The contract: a queued turn runs EXACTLY ONCE, in arrival order; the drain
owns queued turns; the POSTing thread only waits for the result.
"""
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
    r._turn_watchdog = lambda *a, **k: None      # no 120s abort timer in a unit test
    return r


class TestQueuedTurnsRunOnce(unittest.TestCase):
    def _go(self, r, sid, texts, gap=0.05):
        results = {}

        def call(t):
            results[t] = r.run(sid, t)
        threads = []
        for t in texts:
            th = threading.Thread(target=call, args=(t,), daemon=True)
            th.start()
            threads.append(th)
            time.sleep(gap)
        for th in threads:
            th.join(timeout=15)
        return results

    def test_each_queued_turn_runs_exactly_once_in_order(self):
        calls = []
        r = _runner(calls)
        sid = r.sessions.create(title="chat")["id"]
        self._go(r, sid, ["A", "B", "C"])
        time.sleep(1.2)                         # any re-armed replay would land here
        self.assertEqual(calls, ["A", "B", "C"])
        self.assertEqual(r._queued_depth(sid), 0)

    def test_the_waiting_caller_gets_its_own_result(self):
        calls = []
        r = _runner(calls)
        sid = r.sessions.create(title="chat")["id"]
        res = self._go(r, sid, ["A", "B"])
        self.assertEqual(res["A"].get("text"), "A")
        self.assertEqual(res["B"].get("text"), "B")

    def test_a_turn_ending_between_busy_check_and_enqueue_is_not_stranded(self):
        """The kick: if the running turn finished after the queued caller's
        failed acquire but before its enqueue, nobody would drain it."""
        calls = []
        r = _runner(calls, hold=0.05)
        sid = r.sessions.create(title="chat")["id"]
        guard = r._guard(sid)
        guard.acquire()                         # 'busy' at the check...
        orig = r._enqueue

        def enqueue_after_release(*a, **k):
            guard.release()                     # ...the turn ends right here
            return orig(*a, **k)
        r._enqueue = enqueue_after_release
        out = r.run(sid, "late")
        self.assertEqual(calls, ["late"])
        self.assertEqual(out.get("text"), "late")


if __name__ == "__main__":
    unittest.main()
