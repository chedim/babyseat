"""#470 round 3: the cockpit's long lists are virtualized, Android-style. The
past band holds EVERY decided card, but only the rows near the viewport are in
the DOM (spacers keep the page height); the cockpit's chat renders only the
messages near the viewport and fetches older ones from the seat in pages
(GET /session/<id>/message?limit=&before= on the serve port). A graph line to
a card that isn't rendered is a short stub toward where it is."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr, f="decisions.js", name="D"):
    js = 'const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (name, str(UI / f), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


# ---- the windowing helper ------------------------------------------------------------

@NODE
def test_only_the_items_near_the_view_render():
    # 100 items of 50 px (gap included); the view shows 1000..1400; 100 px overscan each way
    w = _js("D.windowRange({count: 100, heights: {}, estimate: 50, viewTop: 1000, viewBottom: 1400, overscan: 100})")
    assert (w["start"], w["end"]) == (18, 30)                  # items 18..29 cover 900..1500
    assert w["before"] == 18 * 50 and w["after"] == (100 - 30) * 50
    assert w["total"] == 100 * 50


@NODE
def test_measured_heights_replace_the_estimate():
    w = _js("D.windowRange({count: 10, heights: {0: 200, 1: 200}, estimate: 50, viewTop: 0, viewBottom: 100, overscan: 0})")
    assert (w["start"], w["end"], w["before"]) == (0, 1, 0)
    assert w["after"] == 200 + 8 * 50 and w["total"] == 400 + 8 * 50
    w = _js("D.windowRange({count: 10, heights: {0: 200, 1: 200}, estimate: 50, viewTop: 420, viewBottom: 480, overscan: 0})")
    assert (w["start"], w["end"], w["before"]) == (2, 4, 400)


@NODE
def test_a_view_outside_the_list_renders_nothing_but_keeps_the_height():
    w = _js("D.windowRange({count: 5, heights: {}, estimate: 40, viewTop: -900, viewBottom: -500, overscan: 50})")
    assert (w["start"], w["end"], w["before"], w["after"]) == (0, 0, 0, 200)
    w = _js("D.windowRange({count: 5, heights: {}, estimate: 40, viewTop: 900, viewBottom: 1300, overscan: 50})")
    assert (w["start"], w["end"], w["before"], w["after"]) == (5, 5, 200, 0)
    assert _js("D.windowRange({count: 0, heights: {}, estimate: 40, viewTop: 0, viewBottom: 10, overscan: 0})") == \
        {"start": 0, "end": 0, "before": 0, "after": 0, "total": 0}


@NODE
def test_the_offset_of_an_unrendered_item():
    assert _js("D.itemOffset(3, {1: 100}, 50)") == 50 + 100 + 50


@NODE
def test_the_past_holds_every_decided_card():
    cards = [{"eid": "p%02d" % i, "title": "p%02d" % i, "status": "decided",
              "set_at": "2026-10-%02dT00:00:00Z" % (i + 1)} for i in range(30)]
    out = _js("D.graphLayout(%s)" % json.dumps({"cards": cards, "edges": []}))
    assert len(out["past"]["left"]) + len(out["past"]["right"]) == 30 and out["gone"] == 0


@NODE
def test_a_line_to_an_unrendered_card_is_a_stub_toward_it():
    s = _js("D.stubLine({x: 100, y: 100}, {x: 100, y: 1100}, 40)")
    assert s == {"x1": 100, "y1": 100, "x2": 100, "y2": 140}


# ---- the chat's pages ------------------------------------------------------------------

@NODE
def test_the_chat_window_counts_from_the_newest():
    # the cockpit's chat is reversed: visual index 0 is the newest row
    w = _js("P.chatWindow(%s)" % json.dumps({"ids": ["m%d" % i for i in range(50)], "heights": {}, "estimate": 100,
                                               "viewTop": 0, "viewBottom": 500, "overscan": 200}), "panel.js", "P")
    assert w["ids"] == ["m%d" % i for i in range(43, 50)]       # the 7 newest (0..700 px from the bar)
    assert w["older"] == 43 * 100 and w["newer"] == 0
    assert w["nearEnd"] is False
    w = _js("P.chatWindow(%s)" % json.dumps({"ids": ["m%d" % i for i in range(10)], "heights": {}, "estimate": 100,
                                               "viewTop": 700, "viewBottom": 1100, "overscan": 200}), "panel.js", "P")
    assert w["nearEnd"] is True                                  # near the oldest loaded: fetch the next page


def test_the_cockpit_chat_reads_older_messages_in_pages():
    src = (UI / "panel.js").read_text()
    assert "?limit=" in src and "&before=" in src
    assert "onViewport(" in src
    dec = (UI / "decisions.js").read_text()
    assert "onViewport" in dec and '"scroll"' in dec


def test_the_past_band_is_windowed_with_spacers():
    dec = (UI / "decisions.js").read_text()
    assert 'class="spacer"' in dec
    assert "windowRange(" in dec
