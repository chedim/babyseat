"""ISSUE #182: after seat restarts the agent sent every call with the tool
name '{function_name}' -- the chat template's own example spelling
('{"name": <function-name>, "arguments": <args-json-object>}'), copied into
the name slot -- and every one bounced as "the call's NAME held its
arguments" (13/13, then 2/2). A template placeholder in the name slot is a
LOST name, not arguments: the call routes by what its arguments carry (the
real name and args the model filled in beside it, or the action's one
owner); with nothing to route by, the refusal says what happened. And the
served manifest never carries a placeholder name of its own."""
import unittest

from membrane import toolset as T
from membrane import server as S


class TestAPlaceholderNameIsALostName(unittest.TestCase):

    def test_the_real_name_and_args_beside_the_placeholder_route(self):
        got = T.resolve("{function_name}",
                        {"fn_name": "exec",
                         "args_json": '{"action": "link", "commands": "echo hi"}'})
        self.assertEqual(got, ("entity_link", {"commands": "echo hi"}), got)

    def test_the_args_object_beside_the_placeholder_routes(self):
        got = T.resolve("{function_name}",
                        {"function_name": "entity",
                         "args_json": {"action": "read", "spec": "presence"}})
        self.assertEqual(got, ("entity_read", {"spec": "presence"}), got)

    def test_a_flat_call_beside_the_placeholder_routes_by_its_action(self):
        got = T.resolve("{function_name}", {"action": "read", "spec": "presence"})
        self.assertEqual(got, ("entity_read", {"spec": "presence"}), got)

    def test_the_angle_spelling_routes_too(self):
        got = T.resolve("<function-name>",
                        {"name": "entity", "arguments": '{"action": "read", "spec": "presence"}'})
        self.assertEqual(got, ("entity_read", {"spec": "presence"}), got)

    def test_the_named_family_beside_the_placeholder_routes_flat(self):
        got = T.resolve("{toolName}", {"tool_name": "exec", "action": "link",
                                       "commands": "echo hi"})
        self.assertEqual(got, ("entity_link", {"commands": "echo hi"}), got)

    def test_a_bare_placeholder_says_it_was_a_placeholder(self):
        with self.assertRaises(ValueError) as cx:
            T.resolve("{function_name}", {"fn_name": "{fn_name}", "args_json": "{args_json}"})
        msg = str(cx.exception)
        self.assertNotIn("NAME held its arguments", msg, msg)
        self.assertIn("placeholder", msg, msg)
        for fam in ("entity", "exec", "find"):
            self.assertIn(fam, msg, msg)


class TestTheManifestNeverServesAPlaceholder(unittest.TestCase):

    def test_a_placeholder_named_row_is_not_served(self):
        rows = S._openai_tools([
            {"name": "{function_name}", "description": "x",
             "inputSchema": {"type": "object",
                             "properties": {"fn_name": {}, "args_json": {}}}},
            {"name": "entity", "description": "y", "inputSchema": {"type": "object"}}])
        names = [r["function"]["name"] for r in rows]
        self.assertEqual(names, ["entity"], names)

    def test_the_served_families_are_all_real_names(self):
        for lane in ("build", "plan", "phone", "watch:x"):
            for r in S._openai_tools(S._openai_tools_for(lane)):
                self.assertTrue(T.is_tool_name(r["function"]["name"]), r)


if __name__ == "__main__":
    unittest.main()
