"""The Claude rung told the operator it had "no tools to work" (2026-10-23):
its built-in tools are off and nothing said that membrane's tools are its
hands -- entity_link runs shell commands (read, edit, test, git, gh) and
method_call runs an object's method. A fixed note now rides the system
prompt (fixed text, so the prompt cache still holds). It also says to ask the
person for a decision as a Decision card on their board, not a list in chat
(Memo kept asking in chat)."""
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
    for word in ("entity_link", "shell", "method_call", "worktree", "query",
                 "Decision", "'status': 'open'", "pick"):
        assert word in CA.TOOLS_NOTE, word


def test_the_system_prompt_carries_the_note_after_the_doctrine():
    s = _system("the doctrine")
    assert s.startswith("the doctrine")
    assert CA.TOOLS_NOTE in s


def test_the_note_alone_when_there_is_no_system_prompt():
    assert _system("") == CA.TOOLS_NOTE


def test_the_note_is_fixed_text():
    assert _system("x") == _system("x")
