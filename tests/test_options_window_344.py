"""#344: the options window (⚙) in membrane home's top bar.

The window's logic is HomeLogic.OptionsWindow (membrane/ui/home.js), driven
here in node with stub elements: the ⚙ button opens and closes it (Escape and
× close it too), opening reads GET /membrane/options, the CPU share slider
(10-100 %) posts {"self_cpu_share": pct/100} and the window shows the saved
value or the seat's refusal. The 🔔 and ▶ buttons stay in the top bar (the ▶
is the seat's pause since #348).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

OPTS = {"self_cpu_share": 0.5, "defaults": {"self_cpu_share": 0.5}, "current_tau_s": 180.0,
        "current_tau_note": "changing it needs the operator's approval", "energy": None,
        "writable": ["self_cpu_share"]}

# stub elements + a fake seat; `script` runs inside an async main and returns a value
HARNESS = r"""
const H = require(%(home)r);
function el() {
  const attrs = {}, cls = new Set();
  return { hidden: true, textContent: "", innerHTML: "", value: "", disabled: false, focused: 0,
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    classList: { toggle(c, on) { on ? cls.add(c) : cls.delete(c); }, contains(c) { return cls.has(c); } },
    focus() { this.focused++; } };
}
const calls = [];
let reply = %(reply)s, refuse = %(refuse)s;
const seat = {
  async get(p) { calls.push(["GET", p]); return JSON.parse(JSON.stringify(reply)); },
  async post(p, body) {
    calls.push(["POST", p, body]);
    if (refuse) { const e = new Error(refuse); e.status = 400; throw e; }
    reply = Object.assign({}, reply, body); return JSON.parse(JSON.stringify(reply));
  },
};
const btn = el(), panel = el(), body = el(), msg = el();
const w = new H.OptionsWindow({ btn, panel, body, msg, get: seat.get, post: seat.post });
(async () => {
  const out = await (async () => { %(script)s })();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ crash: String(e && e.stack || e) })); });
"""


def _run(script, reply=OPTS, refuse=None):
    js = HARNESS % {"home": str(UI / "home.js"), "reply": json.dumps(reply),
                    "refuse": json.dumps(refuse), "script": script}
    out = json.loads(subprocess.check_output(["node", "-e", js]))
    assert "crash" not in out, out.get("crash") if isinstance(out, dict) else out
    return out


@NODE
def test_the_button_opens_and_closes_the_window():
    out = _run("""
      const s0 = [panel.hidden, btn.getAttribute("aria-expanded")];
      await w.toggle();
      const s1 = [panel.hidden, btn.getAttribute("aria-expanded"), btn.classList.contains("on")];
      await w.toggle();
      const s2 = [panel.hidden, btn.getAttribute("aria-expanded"), btn.classList.contains("on")];
      return { s0, s1, s2, calls };""")
    assert out["s0"][0] is True
    assert out["s1"] == [False, "true", True]
    assert out["s2"] == [True, "false", False]
    assert out["calls"] == [["GET", "/membrane/options"]]       # a read on open, none on close


@NODE
def test_escape_closes_and_gives_the_focus_back():
    out = _run("""
      await w.toggle();
      let stopped = 0;
      w.key({ key: "a", stopPropagation() { stopped++; } });
      const after_a = panel.hidden;
      w.key({ key: "Escape", stopPropagation() { stopped++; } });
      return { after_a, hidden: panel.hidden, focused: btn.focused, stopped };""")
    assert out == {"after_a": False, "hidden": True, "focused": 1, "stopped": 1}


@NODE
def test_opening_paints_the_rows():
    out = _run("await w.toggle(); return { html: body.innerHTML };")
    html = out["html"]
    assert 'type="range"' in html and 'min="10"' in html and 'max="100"' in html and 'value="50"' in html
    assert "180" in html and "approval" in html        # the fade, read-only
    assert "energy" in html and "not running yet" in html   # #363: energy where health was, none reported yet
    assert "#333" not in html and "health" not in html


@NODE
def test_the_cpu_share_posts_the_right_value():
    out = _run("""
      await w.toggle();
      const saved = await w.setShare("30");
      return { calls, saved, msg: msg.textContent, html: body.innerHTML };""")
    assert out["calls"][1] == ["POST", "/membrane/options", {"self_cpu_share": 0.3}]
    assert out["saved"]["self_cpu_share"] == 0.3
    assert "30%" in out["msg"] and "saved" in out["msg"]
    assert 'value="30"' in out["html"]


@NODE
def test_the_slider_never_posts_outside_ten_to_a_hundred():
    out = _run("""
      await w.toggle();
      await w.setShare("5"); await w.setShare("140"); await w.setShare("33.4");
      return calls.filter((c) => c[0] === "POST").map((c) => c[2].self_cpu_share);""")
    assert out == [0.1, 1, 0.33]


@NODE
def test_a_refusal_is_shown_and_the_saved_value_stays():
    out = _run("""
      await w.toggle();
      const saved = await w.setShare("30");
      return { saved, msg: msg.textContent, html: body.innerHTML };""", refuse="self_cpu_share must be in (0, 1]")
    assert out["saved"] is None
    assert "self_cpu_share must be in (0, 1]" in out["msg"]
    assert 'value="50"' in out["html"]                 # repainted at the seat's value


@NODE
def test_a_read_failure_is_shown():
    out = _run("""
      const bad = { get: async () => { throw new Error("/membrane/options → 503"); }, post: seat.post };
      const w2 = new H.OptionsWindow({ btn, panel, body, msg, get: bad.get, post: bad.post });
      await w2.toggle();
      return { hidden: panel.hidden, msg: msg.textContent };""")
    assert out["hidden"] is False and "503" in out["msg"]


@NODE
def test_the_percent_helpers():
    js = 'const H=require(%r); process.stdout.write(JSON.stringify([H.optSharePct(0.5), H.optSharePct(1), H.optShareFromPct("25"), H.optShareFromPct(0), H.optShareFromPct("x")]))' % str(UI / "home.js")
    assert json.loads(subprocess.check_output(["node", "-e", js])) == [50, 100, 0.25, 0.1, 0.5]


def test_the_top_bar_keeps_the_bell_and_play_and_gains_the_gear():
    src = (UI / "home.js").read_text()
    top = re.search(r'<div class="top glass">(.*?)</div>', src, re.S).group(1)
    for cls in ("pausebtn", "rungbell", "optbtn"):        # #348: the ▶ is the seat's pause now
        assert f'class="{cls}"' in top, cls
    assert re.search(r'class="optbtn"[^>]*>⚙</button>', top)
    assert 'aria-controls="optpanel"' in top
    assert 'class="optpanel glass" id="optpanel"' in src


@NODE
def test_the_energy_row_shows_percent_and_asleep_363():
    r = dict(OPTS, energy={"energy": 0.873, "asleep": False})
    out = _run("await w.toggle(); return { html: body.innerHTML };", reply=r)
    assert "87%" in out["html"] and "awake" in out["html"]
    r = dict(OPTS, energy={"energy": 0.2, "asleep": True})
    out = _run("await w.toggle(); return { html: body.innerHTML };", reply=r)
    assert "20%" in out["html"] and "asleep" in out["html"]
