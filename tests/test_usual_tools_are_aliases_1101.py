"""#1101 says the usual tools (Read, Write, Edit, Glob, LS) are ALIASES on a no-bash seat: an unlisted name resolves to
the field's file doors. No tool of theirs is listed (the operator, 2026-11-12: "who told you to add those tools"):
the tool list carries no read_file / write_file / edit_file / list_dir, and the names still resolve."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane import cliffhanger_only as CO  # noqa: E402

NAMES = {"read_file", "write_file", "edit_file", "list_dir"}


def test_the_usual_file_tools_are_not_listed():
    assert not NAMES & {t["name"] for t in M.TOOLS}
    assert not NAMES & set(M.TOOL_IMPLS)
    assert not hasattr(CO, "FILE_TOOLS")


def test_the_usual_names_still_resolve_on_a_no_bash_seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_CLIFFHANGER_ONLY", "1")
    assert TS.canonical("Read", {"path": "/x"})[0] == "fs_read"
    assert TS.canonical("LS", {"path": "/x"})[0] == "fs_list"
    assert TS.canonical("list_dir", {"path": "/x"})[0] == "fs_list"
    assert TS.canonical("Edit", {"path": "/x", "old_string": "a", "new_string": "b"})[0] == "fs_edit"
    assert TS.canonical("Write", {"path": "/x", "content": "c"})[0] == "fs_write"


def test_the_shell_names_stay_refused_on_a_no_bash_seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_CLIFFHANGER_ONLY", "1")
    assert "bash" not in {t["name"] for t in CO.strip_tools(M.TOOLS)}
