"""THE LEAD-IN THAT NEVER LEFT (2026-10-08, the transcript review, red first).

A model step that writes text AND calls tools ("Let me check the BMS." +
entity_read) kept the text only in the model's own history: no part was
published until the last, tool-free step. On a phone call that means
silence for the whole tool chain -- the caller can never hear "checking
the BMS..." before the lookup runs, whatever the phone note asks.

Pinned: the step's text is a real text part, streamed (opened, delta,
closed) BEFORE the tool part opens, and kept in the stored message.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402


class _Ladder:
    def __init__(self):
        self.n = 0

    def __call__(self, body):
        self.n += 1
        if self.n == 1:
            return {"choices": [{"message": {
                "content": "Let me check the BMS.",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "state", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"content": "It's at eighty percent."}}]}


class TestLeadIn(unittest.TestCase):
    def test_step_text_streams_before_its_tools(self):
        hub = MS.SSEHub()
        q = hub.subscribe()
        r = MS.TurnRunner(hub, MS.SessionStore(), MS.MessageStore(), _Ladder(),
                          lambda name, args, call_id: "ok", system="", model="m",
                          tools=[])
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "how's the battery?")
        evs = []
        while not q.empty():
            evs.append(q.get())
        texts, order = {}, []
        for ev in evs:
            props = ev.get("properties") or ev
            if ev.get("type") == "message.part.updated":
                p = props.get("part") or {}
                if p.get("messageID") == (out or {}).get("info", {}).get("parentID") \
                        or p.get("text") == "how's the battery?":
                    continue                                  # the user's own row
                if p.get("type") in ("text", "tool") and \
                        p.get("id") not in [i for _, i in order]:
                    order.append((p.get("type"), p.get("id")))
                if p.get("type") == "text":
                    texts[p["id"]] = p.get("text") or ""
            elif ev.get("type") == "message.part.delta":
                pid = props.get("partID")
                texts[pid] = texts.get(pid, "") + (props.get("delta") or "")
        kinds = [k for k, _ in order]
        self.assertIn("tool", kinds)
        self.assertEqual(kinds[0], "text", order)           # the lead-in first
        self.assertIn("Let me check the BMS.", texts.values())
        stored = [p.get("text") for p in (out or {}).get("parts") or []
                  if p.get("type") == "text"]
        self.assertIn("Let me check the BMS.", stored)
        self.assertIn("It's at eighty percent.", " ".join(s or "" for s in stored))


if __name__ == "__main__":
    unittest.main()
