"""#316: the wheel over the Skill tree tab scrolls the panel. The tree's box is a
horizontal scroller with no vertical overflow; containing its overscroll in both
axes stopped a vertical scroll from chaining up to the panel (in Chrome a 300 px
wheel over it moved the panel 0 px). Only its horizontal axis is contained."""
import re
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "home.js"


def _rule(sel):
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", HOME.read_text())
    assert m, sel
    return m.group(1)


def test_the_tree_box_lets_a_vertical_scroll_reach_the_panel():
    ob = re.search(r"overscroll-behavior\s*:\s*([^;]+);", _rule(".sktree .skbox"))
    assert ob, "the tree's box names its overscroll"
    axes = ob.group(1).split()
    x, y = (axes * 2)[:2]                       # one value means both axes
    assert x == "contain", "a sideways swipe over a wide tree stays in the tree"
    assert y == "auto", "a vertical scroll over the tree chains up to the panel"
