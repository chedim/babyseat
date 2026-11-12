"""Tool descriptions are written for the model, not a person (the operator, 2026-11-12: save tokens): dense notation --
signature, grammar, effect -- on one line, in the tool list that rides every step. The full human text of every tool
stays in the help topic `tools` (`help('tools')`, membrane::help:tools), one `## <name>` section each."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import help_pack as HP  # noqa: E402
from membrane import mcp_server as M  # noqa: E402

FILE_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}      # the no-bash seat strips these (#1101)
SHORT = {"call", "entity_enter", "entity_exit"}                          # already one line; no second text


def _sections():
    text = HP.packaged("tools")
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^## (\S+)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}


def test_every_tool_has_its_full_text_in_the_tools_topic():
    sec = _sections()
    missing = [t["name"] for t in M.TOOLS if t["name"] not in FILE_TOOLS | SHORT and t["name"] not in sec]
    assert not missing, missing


def test_the_tool_list_carries_the_dense_form_not_the_prose():
    sec = _sections()
    for t in M.TOOLS:
        n = t["name"]
        if n in FILE_TOOLS | SHORT:
            continue
        d = t["description"]
        assert "\n" not in d, n
        assert len(d) <= len(sec[n]), (n, len(d), len(sec[n]))
    live = sum(len(t["description"]) for t in M.TOOLS if t["name"] not in FILE_TOOLS)
    full = sum(len(v) for v in _sections().values())
    assert live < 0.7 * full, (live, full)


def test_the_doctrine_says_where_the_details_are():
    assert "help('tools')" in (Path(__file__).resolve().parent.parent / "membrane" / "doctrine.md").read_text()
