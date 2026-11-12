"""The model's tool list holds field manipulation and querying only (the operator, 2026-11-12). The tools that are not
that -- goals, decision cards, memory, peers, watches, dials -- are METHODS: `call` reaches them by name
(`call method='fulfill' args={...}`), under the same lane rules as when they were listed. Their Python behind the
door is a labelled bridge (docs/python-to-c1f.md, phase A); phase B moves each into c1f as the system behind it moves."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402

NAMES = ("junk_cut",
         "entity_watch", "entity_unwatch", "field_watch", "entity_tracks")


def test_they_are_not_in_the_tool_list():
    assert not set(NAMES) & {t["name"] for t in M.TOOLS}
    assert set(NAMES) == set(M.METHOD_TOOLS)


def test_the_field_tools_stay():
    have = {t["name"] for t in M.TOOLS}
    assert {"entity_read", "entity_query", "entity_add", "entity_edit", "entity_remove", "entity_write",
            "entity_inherit", "entity_enter", "entity_exit", "query", "graph", "state", "pack", "call"} <= have


def test_the_method_tools_are_gone_everywhere():
    """#1101: methods became Command entities (packs/methods.c1f); the four tools, the exec actions and the
    /membrane/method routes are removed."""
    from membrane import toolset as T
    gone = {"method_define", "method_call", "method_each", "method_inspect"}
    assert not gone & {t["name"] for t in M.TOOLS}
    assert not gone & set(M.TOOL_IMPLS) and not gone & set(M.LOCAL_TOOL_IMPLS)
    assert not {"call", "each", "define", "inspect"} & set(T.FAMILIES["exec"]["actions"])


def test_call_reaches_a_method_tool_by_name(monkeypatch):
    seen = []
    monkeypatch.setitem(M.TOOL_IMPLS, "entity_tracks", lambda a, p: (seen.append(("entity_tracks", a)), "the board")[1])
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    assert M.tool_dunder_call({"method": "entity_tracks", "args": {}}, 1) == "the board"
    assert seen == [("entity_tracks", {})]


def test_call_passes_the_args_through(monkeypatch):
    seen = []
    monkeypatch.setitem(M.TOOL_IMPLS, "junk_cut", lambda a, p: (seen.append(a), "closed")[1])
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    M.tool_dunder_call({"method": "junk_cut", "args": {"spec": "g1", "evidence": "rc=0"}}, 1)
    assert seen == [{"spec": "g1", "evidence": "rc=0"}]


def test_the_lane_rules_apply_through_call(monkeypatch):
    called = []
    monkeypatch.setitem(M.TOOL_IMPLS, "field_watch", lambda a, p: called.append(a) or "planted")
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: "listener lane: a watch loop does not plant new goals")
    out = M.tool_dunder_call({"method": "field_watch", "args": {"text": "x"}}, 1)
    assert "listener lane" in out and called == []


def test_an_unknown_name_is_no_method_any_more(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no route is reached")))
    out = M.tool_dunder_call({"method": "frobnicate", "args": {}}, 1)
    assert "retired" in out and "Command:<name>.run" in out
