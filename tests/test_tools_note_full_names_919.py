"""#919: Claude calls a membrane tool by the name the note gives it, and the SDK knows only
mcp__membrane__<name> -- every bare name in the note was a failed call (~90 a day)."""
import re
import unittest

from membrane import claude_agent as CA
from membrane import mcp_server as MCP

ENGLISH = {"call"}      # a tool named like an ordinary word (#1101): the note says "call the full name", never the tool bare
NAMES = sorted((t["name"] for t in MCP.TOOLS if t["name"] not in ENGLISH), key=len, reverse=True)


def _bare(note: str) -> list[str]:
    """The membrane tool names the note says without the prefix (a quoted action value
    like find(action='query') is an action, not a tool name)."""
    out = []
    for n in NAMES:
        for m in re.finditer(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])", note):
            if not note[:m.start()].endswith(CA.TOOL_PREFIX):
                out.append(n)
    return sorted(set(out))


class ToolsNoteNamesTools(unittest.TestCase):
    def test_no_tool_is_named_bare(self):
        self.assertEqual(_bare(CA.TOOLS_NOTE), [])

    def test_every_full_name_is_a_real_tool(self):
        named = set(re.findall(re.escape(CA.TOOL_PREFIX) + r"(\w+)", CA.TOOLS_NOTE))
        self.assertTrue({"code_run", "bash", "query", "entity_read", "entity_query"} <= named)
        self.assertEqual(sorted(named - {t["name"] for t in MCP.TOOLS}), [])      # (`call` is a tool, though not a bare-name check)

    def test_the_note_names_what_claude_may_call(self):
        """Every name the note gives is one the rung allows (tool_names)."""
        allowed = set(CA.tool_names())
        for n in re.findall(re.escape(CA.TOOL_PREFIX) + r"\w+", CA.TOOLS_NOTE):
            self.assertIn(n, allowed)

    def test_the_checker_sees_a_bare_name(self):
        self.assertEqual(_bare("call code_run or " + CA.TOOL_PREFIX + "bash; find(action='query')"),
                         ["code_run"])


if __name__ == "__main__":
    unittest.main()
