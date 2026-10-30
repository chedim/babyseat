"""#736: cards were wider than their lane (a <select> is as wide as its longest
option, and the stack's grid column grew to it), so a left card slid under the
center column -- Submit sat under the logo -- and the right pile ran off the
screen. Decided (operator, 2026-10-30): a card always fits its lane, long text
is cut off with an ellipsis, the collapsed card shows no answer options, and
clicking it opens the card in place as a floating panel over everything.

The layout is measured for real: headless Chrome at the operator's screen (a
16-inch MacBook Pro 2023, 1728x1117 CSS px) renders the cockpit and reports
the boxes."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
W, H = 1728, 1117


def _chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if os.path.exists(mac) else None


CHROME = _chrome()
needs_chrome = pytest.mark.skipif(not CHROME, reason="no Chrome for the layout test")

LONG = "a very long answer option that keeps going " * 6
BOARD = {"edges": [], "cards": (
    [{"eid": "d:%d" % i, "title": "Card %d: %s" % (i, "an extremely long question title " * 5),
      "description": "why it matters " * 30, "status": "open",
      "var": {"type": "option", "options": ["short", LONG + str(i), "x" * 300]}, "pick": "short"}
     for i in range(6)] +
    [{"eid": "w:%d" % i, "title": "In flight %d %s" % (i, "y" * 200), "status": "in_flight"} for i in range(2)])}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%%;background:#000}</style></head><body>
<membrane-decisions id="ck"></membrane-decisions><pre id="out">pending</pre>
<script src="%(js)s"></script>
<script>
const BOARD = %(board)s, posts = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const box = (el) => { const r = el.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; };
(async () => {
  const out = {};
  try {
    const ck = document.getElementById("ck");
    ck.api = { get: async (p) => p === "/membrane/decisions" ? BOARD : {goals: []},
               post: async (p, b) => { posts.push([p, b]); return {ok: true}; }, chat: null };
    await sleep(400);
    const sr = ck.shadowRoot;
    out.vw = innerWidth;
    out.cards = [];
    for (const band of sr.querySelectorAll(".band.future, .band.now")) {
      const bb = box(band);
      for (const c of band.querySelectorAll(".dc"))
        out.cards.push({eid: c.dataset.eid, band: bb, card: box(c),
          selects: c.querySelectorAll("select, form.ans, input[type=radio]").length,
          ws: getComputedStyle(c.querySelector(".t")).whiteSpace,
          to: getComputedStyle(c.querySelector(".t")).textOverflow});
    }
    // click the front card of the left pile: the panel opens over everything
    const front = sr.querySelector(".band.future.fl .dc[data-eid='d:0']");
    front.click();
    await sleep(100);
    const pn = sr.querySelector(".dpanel");
    out.panel = pn && !pn.hidden ? box(pn) : null;
    if (out.panel) {
      out.ptext = pn.textContent;
      out.opts = [...pn.querySelectorAll("label.opt")].map(box);
      const sb = pn.querySelector("button[type=submit]"), r = sb.getBoundingClientRect();
      const hit = sr.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out.submitHit = !!(hit && hit.closest("button") === sb);
      pn.querySelectorAll("input[type=radio]")[1].checked = true;
      sb.click();
      await sleep(100);
      out.posts = posts;
      out.closedAfter = !sr.querySelector(".dpanel") || sr.querySelector(".dpanel").hidden;
      // reopen, then Escape closes it
      sr.querySelector(".dc[data-eid='w:0']").click();
      await sleep(50);
      out.flightPanel = !!(sr.querySelector(".dpanel") && !sr.querySelector(".dpanel").hidden);
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
      sr.querySelector(".dpanel") && sr.querySelector(".dpanel").dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, composed: true}));
      await sleep(50);
      out.escClosed = !sr.querySelector(".dpanel") || sr.querySelector(".dpanel").hidden;
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById("out").textContent = "RESULT" + JSON.stringify(out) + "END";
})();
</script></body></html>"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("cardfit")
    page = d / "page.html"
    page.write_text(PAGE % {"js": (UI / "decisions.js").as_uri(), "board": json.dumps(BOARD)})
    prof = d / "profile"
    dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                          "--user-data-dir=%s" % prof, "--allow-file-access-from-files",
                          "--window-size=%d,%d" % (W, H), "--virtual-time-budget=3000",
                          "--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=90).stdout
    m = re.search(r"RESULT(.*?)END", dom, re.S)
    assert m, dom[-2000:]
    out = json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    assert "error" not in out, out.get("error")
    return out


@needs_chrome
def test_every_card_fits_its_lane_and_the_screen(run):
    assert run["vw"] == W
    assert len(run["cards"]) >= 8
    for c in run["cards"]:
        assert c["card"]["l"] >= c["band"]["l"] - 1 and c["card"]["r"] <= c["band"]["r"] + 1, c["eid"]
        assert c["card"]["r"] <= W, c["eid"]


@needs_chrome
def test_long_titles_are_cut_with_an_ellipsis(run):
    for c in run["cards"]:
        assert c["ws"] == "nowrap" and c["to"] == "ellipsis", c["eid"]


@needs_chrome
def test_the_collapsed_card_shows_no_answer_options(run):
    assert all(c["selects"] == 0 for c in run["cards"])


@needs_chrome
def test_a_click_opens_the_card_as_a_floating_panel_with_everything(run):
    p = run["panel"]
    assert p, "no panel opened"
    assert p["l"] >= 0 and p["t"] >= 0 and p["r"] <= W and p["b"] <= H
    assert "an extremely long question title " * 5 in run["ptext"]
    assert LONG + "0" in run["ptext"]
    for o in run["opts"]:                       # every option wraps inside the panel
        assert o["l"] >= p["l"] and o["r"] <= p["r"]


@needs_chrome
def test_submit_in_the_panel_is_clickable_and_answers(run):
    assert run["submitHit"], "something covers the panel's Submit"
    assert ["/membrane/decision/answer", "d:0"] == [run["posts"][0][0], run["posts"][0][1]["card"]]
    assert run["posts"][0][1]["value"] == LONG + "0"
    assert run["closedAfter"]


@needs_chrome
def test_an_in_flight_card_opens_too_and_escape_closes(run):
    assert run["flightPanel"]
    assert run["escClosed"]


def test_hover_never_widens_a_card():
    src = (UI / "decisions.js").read_text()
    hover = re.findall(r"\.dc:hover[^{]*\{([^}]*)\}", src)
    assert hover and not any(re.search(r"\b(width|scale|translateX)\b", h) for h in hover)
