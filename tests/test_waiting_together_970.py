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


class TestEachWaitingMessageReachesTheModelOnce(unittest.TestCase):
    def test_the_batched_turn_does_not_replay_its_own_messages(self):
        # review 2026-11-10: run() stores every message's row when it arrives; the batched turn must leave ALL of
        # its messages out of the replayed history (they are in its joined text), not only the first
        import tempfile
        d = tempfile.mkdtemp()
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(str(Path(d) / "s.json")), MS.MessageStore(str(Path(d) / "m.json")),
                          None, None, system="", model="m")
        sid = r.sessions.create(title="chat")["id"]
        sent = []
        orig = r._prior_history

        def spy(s, exclude, anchor=False, also=None):
            h = orig(s, exclude, anchor=anchor, also=also)
            sent.append([m["content"] for m in h])
            return h
        r._prior_history = spy
        rows = {}
        for t in ("B", "C", "D"):
            info = {"id": "msg_" + t, "sessionID": sid, "role": "user", "time": {"created": time.time()}}
            parts = [{"id": "prt_" + t, "type": "text", "text": t}]
            r.messages.append(sid, info, parts)
            rows[t] = (info, parts)
        batch_info = dict(rows["B"][0], _batch_ids=["msg_B", "msg_C", "msg_D"])
        try:
            r._run_inner(sid, "B\n\nC\n\nD", user_info=batch_info, user_parts=rows["B"][1])
        except Exception:
            pass                                  # no model here: only the history it built matters
        self.assertTrue(sent, "the turn never built its history")
        for t in ("B", "C", "D"):
            self.assertNotIn(t, sent[0], f"{t} replayed as an earlier message and sent again in the joined text")

    def test_the_batch_carries_every_message_id(self):
        calls, infos = [], []
        r = _runner(calls, hold=0.5)

        def fake_inner(sid, text, user_info=None, user_parts=None):
            calls.append(text)
            infos.append(dict(user_info or {}))
            time.sleep(0.5)
            return {"info": {"id": "a", "role": "assistant"}, "parts": [], "text": text}
        r._run_inner = fake_inner
        sid = r.sessions.create(title="chat")["id"]
        ths = []
        for t in ["A", "B", "C"]:
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
        self.assertEqual(len(calls), 2, calls)
        self.assertEqual(len(infos[1].get("_batch_ids") or []), 2, infos[1])
