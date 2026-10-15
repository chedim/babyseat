"""Issue #110: a tool call with a name the seat does not mount (f_1..f_4)
is refused by the surface, but the live capture still minted a `tool:f_N`
field entity -- turn noise with no world referent (the same class of
noise-minting that #101 fixed for dead files). The live capture now
drops unmounted tool names before they become observations, so a refused
call leaves no live entity."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import toolset  # noqa: E402
from membrane.lanes import _capture_plan_and_calls  # noqa: E402


def test_mounted_name_accepts_families_and_legacy_members():
    assert toolset.mounted_name("entity") is True
    assert toolset.mounted_name("entity_read") is True      # a legacy member
    assert toolset.mounted_name("entity/action-read") is True  # family/action
    assert toolset.mounted_name("membrane_exec") is True      # namespace prefix drops
    assert toolset.mounted_name("entity-link") is True         # dashed legacy member spelling
    assert toolset.mounted_name("help") is True


def test_mounted_name_rejects_unmounted_names():
    assert toolset.mounted_name("f_1") is False
    assert toolset.mounted_name("f_4") is False
    assert toolset.mounted_name("") is False
    assert toolset.mounted_name("bogus_tool_xyz") is False


def _capture(mem, calls):
    with mock.patch("membrane.lanes._codegraph_note_dirty") as dirty, \
         mock.patch("membrane.lanes._tool_arg_path", side_effect=lambda fn: str(fn)):
        _capture_plan_and_calls(mem, "q", "", calls)
        return dirty


def _calls(*names):
    out = {}
    for i, n in enumerate(names):
        out[i] = {"id": f"cid_{i}", "function": {"name": n, "arguments": "{}"}}
    return out


def test_capture_drops_unmounted_tool_names():
    cap = mock.MagicMock()
    mem = mock.MagicMock()
    mem.server_ref = mock.MagicMock()
    mem.server_ref.capturer = cap
    mem._pending_tools = {}
    calls = _calls("entity", "f_1", "exec", "f_4")
    _capture(mem, calls)
    # only mounted names reach the observation capture
    sent = cap.on_calls.call_args.args[1]
    assert [c["function"]["name"] for c in sent.values()] == ["entity", "exec"]
    # and only mounted names wait for a result
    assert set(mem._pending_tools) == {"cid_0", "cid_2"}


def test_capture_keeps_mounted_names_when_mixed():
    cap = mock.MagicMock()
    mem = mock.MagicMock()
    mem.server_ref = mock.MagicMock()
    mem.server_ref.capturer = cap
    mem._pending_tools = {}
    calls = _calls("entity_read", "not_a_tool")
    _capture(mem, calls)
    sent = cap.on_calls.call_args.args[1]
    assert [c["function"]["name"] for c in sent.values()] == ["entity_read"]
    assert set(mem._pending_tools) == {"cid_0"}


def test_capture_keeps_opencode_builtin_tools():
    # the client's own toolbelt (edit/write/bash/read/...) is a mounted
    # surface too -- only names the seat never mounted (f_1..f_4) drop
    cap = mock.MagicMock()
    mem = mock.MagicMock()
    mem.server_ref = mock.MagicMock()
    mem.server_ref.capturer = cap
    mem._pending_tools = {}
    calls = _calls("edit", "bash", "f_2")
    _capture(mem, calls)
    sent = cap.on_calls.call_args.args[1]
    assert [c["function"]["name"] for c in sent.values()] == ["edit", "bash"]
    assert set(mem._pending_tools) == {"cid_0", "cid_1"}
