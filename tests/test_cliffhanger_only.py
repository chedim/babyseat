"""Cliffhanger only (operator 2026-11-11, "no bash"): with MEMBRANE_CLIFFHANGER_ONLY=1 the model's shell doors
(bash, code_run, exec run -- all lang.run(oneshot=True)) refuse and point at cliffhanger's fs/proc stdlib
(read/listdir/stat getters, File/Command effects, #1044); the model's tool list loses bash and code_run;
help('stdlib') says how. Off by default."""

import os
import unittest
from unittest import mock

from membrane import lang as LG
from membrane import cliffhanger_only as CO


class Switch(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(CO.on())

    def test_on_by_env(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            self.assertTrue(CO.on())


class Doors(unittest.TestCase):
    def test_a_one_shot_is_refused_with_the_way(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            out = LG.run(object(), code="ls -la", lang="sh", oneshot=True)
        self.assertFalse(out["ok"])
        for word in ("read(", "listdir(", "Command:", "help('stdlib')"):
            self.assertIn(word, out["error"])

    def test_js_too(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            out = LG.run(object(), code="1+1", lang="js", oneshot=True)
        self.assertFalse(out["ok"])

    def test_the_tool_list_drops_the_shell(self):
        tools = [{"name": "bash"}, {"name": "code_run"}, {"name": "entity_read"},
                 {"type": "function", "function": {"name": "Bash"}}]
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            kept = CO.strip_tools(tools)
        self.assertEqual([t.get("name") or t["function"]["name"] for t in kept], ["entity_read"])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(CO.strip_tools(tools), tools)

    def test_the_help_topic_exists(self):
        from pathlib import Path
        p = Path(LG.__file__).parent / "help" / "stdlib.md"
        self.assertTrue(p.exists())
        t = p.read_text()
        for word in ("read(", "listdir(", "stat(", "File:", "Command:", ".run"):
            self.assertIn(word, t)


if __name__ == "__main__":
    unittest.main()


class Notes(unittest.TestCase):
    def test_the_claude_rung_is_told(self):
        from membrane import claude_agent as CA
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            self.assertIn("NO SHELL", CA._with_tools_note("sys"))
            self.assertNotIn("bash", " ".join(CA.tool_names()).lower().replace("membrane__", ""))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("NO SHELL", CA._with_tools_note("sys"))
