"""#766: work given in the chat goes through the goal graph.

The doctrine, the goals help and the Claude rung's tools note tell the agent
to plant work as a Goal (issue link + what done means) and answer with its
id, while quick questions, reviews and lookups stay in its own turn.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane.toolset import HELP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCTRINE = (ROOT / "doctrine.md").read_text()


def _carries_the_rule(text):
    t = " ".join(text.lower().split())
    assert "work" in t and "plant" in t, text
    assert "instead of doing it in your own turn" in t
    assert "what done means" in t
    assert "goal's id" in t
    assert "planner" in t and "workers" in t
    assert "stay in your turn" in t


def test_the_doctrine_carries_the_rule():
    _carries_the_rule(DOCTRINE)


def test_the_goals_help_carries_the_rule():
    _carries_the_rule(HELP["goals"])


def test_the_tools_note_points_work_at_a_goal():
    # the note is capped (test_the_note_stays_tight): a pointer, the doctrine has the rest
    t = " ".join(CA.TOOLS_NOTE.lower().split())
    assert "work you are given goes to intend" in t
    assert "instead of doing it in your own turn" in t
    assert "goal's id" in t


def test_the_background_mind_doc_names_chat_planted_goals():
    doc = (ROOT / "docs" / "background-mind.md").read_text().lower()
    assert "the planner" in doc and "work given in a chat" in doc
