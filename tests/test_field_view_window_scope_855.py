"""#855 regression: the work window must not be matched by home.js's .card rules; the entity drawer must show on narrow screens while the chat is covered."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def test_work_window_does_not_use_home_card_class():
    src = (UI / "fieldview.js").read_text()
    assert not re.search(r'class="card"', src), "the window's class=card is hit by home.js .card{display:none;left:12px;bottom:76px!important}"
    assert 'class="fvwin"' in src
    assert 'querySelector(".fvwin")' in src


def test_narrow_drawer_not_pushed_off_screen_while_chat_covered():
    css = (UI / "home.js").read_text()
    assert ".chat.covered ~ .card" in css, "on phones .card sits above the 56dvh chat sheet, which is slid away when covered"
