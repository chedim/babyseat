"""ISSUE #74: "the call's NAME held its arguments" bounced every tool call for a
window. On the seat's own history 80 of those calls carried the tool name '{}' --
an EMPTY JSON object, not arguments -- with the whole real call intact beside it:
  {"name": "entity", "arguments": "{\\"action\\": \\"read\\", ...}"}   (the wrapper)
  {"action": "call", "object": "...", "method": "..."}                (the call itself)
An empty-JSON name is NO name: the wrapper's family routes it, else the one family
that owns the action; with nothing to route by it still says so."""

import unittest

from membrane import toolset as T


class TestAnEmptyJsonNameIsNoName(unittest.TestCase):
    def test_the_wrapper_names_its_family(self):
        got = T.resolve("{}", {"name": "entity", "arguments": '{"action": "read", "name": "m:synth:x"}'})
        self.assertEqual(got, T.resolve("entity", {"action": "read", "name": "m:synth:x"}))

    def test_the_call_itself_is_routed_by_its_action(self):
        args = {"action": "call", "object": "presence", "method": "status"}
        self.assertEqual(T.resolve("{}", dict(args)), T.resolve("exec", dict(args)))

    def test_every_empty_spelling(self):
        args = {"action": "call", "object": "presence", "method": "status"}
        for name in ("[]", "null", "{ }", "  {}  "):
            self.assertEqual(T.resolve(name, dict(args)), T.resolve("exec", dict(args)), name)

    def test_nothing_to_route_by_still_says_so(self):
        with self.assertRaises(ValueError) as e:
            T.resolve("{}", {})
        self.assertIn("name the tool", str(e.exception))

    def test_arguments_in_the_name_are_still_salvaged(self):
        self.assertEqual(T.resolve('{"name": "entity", "arguments": {"action": "read", "name": "x"}}', {})[0],
                         T.resolve("entity", {"action": "read", "name": "x"})[0])


if __name__ == "__main__":
    unittest.main()
