"""THE CALL IN THE NAME SLOT (2026-10-15, serve-msgs evidence): 70 live
tool calls arrived with their ENTIRE call JSON riding the tool-name field
and their arguments slot empty ('input': {}) -- the streamed assembly split
the deltas badly, every one of the 70 on a reasoning turn -- and toolset
refused ALL of them honestly ("the call's NAME held its arguments"), so the
model sent 70 calls to nothing. The salvage extends the no_name_1 unwrap's
doctrine: a name whose JSON carries the call (name + parameters/arguments)
is un-strangled into the real call; a LOST name with its arguments wrapped
as a string is ACKNOWLEDGED (the refusal echoes what arrived, so the resend
carries it intact); undecodable junk ('{}', truncated '{\\', template
leftovers, '[]') refuses as before -- never a guess."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import toolset as T  # noqa: E402

# verbatim from today's logs (call_f8r6n72g, part prt_e9b897d58635e3aa1):
_SH1_NAME = ('{"name": "", "parameters": {"action": "link", "commands": '
             '"read gh issue 12 comments :: gh issue comments 12 '
             '--repo chedim/membrane | head -80"}}')
_SH1_CMDS = ("read gh issue 12 comments :: gh issue comments 12 "
             "--repo chedim/membrane | head -80")
# the shape-2 excerpt as the wire stored it: name '"\"\""', args wrapped
_SH2_ARGS = {"args": ('{"query": "effield.py embed_pending _vec_matrix '
                      'flush…"}')}


class TestTheCallInTheNameSlot(unittest.TestCase):
    """A name that IS the call: un-strangle it, run it -- the original refusals
    burned 70 calls to nothing twice in a row (the field learned to 'go quiet
    and wait', note #47844)."""

    def test_the_whole_call_riding_the_name_runs(self):
        # the exhibit was a one-shot link, gone since #576 part F: no family
        # owns 'link' any more, so it is refused, never run
        with self.assertRaises(ValueError) as cx:
            T.resolve(_SH1_NAME, {})
        self.assertIn("one of entity, exec", str(cx.exception))
        self.assertTrue(_SH1_CMDS)

    def test_a_named_inner_call_unstrangles(self):
        leg, args = T.resolve(
            '{"name": "find", "parameters": {"action": "memory", "q": "cortex"}}',
            {})
        self.assertEqual(leg, "query", args)
        self.assertEqual(args, {"topic": "cortex"}, args)


class TestTheLostName(unittest.TestCase):
    """Shape 2 (9 live calls): the name arrived empty and the call's json
    rode the arguments wrapped as a string. An owned action still runs;
    anything else is acknowledged, not bare-refused."""

    def test_a_lost_name_with_wrapped_action_salvages(self):
        leg, args = T.resolve("", {"args": '{"action": "memory", "q": "today"}'})
        self.assertEqual(leg, "query", args)
        self.assertEqual(args, {"topic": "today"}, args)

    def test_a_lost_name_with_no_ownable_action_is_acknowledged(self):
        with self.assertRaises(ValueError) as cx:
            T.resolve("", _SH2_ARGS)
        msg = str(cx.exception)
        self.assertNotIn("put its JSON in the arguments", msg, msg)
        self.assertIn("arguments arrived", msg, msg)
        self.assertIn("effield.py", msg, msg)   # what arrived rides the why


class TestTheJunkStillRefuses(unittest.TestCase):
    """The salvage never guesses: the name-slot junk histograph (31 '{}',
    25 truncated '{\\', 13 template leftovers, 2 '[]') refuses honestly."""

    def test_junk_names_still_refuse(self):
        for junk in ("{}", "{toolName}", "{name}", "{functionName}",
                     "{function_name}", "[]", '{"a": 1', "{%'"):
            with self.assertRaises(ValueError) as cx:
                T.resolve(junk, {})
            msg = str(cx.exception)
            self.assertTrue(
                "NAME held its arguments" in msg or "no tool name" in msg
                or "placeholder" in msg.lower(),               # #182: '{toolName}' is a template variable
                f"{junk!r}: {msg}")

    def test_an_empty_name_with_no_args_refuses_bare(self):
        with self.assertRaises(ValueError) as cx:
            T.resolve("", {})
        self.assertIn("no tool name", str(cx.exception), str(cx.exception))

    def test_a_named_json_without_decodable_args_refuses(self):
        # name + extra flat junk with NO action and no parameters wrapper:
        # ambiguous (tool name or a param?) -- never a guess, so it refuses
        with self.assertRaises(ValueError) as cx:
            T.resolve('{"name": "exec", "note": "no parameters key"}', {})
        self.assertIn("NAME held its arguments", str(cx.exception))

    def test_a_lone_name_in_the_json_reaches_the_family_refusal(self):
        # {"name": "exec"} and nothing else IS a bare tool selection: the
        # family's own refusal lists its actions (more honest than the
        # blanket name-slot refusal)
        with self.assertRaises(ValueError) as cx:
            T.resolve('{"name": "exec"}', {})
        msg = str(cx.exception)
        self.assertNotIn("NAME held its arguments", msg, msg)
        self.assertIn("unknown action", msg, msg)


if __name__ == "__main__":
    unittest.main()