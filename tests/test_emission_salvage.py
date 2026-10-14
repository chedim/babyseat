"""THE EMISSION SALVAGE (the five bare one-liners): the seat's per-turn
model sometimes writes its tool call as PROSE -- the turn carries a bullet
line

    🧰 **Tool call** (exec): `{"action": "link", "commands": "false"}`

and no tool_call at all. Nothing ran, no receipt rode back; the turn
ended on the text and the five intended calls 'all failed' without ever
leaving. The salvage: the turn lane reads the bullet shape back out of
the model's own prose and routes each one through the round loop's
ordinary gate -- resolve, run, receipts in-turn (the same path a real
tool_call takes: the refusal machinery included). Zero real tool_calls
and no bullets = nothing salvaged; real tool_calls with bullets beside
them = the model fired already, the prose is display, nothing salvaged.
"""

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import streams as ST  # noqa: E402
from membrane import server as MS  # noqa: E402


FIVE = """**Back, and the verification batch is the same one I promised** -- five bare one-liners, all independent:

🧰 **Tool call** (exec): `{"action": "link", "commands": "false"}`

🧰 **Tool call** (exec): `{"action": "link", "commands": "echo membrane-polarity-ok"}`

🧰 **Tool call** (exec): `{"action": "link", "commands": "git -C ~/projects/membrane log --oneline -8"}`

🧰 **Tool call** (exec): `{"action": "link", "commands": "git -C ~/projects/membrane status -sb"}`

🧰 **Tool call** (exec): `{"action": "link", "commands": "pgrep -af membraned"}`

What the receipts decide: `false` -> must print FAILED -- rc=1 ...
"""


class TestTheBulletReader(unittest.TestCase):
    def test_five_bare_one_liners_are_five_calls(self):
        got = ST.emission_calls(FIVE)
        self.assertEqual([n for n, _ in got], ["exec"] * 5)
        self.assertEqual(json.loads(got[0][1]), {"action": "link", "commands": "false"})
        self.assertEqual(
            json.loads(got[3][1]),
            {"action": "link", "commands": "git -C ~/projects/membrane status -sb"})
        # the args stay the model's RAW text (the round loop parses and,
        # on a bad object, refuses honestly quoting it)
        self.assertEqual(got[0][1], '{"action": "link", "commands": "false"}')

    def test_a_call_without_the_brush_emoji_still_reads(self):
        got = ST.emission_calls('**Tool call** (exec): `{"action": "read"}`')
        self.assertEqual(got, [("exec", '{"action": "read"}')])

    def test_a_nested_json_reads_whole(self):
        # braces inside the command's strings must not close the object early
        line = ("🧰 **Tool call** (exec): `{\"action\": \"link\", "
                "\"commands\": \"jq -r '{.a[0]}' x.json\", \"q\": \"{not call}\"}`")
        got = ST.emission_calls(line)
        self.assertEqual(len(got), 1)
        self.assertEqual(json.loads(got[0][1])["commands"],
                         "jq -r '{.a[0]}' x.json")

    def test_prose_json_without_the_bullet_is_left_alone(self):
        # a code fence discussing an example is not a call
        self.assertEqual(ST.emission_calls('the shape is `{\"action\": \"read\"}` '
                                           'and that is all'), [])
        self.assertEqual(ST.emission_calls("no calls in this text"), [])

    def test_an_unterminated_object_is_refused_not_guess(self):
        # balanced scan finds no close: the line refuses as a raw arg --
        # the round loop's _args_bad path quotes it and never runs
        got = ST.emission_calls('🧰 **Tool call** (exec): `{"action": "read", "s')
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "exec")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(got[0][1])


def _rounds(*msgs):
    state = {"n": 0}
    msgs_ = msgs
    def ladder(body):
        raise AssertionError("the buffered call is not the streamed round's door")
    def stream(body, on_event):
        msg = msgs_[min(state["n"], len(msgs_) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}
    ladder.stream = stream
    return ladder


def _runner(ladder):
    return MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                         ladder, lambda *a: "ok", system="", model="m",
                         tools=None)   # the family gate's own surface


class TestTheTurnTakesThem(unittest.TestCase):
    """End the turn as text WITH the bullets -> the round loop turns them
    into real tool parts (the receipts ride back in the same turn)."""

    def _run(self, *msgs):
        r = _runner(_rounds(*msgs))
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")
        return [p for p in out["parts"] if p.get("type") == "tool"]

    def test_five_one_liners_become_five_parts_and_a_second_round(self):
        parts = self._run({"content": FIVE}, {"content": "done."})
        run = [p for p in parts if p["tool"] == "exec"]
        self.assertEqual(len(run), 5, [(p.get("tool"), p.get("state", {}).get("output")) for p in parts])
        self.assertTrue(all(p["state"]["status"] == "completed" for p in run))
        self.assertEqual(run[0]["state"]["input"],
                         {"action": "link", "commands": "false"},
                         run[0]["state"])

    def test_prose_alone_runs_nothing(self):
        parts = self._run({"content": "just prose, no calls today"})
        self.assertEqual([p["tool"] for p in parts], [], parts)

    def test_real_calls_with_bullets_beside_them_stay_real(self):
        # the model fired AND wrote the bullets: the fired call runs once,
        # the prose is display -- no double execution
        tc = {"id": "c1", "type": "function",
              "function": {"name": "entity",
                           "arguments": '{"action": "read", "name": "presence"}'}}
        parts = self._run({"tool_calls": [tc],
                           "content": '🧰 **Tool call** (exec): `{"action": "link", "commands": "false"}`'},
                          {"content": "done."})
        self.assertEqual([p["tool"] for p in parts], ["entity"], parts)
        self.assertEqual(parts[0]["state"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()