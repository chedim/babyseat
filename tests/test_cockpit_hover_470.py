"""#470: hovering a decided card jumped the page half a screen (operator,
2026-10-23). Since the cockpit scrolls as one page (#496), the hover's
scroll-the-chat-to-its-message scrolled the whole page under the pointer.
Hover now only marks the message; clicking the card is what scrolls to it."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def _method(src, name):
    m = re.search(r"\n\s*(?:async\s+)?" + name + r"\([^)]*\)\s*\{(.*?)\n    \}", src, re.S)
    assert m, name
    return m.group(1)


def test_hover_marks_without_scrolling():
    body = _method((UI / "decisions.js").read_text(), "_hover")
    assert "scrollToMessage" not in body
    assert "markMessage" in body


def test_the_panel_can_mark_a_message_without_scrolling():
    body = _method((UI / "panel.js").read_text(), "markMessage")
    assert "scrollIntoView" not in body
    assert "found" in body


def test_a_click_still_scrolls_to_where_it_was_decided():
    body = _method((UI / "decisions.js").read_text(), "_openPast")
    assert "scrollToMessage" in body
