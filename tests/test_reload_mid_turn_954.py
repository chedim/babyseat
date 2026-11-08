"""#954: a reload mid-turn. A fresh client loads GET /session/<id>/message (the
store's rows) while a turn is still running: the text and tool cards shown so
far must be in that history, and the live SSE must keep streaming the rest."""
import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402


class TestReloadMidTurn(unittest.TestCase):
    def test_fresh_client_sees_cards_so_far_and_streaming_continues(self):
        mid_turn, release = threading.Event(), threading.Event()

        def ladder(body):
            raise AssertionError("buffered fallback unexpected")

        def stream(body, on_event):
            on_event("text", "looking at the logs")
            on_event("tool_start", {"id": "t1", "name": "bash", "input": {"command": "ls"}})
            on_event("tool", {"id": "t1", "name": "bash", "input": {"command": "ls"},
                              "output": "a.txt"})
            on_event("text", " and then more")
            mid_turn.set()
            release.wait(10)
            on_event("text", " THE TAIL")
            return {"choices": [{"message": {"content": "looking at the logs and then more THE TAIL"}}]}
        ladder.stream = stream

        hub = MS.SSEHub()
        r = MS.TurnRunner(hub, MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="m", tools=[])
        sid = r.sessions.create(title="t")["id"]
        t = threading.Thread(target=r.run, args=(sid, "go"), daemon=True)
        t.start()
        self.assertTrue(mid_turn.wait(10))

        # the reload: a fresh client loads history, then subscribes to events
        rows = r.messages.list(sid)               # what GET /session/<id>/message serves
        q = hub.subscribe()
        parts = [p for row in rows if (row.get("info") or {}).get("role") == "assistant"
                 for p in row.get("parts") or []]
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        tools = [p for p in parts if p.get("type") == "tool"]
        try:
            self.assertIn("looking at the logs", text, "history lost the text shown so far")
            self.assertEqual([p.get("callID") for p in tools], ["t1"],
                             "history lost the tool card shown so far")
        finally:
            release.set()
            t.join(10)
        self.assertFalse(t.is_alive())

        # the live stream keeps going: drain q, reduce like panel.js does
        tp = [p for p in parts if p.get("type") == "text"]
        self.assertGreaterEqual(len(tp), 1)
        last = tp[-1]                       # the part still open: it gets the tail
        hist = {p["id"]: p["text"] for p in tp}   # ordered by part
        seen_tail = False
        while True:
            try:
                ev = q.get(timeout=0.5)
            except Exception:
                break
            if ev.get("type") == "message.part.delta" and ev.get("field", "text") == "text":
                if ev["partID"] in hist:
                    hist[ev["partID"]] += ev["delta"]
                if " THE TAIL" in ev["delta"]:
                    seen_tail = True
                    self.assertEqual((ev["messageID"], ev["partID"]),
                                     (last["messageID"], last["id"]))
            elif ev.get("type") == "message.part.updated":
                part = ev["part"]
                if part.get("type") == "text":
                    hist[part["id"]] = part["text"]     # updated replaces the part
                    if " THE TAIL" in part["text"]:
                        seen_tail = True
                        self.assertEqual((part["messageID"], part["id"]),
                                         (last["messageID"], last["id"]))
        self.assertTrue(seen_tail, "no event carried the tail")
        # the server trims the edges of each text part, so the two parts
        # (before / after the tool card) meet without the space: compare the
        # words, which still catches any duplicated or missing text
        final = " ".join(v.strip() for v in hist.values())
        self.assertEqual(final, "looking at the logs and then more THE TAIL")
        self.assertEqual(final.count("THE TAIL"), 1)


if __name__ == "__main__":
    unittest.main()
