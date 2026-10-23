"""#470 round 3: the whole cockpit scrolls as one document. The background
field stays put (it is the page's own fixed host, unclickable); the goals, the
open pile, the bar's line, the chat and the past band all scroll together in
the cockpit element. Nothing inside scrolls on its own: the chat grows with
its messages (no inner scrollbar, no jump button) and the open pile grows
upward. On load the input bar is scrolled into view once; the graph's lines
live in the scrolling layer and are recomputed on a resize or a content
change, not per scroll frame."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
DEC = (UI / "decisions.js").read_text()
PANEL = (UI / "panel.js").read_text()


def rule(src, sel):
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", src)
    assert m, sel
    return m.group(1)


def _js(expr):
    js = 'const D=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "decisions.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def test_the_cockpit_element_is_the_scroll_container():
    host = rule(DEC, ":host")
    assert "overflow-y:auto" in host and "overflow:hidden" not in host
    ck = rule(DEC, ".ck")
    assert not re.search(r"(?<!min-)height:100%", ck) and "min-height:100%" in ck
    # the chat spans the bar's row and the past's: the flexible last row takes its height,
    # so the bar's row stays the in-flight cards' height and the past starts right under it
    assert "grid-template-rows:auto auto 1fr" in ck


def test_no_band_scrolls_on_its_own():
    assert "overflow-y:auto" not in rule(DEC, ".band.future")
    assert "overflow" not in rule(DEC, ".band.future")


def test_the_chat_grows_with_its_messages():
    assert "height:auto" in rule(PANEL, ":host([cockpit])")
    assert "height:auto" in rule(PANEL, ":host([cockpit]) .app")
    sc = rule(PANEL, ":host([cockpit]) .scroller")
    assert "overflow:visible" in sc and "flex:none" in sc
    assert "display:none" in rule(PANEL, ":host([cockpit]) .jump")
    # the panel's own scroll-to-latest is a no-op there: the newest message is right under the bar
    assert 'if (this.hasAttribute("cockpit")) return;' in PANEL


@NODE
def test_the_bar_is_scrolled_into_view_once_with_the_future_above_it():
    # the bar's line a little above the middle of the view: the open pile above, the chat below
    assert _js("D.barScrollTop(1200, 800)") == 1200 - 0.4 * 800
    assert _js("D.barScrollTop(100, 800)") == 0                      # never above the top
    assert "this._barShown" in DEC


def test_the_lines_move_with_the_cards_not_per_scroll_frame():
    assert 'addEventListener("scroll", this._redraw' not in DEC
    assert "new ResizeObserver(" in DEC                               # a content change redraws them
