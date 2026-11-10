"""#1061 (Memo, 2026-11-10): a watch planted through the Claude rung's tools is the calling agent's, as the tool
says ("Your agent is stamped as each watch's owner"); the native serve already stamped it, the Claude rung's tool
handler did not, so the watch fell to the operator. #1063: the reply names both limits (field watches N/7 and the
shared attention N/12)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import mcp_server as M  # noqa: E402


def _call(monkeypatch, agent, args):
    sent = []
    monkeypatch.setitem(M.TOOL_IMPLS, "field_watch", lambda a, port: sent.append(a) or "ok")
    h = CA._tool_handler("field_watch", 1)
    tok = CA.TURN_AGENT.set(agent)
    try:
        asyncio.run(h(args))
    finally:
        CA.TURN_AGENT.reset(tok)
    return sent[0]


def test_a_claude_turns_watch_is_its_agents(monkeypatch):
    assert _call(monkeypatch, "watch:membrane::goal:x", {"action": "plant", "topic": "x"})["owner"] == \
        "watch:membrane::goal:x"


def test_an_explicit_owner_is_kept(monkeypatch):
    assert _call(monkeypatch, "build", {"action": "plant", "topic": "x", "owner": "memo"})["owner"] == "memo"


def test_no_turn_no_stamp(monkeypatch):
    assert "owner" not in _call(monkeypatch, None, {"action": "plant", "topic": "x"})


def test_the_reply_names_both_limits():
    from membrane import lanes as L
    src = Path(L.__file__).read_text()
    assert "field watches" in src and "MAX_WATCHES" in src.split("def _membrane_watch")[1].split("def ")[0]
