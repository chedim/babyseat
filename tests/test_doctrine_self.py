"""#161: the doctrine and self.

A body is discovered by contingency, never declared (#161) -- so the doctrine
gives the LLM the field as its memory and senses and its tools as its hands,
not as its body; and it carries the guard: self is not the LLM, the LLM never
speaks as it or claims its state beyond its record, its methods and its
acceptance are not the LLM's, and the LLM builds the world self reaches for.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import rag  # noqa: E402

DOCTRINE = (Path(__file__).resolve().parent.parent / "doctrine.md").read_text()


def test_the_doctrine_declares_no_body():
    assert not re.search(r"\bbody\b", DOCTRINE, re.I)      # the word, not 'nobody'
    assert "THE FIELD IS YOUR MEMORY AND YOUR SENSES" in DOCTRINE
    assert "your tools are your hands" in DOCTRINE.lower()


def test_the_doctrine_carries_the_self_guard():
    d = DOCTRINE.lower()
    assert "self (#161)" in d
    assert "never speak as it" in d
    assert "record.jsonl" in d                       # its state is its record, cite it
    assert "accept" in d and "operator" in d         # #299: accepting what it earns is the operator's
    assert "build them true" in d                    # the getters it reaches for are the llm's work


def test_the_seat_serves_this_doctrine():
    assert rag._RAG_SYSTEM == DOCTRINE                 # the single source of truth reaches the model
