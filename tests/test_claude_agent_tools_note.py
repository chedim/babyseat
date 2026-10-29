"""The Claude rung told the operator it had "no tools to work" (2026-10-23):
its built-in tools are off and nothing said that membrane's tools are its
hands -- method_call runs an object's method (since #576 part F the only
way to run a command: define the method first, then call it). A fixed note now rides the system
prompt (fixed text, so the prompt cache still holds). It also says to ask the
person for a decision as a Decision card on their board, not a list in chat
(Memo kept asking in chat). And it says how to work, as Claude Code does:
carry the task through within the turn, end only when done, when the
person's decision is needed, or when blocked (11 of Memo's 15 turns ended on
a permission question or a "next I'll..." with nothing after it)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from tests import test_claude_agent_384 as T384  # noqa: E402


def _system(system="the doctrine"):
    opts = CA.build_options(T384.fake_sdk(), system, "claude-agent:haiku", stream=False, port=9, env={})
    return opts.system_prompt if hasattr(opts, "system_prompt") else opts.kwargs["system_prompt"]


def test_the_note_names_the_hands():
    for word in ("method_define", "method_call", "worktree", "query",
                 "Decision", "'status': 'open'", "pick"):
        assert word in CA.TOOLS_NOTE, word


def test_the_note_says_how_to_work():
    note = CA.TOOLS_NOTE
    for phrase in ("carry it out to the end in this turn",
                   "Don't ask permission",
                   "\"next I'll...\"",
                   "End your turn only when",
                   "the task is done",
                   "you are blocked",
                   "Say which",
                   "same message as your next tool call",
                   "\"Open decisions\""):
        assert phrase in note, phrase


# ---- programming the field (2026-10-25): Memo "just runs commands instead
# of programming the field" -- the note sold entity_link as a raw shell. It
# now says the doctrine's way: reuse what you have, name or define what is
# worth repeating, keep world things current with getter/tick scripts; raw
# one-off commands are for exploring and for code work in a worktree.
# 2026-10-26 (#576 part F): no one-shot commands at all -- define, then call.
# 2026-10-27 (#614): code runs as a tree kept in the field (code_run), re-runnable.

def test_the_note_teaches_programming_the_field():
    note = CA.TOOLS_NOTE
    for phrase in ("programming the field", "method_define", "method_call",
                   "method_each", "method_inspect", "entity_query",
                   "getter_script", "tick_script", "tick_every", "doctrine",
                   "worktree", "no one-shot", "program the field first", "code_run"):
        assert phrase in note, phrase
    # reuse comes BEFORE defining something new
    assert note.index("entity_query") < note.index("method_define")
    # the raw-shell sales pitch is gone, and so is the one-shot door
    assert "read files (cat, sed -n" not in note
    assert "entity_link" not in note and "one-off" not in note


def test_the_note_keeps_detach_and_the_decision_card():
    note = CA.TOOLS_NOTE
    assert "nohup <command> > <log> 2>&1 &" in note
    assert "longer than about 4 minutes" in note
    assert "entity_add class='Decision'" in note
    # a card no longer ends the turn (2026-10-25, the operator)
    assert "After making a card" not in note
    assert "End your turn only when the task is done or when you are blocked." in note


def test_the_note_stays_tight():
    assert len(CA.TOOLS_NOTE) <= 3200, len(CA.TOOLS_NOTE)


def test_the_name_map_carries_define_and_each():
    pairs = {(f, a): n for f, a, n in _name_map()}
    assert pairs[("exec", "define")] == "method_define"
    assert pairs[("exec", "each")] == "method_each"


def test_the_system_prompt_carries_the_note_after_the_doctrine():
    s = _system("the doctrine")
    assert s.startswith("the doctrine")
    assert CA.TOOLS_NOTE in s


def test_the_note_alone_when_there_is_no_system_prompt():
    assert _system("") == CA.TOOLS_NOTE


def test_the_note_is_fixed_text():
    assert _system("x") == _system("x")


# ---- one call makes a card (2026-10-23: "Memo really struggles with decisions")
# entity_add applies `properties` itself (tool_entity_add follows the add with
# an entity/edit), so the note teaches the ONE-CALL form, with a concrete
# option card -- and that a name can't hold quotes or apostrophes.

CARD = {"class": "Decision", "name": "Which test runner",
        "summary": "pytest or unittest for the new suite",
        "properties": {"status": "open",
                       "var": {"type": "option", "options": ["pytest", "unittest"]},
                       "pick": "pytest"}}


def test_the_note_teaches_the_one_call_card():
    note = CA.TOOLS_NOTE
    assert "in one call" in note
    assert ("entity_add class='Decision' name='Which test runner' "
            "summary='pytest or unittest for the new suite' "
            "properties={'status': 'open', 'var': {'type': 'option', "
            "'options': ['pytest', 'unittest']}, 'pick': 'pytest'}") in note
    assert "no quotes or apostrophes" in note
    assert "then entity_edit" not in note          # the two-call form is gone


def test_the_one_call_card_lands_on_a_real_store():
    from unittest import mock
    from membrane import mcp_server as M
    from membrane.eids import lint_name
    from membrane.entities import EntityStore
    from tests.test_jwt_auth import _jwt_env
    from tests.test_offbox_auth import _proxy
    assert lint_name(CARD["name"]) is None
    assert lint_name("Memo's runner")              # an apostrophe is refused
    import tempfile
    with tempfile.TemporaryDirectory() as d, \
            mock.patch("membrane.lanes._pulse_edit", lambda *a: None), \
            mock.patch("membrane.lanes._entity_post_process", lambda *a: None), \
            mock.patch("membrane.lanes._active_model_hook", lambda *a: None):
        env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(d, "seat-token"),
                       MEMBRANE_DATA_DIR=d)
        env.start()
        backend, srv = _proxy(offbox=False)
        try:
            srv.entity_store = est = EntityStore(path=None, shared=False)
            out = M.tool_entity_add(dict(CARD), srv.server_address[1])
        finally:
            srv.shutdown()
            backend.shutdown()
            env.stop()
    assert "Also set: properties" in out, out
    [doc] = [dc for dc in est.docs.values() if dc.cls == "Decision"]
    assert doc.cls == "Decision"
    p = doc.properties
    assert (p["status"], p["pick"]) == ("open", "pytest")
    assert p["var"] == {"type": "option", "options": ["pytest", "unittest"]}


# ---- the name map (2026-10-23): the doctrine names the seat's tool FAMILIES
# (exec(action='call'), goal(action='plant')...), the rung has only the MCP
# tools; the note maps one onto the other, checked against both registries so
# it can't go stale.

def _name_map():
    import re
    note = CA.TOOLS_NOTE
    start = note.index("The doctrine names the seat's tool families")
    seg = note[start:note.index("\n", start) if "\n" in note[start:] else None]
    out = []
    for fam, acts, names in re.findall(r"(\w+)(?:\(action=([^)]*)\))? -> ([\w/]+)", seg):
        names = names.split("/")
        acts = [a.strip("'") for a in acts.split("/")] if acts else [None] * len(names)
        assert len(acts) == len(names), (fam, acts, names)
        out += [(fam, a, n) for a, n in zip(acts, names)]
    return out


def test_the_note_maps_the_doctrine_families_to_the_tools():
    from membrane import mcp_server as M
    from membrane import toolset as T
    tools = {t["name"] for t in M.TOOLS}
    pairs = _name_map()
    fams = {f for f, _, _ in pairs}
    assert {"exec", "entity", "goal", "memory", "find", "track", "peer"} <= fams, fams
    for fam, act, name in pairs:
        assert name in tools, name
        assert fam in T.FAMILIES, fam
        legs = T.FAMILIES[fam]["actions"]
        assert (legs[act] == name) if act else (name in legs.values()), (fam, act, name)


def test_the_note_names_the_real_tools():
    """2026-10-29: 112 of Memo's 1,621 tool calls (7%) failed as "No such
    tool available" -- mostly a bare `bash` (Claude Code's own Bash is off;
    membrane's is mcp__membrane__bash). The Claude-only note says the real
    names, near its start."""

    from membrane import claude_agent as CA
    head = CA.TOOLS_NOTE[:600]
    assert CA.TOOL_PREFIX + "<name>" in head
    assert "no plain bash or query" in head



def test_claude_drops_the_card_lines_its_note_replaces():
    """On the Claude rung TOOLS_NOTE covers exec, and the router's
    'free-tier only' line is wrong for a Claude turn."""
    from membrane import claude_agent as CA
    system = "doctrine\n- EXEC: you have NO built-in tools ...\n- ROUTING: membrane picks the model per turn (free-tier only)\n- COMMONS: kept"
    out = CA._with_tools_note(system)
    assert "- EXEC:" not in out and "- ROUTING:" not in out
    assert "- COMMONS: kept" in out and CA.TOOLS_NOTE in out
