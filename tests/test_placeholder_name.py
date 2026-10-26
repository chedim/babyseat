"""ISSUE #182 (Memo): after deploys every agent call bounced -- the tool's name
was the literal '{function_name}', params {fn_name} / {args_json}. The model was
copying the placeholders of its own chat template's call format (the backend
renders the tools; membrane never writes it). A {placeholder} name is a template
variable, not arguments: route the call when its fields name a tool, else say
plainly it is a placeholder to replace -- with a filled-in example."""

import unittest

from membrane import toolset as T


class TestATemplatePlaceholderNameIsNoName(unittest.TestCase):
    def test_the_fields_that_name_a_tool_route_it(self):
        got = T.resolve("{function_name}", {"fn_name": "entity",
                                            "args_json": '{"action": "read", "name": "presence"}'})
        self.assertEqual(got, T.resolve("entity", {"action": "read", "name": "presence"}))

    def test_a_named_exec_with_a_command_line_never_runs_it(self):
        # #576 part F: no one-shot commands -- free text after 'exec' is refused
        with self.assertRaises(ValueError):
            T.resolve("{function_name}", {"name": "exec ls -a .membrane/self/", "params": ""})

    def test_nothing_to_route_by_says_it_is_a_placeholder_with_an_example(self):
        with self.assertRaises(ValueError) as e:
            T.resolve("{function_name}", {"params": "ls -a .membrane/self/"})
        msg = str(e.exception)
        self.assertIn("placeholder", msg.lower())
        self.assertIn("{function_name}", msg)
        self.assertIn('"action": "call", "object": "presence"', msg)   # a real, filled-in call

    def test_other_placeholder_spellings(self):
        for name in ("{fn_name}", "{ function-name }", "{tool_name}"):
            got = T.resolve(name, {"fn_name": "state", "args_json": "{}"})
            self.assertEqual(got, T.resolve("state", {}), name)

    def test_json_in_the_name_is_still_salvaged(self):
        self.assertEqual(T.resolve('{"name": "state", "arguments": {}}', {}), T.resolve("state", {}))
