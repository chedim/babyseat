"""THE INJECTION AS A CARD (2026-10-08, the operator: "update the viewer to
show the injection like it does for all tool calls"). The proxy's response
carries membrane.injection; the serve turns it into a completed 'membrane'
tool part (the first round's, and any later round's that differs) -- never
into the model's history."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402


class TestInjectionCard(unittest.TestCase):
    def test_the_injection_rides_as_a_tool_card(self):
        replies = [
            {"membrane": {"injection": "[membrane] from the field: the battery sits near 92%", "massage": {"alignment": 0.91, "tokens": 12}},
             "choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                    "function": {"name": "state", "arguments": "{}"}}]}}]},
            {"membrane": {"injection": "[membrane] from the field: the battery sits near 92%"},
             "choices": [{"message": {"content": "done"}}]},
        ]
        seen = []

        def ladder(body):
            seen.append(body["messages"])
            return replies.pop(0)
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="m", tools=[])
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "how is the battery?")
        cards = [p for p in out["parts"] if p.get("type") == "tool" and p.get("tool") == "membrane"]
        self.assertEqual(len(cards), 1)                       # the same injection twice: one card
        self.assertIn("battery sits near 92%", cards[0]["state"]["output"])
        self.assertIn("alignment 0.91", cards[0]["state"]["title"])
        self.assertEqual(cards[0]["state"]["status"], "completed")
        for msgs in seen:                                     # never in the model's own input
            self.assertFalse(any("battery sits near 92%" in str(m.get("content")) for m in msgs))


if __name__ == "__main__":
    unittest.main()
