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
            "entity_inherit", "entity_enter", "entity_exit", "query", "graph", "state", "pack", "call",
            "method_define", "method_call", "method_each", "method_inspect"} <= have


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


def test_an_unknown_name_still_goes_the_old_way(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: {"ok": False, "error": "no method"})
    assert "call refused" in M.tool_dunder_call({"method": "frobnicate", "args": {}}, 1)


def test_a_method_the_model_defined_wins_over_the_bridge(monkeypatch):
    """Resolution order: the model's own method (in its context stack) first, the legacy Python only as the fallback --
    so every one of these can be redefined by the model."""
    legacy = []
    monkeypatch.setitem(M.TOOL_IMPLS, "entity_tracks", lambda a, p: legacy.append(a) or "legacy board")
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    monkeypatch.setattr(M, "_http", lambda *a, **k: {"ok": True, "status": "ok", "rc": 0, "object": "membrane::Component:mine",
                                                       "resolved": {"object": "membrane::Component:mine", "level": "chat"},
                                                       "outcome": "ok", "head": "my board"})
    out = M.tool_dunder_call({"method": "entity_tracks", "args": {}}, 1)
    assert "my board" in out and legacy == []
