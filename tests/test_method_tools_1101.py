"""The model's tool list holds field manipulation and querying only (the operator, 2026-11-12). The tools that are not
that -- goals, decision cards, memory, peers, watches, dials -- are METHODS: `call` reaches them by name
(`call method='fulfill' args={...}`), under the same lane rules as when they were listed. Their Python behind the
door is a labelled bridge (docs/python-to-c1f.md, phase A); phase B moves each into c1f as the system behind it moves."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402

NAMES = ("intend", "fulfill", "intents", "answer_card", "learn", "forget", "peer_post", "junk_cut", "attention",
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
    monkeypatch.setitem(M.TOOL_IMPLS, "intents", lambda a, p: (seen.append(("intents", a)), "the board")[1])
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    assert M.tool_dunder_call({"method": "intents", "args": {}}, 1) == "the board"
    assert seen == [("intents", {})]


def test_call_passes_the_args_through(monkeypatch):
    seen = []
    monkeypatch.setitem(M.TOOL_IMPLS, "fulfill", lambda a, p: (seen.append(a), "closed")[1])
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    M.tool_dunder_call({"method": "fulfill", "args": {"spec": "g1", "evidence": "rc=0"}}, 1)
    assert seen == [{"spec": "g1", "evidence": "rc=0"}]


def test_the_lane_rules_apply_through_call(monkeypatch):
    called = []
    monkeypatch.setitem(M.TOOL_IMPLS, "answer_card", lambda a, p: called.append(a) or "passed")
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: "listener lane: a loop doesn't answer a Decision card")
    out = M.tool_dunder_call({"method": "answer_card", "args": {"card": "c", "value": 1, "quote": "yes"}}, 1)
    assert "listener lane" in out and called == []


def test_the_refusal_comes_from_the_seats_lane_rules(monkeypatch):
    from membrane import diag_requests as DR
    from membrane import server as S
    monkeypatch.setattr(DR, "current", lambda: {"lane": "loop", "agent": "watch:membrane::x"})
    assert "answer a Decision card" in (M._method_tool_refusal("answer_card") or "")
    monkeypatch.setattr(DR, "current", lambda: {"lane": "chat", "agent": ""})
    assert M._method_tool_refusal("answer_card") is None
    monkeypatch.setattr(DR, "current", lambda: {"lane": "loop", "agent": "worker:membrane::task:a"})
    assert "isn't closed from here" in (M._method_tool_refusal("fulfill") or "") or S._graph_refusal("fulfill", "worker:membrane::task:a")


def test_an_unknown_name_still_goes_the_old_way(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: {"ok": False, "error": "no method"})
    assert "call refused" in M.tool_dunder_call({"method": "frobnicate", "args": {}}, 1)


def test_a_method_the_model_defined_wins_over_the_bridge(monkeypatch):
    """Resolution order: the model's own method (in its context stack) first, the legacy Python only as the fallback --
    so every one of these can be redefined by the model."""
    legacy = []
    monkeypatch.setitem(M.TOOL_IMPLS, "intents", lambda a, p: legacy.append(a) or "legacy board")
    monkeypatch.setattr(M, "_method_tool_refusal", lambda name: None)
    monkeypatch.setattr(M, "_http", lambda *a, **k: {"ok": True, "status": "ok", "rc": 0, "object": "membrane::Component:mine",
                                                       "resolved": {"object": "membrane::Component:mine", "level": "chat"},
                                                       "outcome": "ok", "head": "my board"})
    out = M.tool_dunder_call({"method": "intents", "args": {}}, 1)
    assert "my board" in out and legacy == []
