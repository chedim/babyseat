"""A TURN THAT REPEATS ITS REFUSED CALL (issue #274): on qwen3:14b a chat
turn sent the same nameless call ('' with {"focus": "self"}) seven times,
one per model round, each refused with 'the call had no tool name' -- the
person's question went unanswered for ten minutes and nothing stopped it
before MAX_ROUNDS. THE CONTRACT:

1. a call refused before in the same turn (same name, arguments and
   refusal) is not refused the same way again: its result says it already
   failed in this turn and asks for a plain-text answer, and the NEXT
   round goes to the model WITHOUT tools -- it can only answer;
2. the no-name refusal names the closest tool for the arguments given,
   `state` when none of them fits any tool.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402
from membrane import toolset as TS  # noqa: E402

NAMELESS = {"id": "c", "type": "function",
            "function": {"name": "", "arguments": '{"focus": "self"}'}}


def _rounds(*msgs):
    """A streamed ladder over the given model rounds; it keeps each
    round's request body (did it carry tools?)."""
    state = {"n": 0, "bodies": []}

    def ladder(body):
        raise AssertionError("the buffered call is not the streamed round's door")

    def stream(body, on_event):
        state["bodies"].append(body)
        msg = msgs[min(state["n"], len(msgs) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}

    ladder.stream = stream
    ladder.state = state
    return ladder


def _run(ladder):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                      ladder, lambda *a: "ok", system="", model="m", tools=None)
    sid = r.sessions.create(title="t")["id"]
    out = r.run(sid, "how is self doing?")
    return out


def _tools(out):
    return [p for p in out["parts"] if p.get("type") == "tool"]


class TestRepeatRefusal(unittest.TestCase):
    def test_the_second_identical_refusal_ends_the_tool_rounds(self):
        # THE EXHIBIT: the model resends the same nameless call every
        # round. Round 2's refusal says it failed already; round 3 has no
        # tools, and the model's text there ends the turn.
        lad = _rounds({"tool_calls": [dict(NAMELESS, id="c1")]},
                      {"tool_calls": [dict(NAMELESS, id="c2")]},
                      {"content": "self is at rung 6."},
                      {"tool_calls": [dict(NAMELESS, id="c9")]})
        out = _run(lad)
        bodies = lad.state["bodies"]
        self.assertEqual(len(bodies), 3, [list(b) for b in bodies])
        self.assertIn("tools", bodies[0])
        self.assertIn("tools", bodies[1])
        self.assertNotIn("tools", bodies[2], "the round after a repeat must be text-only")
        parts = _tools(out)
        self.assertEqual(len(parts), 2, parts)
        second = parts[1]["state"]["output"]
        self.assertIn("already failed", second)
        self.assertIn("plain text", second)
        text = " ".join(p.get("text") or "" for p in out["parts"] if p.get("type") == "text")
        self.assertIn("rung 6", text)

    def test_a_first_refusal_keeps_the_tools(self):
        # one refusal is a lesson, not a loop: the next round keeps its tools
        lad = _rounds({"tool_calls": [dict(NAMELESS, id="c1")]},
                      {"tool_calls": [{"id": "c2", "type": "function",
                                       "function": {"name": "state", "arguments": "{}"}}]},
                      {"content": "done."})
        _run(lad)
        bodies = lad.state["bodies"]
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all("tools" in b for b in bodies))

    def test_different_refusals_are_not_a_repeat(self):
        lad = _rounds({"tool_calls": [dict(NAMELESS, id="c1")]},
                      {"tool_calls": [{"id": "c2", "type": "function",
                                       "function": {"name": "", "arguments": '{"focus": "body"}'}}]},
                      {"content": "done."})
        _run(lad)
        self.assertTrue(all("tools" in b for b in lad.state["bodies"]))


class TestClosestTool(unittest.TestCase):
    def test_nothing_fits_suggests_state(self):
        with self.assertRaises(ValueError) as cm:
            TS.resolve("", {"focus": "self"})
        self.assertIn("had no tool name", str(cm.exception))
        self.assertIn("closest: state", str(cm.exception))

    def test_a_fitting_argument_suggests_its_family(self):
        fam = next(f for f, d in TS.FAMILIES.items()
                   if d["params"] and f != "state")
        key = next(iter(TS.FAMILIES[fam]["params"]))
        owners = [f for f, d in TS.FAMILIES.items() if key in d["params"]]
        if len(owners) != 1:
            self.skipTest(f"{key!r} is shared by {owners}")
        with self.assertRaises(ValueError) as cm:
            TS.resolve("", {key: "x"})
        self.assertIn(f"closest: {fam}", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
