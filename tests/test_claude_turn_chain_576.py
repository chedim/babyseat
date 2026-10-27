"""#576: Blurp learned nothing live. A Claude turn runs the Agent SDK on its own
thread (claude-agent), started without the turn's context, so every tool call it
made reached the seat with writer "llm" and NO chain -- and the learner drops a
write without a chain. The SDK thread must run in the context it was started in:
the turn's chain (and writer) ride into the tool calls."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import writes as W  # noqa: E402


def test_the_sdk_thread_carries_the_turns_chain(monkeypatch):
    seen = {}

    async def consume(sdk, messages, model, stream, put, port, resume, env):
        seen["chain"] = W.current()
        seen["headers"] = W.headers(default_writer="llm")

    monkeypatch.setattr(CA, "_consume", consume)
    monkeypatch.setattr(CA, "credential_env", lambda mode=None: {})
    with W.turn("ses_1"):
        want = W.current()["id"]
        list(CA.iter_reply([{"role": "user", "content": "hi"}], "claude-agent:sonnet", sdk=object()))
    assert (seen["chain"] or {}).get("id") == want
    assert W.HDR_CHAIN in seen["headers"]
