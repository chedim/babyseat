"""#358: Blurp gets the film's body in the viewer.

Where membrane home drew self as a 👶 (the tab at the left edge, the panel's
header) it now draws a small canvas body: a soft gold ellipse with a glow, a
slight wobble, two eyes offset toward what self looks at, an occasional blink,
closed-eye arcs and drifting z's when asleep, a small smile. The zoo's cards
draw each seat's Blurp the same way, from that seat's card.

The pure parts live in membrane/ui/home.js (HomeLogic) and run here under node:
blurpPose (the seat's card -> asleep / glow / look), blurpEyes (the eye offset
from a look angle), blurpLookAngle (a screen direction), blurpBlink, blurpZs,
blurpRuns (animate only while shown, the tab visible and motion allowed),
drawBlurp (against a recording 2D context) and BlurpBodies (the one animation
loop, driven with stubs).
"""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

# a 2D context that records what is drawn
REC = r"""
function rec() {
  const log = [];
  const g = { log, shadowBlur: 0, globalAlpha: 1, lineWidth: 1, fillStyle: "", strokeStyle: "", shadowColor: "", font: "",
    textAlign: "", textBaseline: "" };
  for (const m of ["save", "restore", "beginPath", "ellipse", "arc", "fill", "stroke", "fillText", "clearRect",
                   "setTransform", "moveTo", "lineTo", "closePath"])
    g[m] = (...a) => log.push([m].concat(a.map((v) => typeof v === "number" ? Math.round(v * 1000) / 1000 : v)));
  g.createRadialGradient = () => ({ addColorStop() {} });
  const fill = g.fill;
  g.fill = () => { log.push(["fill@blur", Math.round(g.shadowBlur * 1000) / 1000]); };
  return g;
}
"""


def _run(body):
    js = "const H=require(%r);%s let out; %s; process.stdout.write(JSON.stringify(out))" % (str(UI / "home.js"), REC, body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _home():
    return (UI / "home.js").read_text()


# ---- the pose: from the seat's card, nothing invented ----------------------------

@NODE
def test_an_awake_card_at_full_energy():
    p = _run("out = H.blurpPose({awake: true, asleep: false, energy: 1, mode: 'on'})")
    assert p["asleep"] is False
    assert p["glow"] == pytest.approx(1)
    assert p["look"] is None                                  # no look given: the eyes look forward


@NODE
def test_asleep_from_the_card():
    assert _run("out = H.blurpPose({awake: false, asleep: true, energy: 0.2, mode: 'on'})")["asleep"] is True
    # #516: switched off is not asleep -- real sleep (awake false, mode on) keeps the closed eyes
    assert _run("out = H.blurpPose({awake: false, asleep: false, energy: 0.9, mode: 'off'})")["asleep"] is False
    assert _run("out = H.blurpPose({awake: false, asleep: true, energy: 0.9, mode: 'watch'})")["asleep"] is True
    assert _run("out = H.blurpPose({state: 'asleep'})")["asleep"] is True
    assert _run("out = H.blurpPose({state: 'awake'})")["asleep"] is False


@NODE
def test_off_from_the_card_is_not_asleep():
    for card in ("{awake: false, mode: 'off', energy: 0.9}", "{mode: 'off'}", "{state: 'off'}"):
        p = _run("out = H.blurpPose(%s)" % card)
        assert p["off"] is True and p["asleep"] is False, card
    for card in ("{awake: true, mode: 'on'}", "{awake: false, mode: 'on'}", "{state: 'asleep'}", "{state: 'awake'}"):
        assert _run("out = H.blurpPose(%s)" % card)["off"] is False, card
    # the toggle's own view (before the card loads): switched off
    p = _run("out = H.blurpPose(null, {loaded: true, on: false})")
    assert p["off"] is True and p["asleep"] is False


@NODE
def test_the_glow_scales_with_energy():
    g = _run("out = [0, 0.5, 1].map((e) => H.blurpPose({awake: true, energy: e}).glow)")
    assert 0 < g[0] < g[1] < g[2] == pytest.approx(1)
    # no energy on the card (an older seat): the film's full glow, not a made-up level
    assert _run("out = H.blurpPose({awake: true}).glow") == pytest.approx(1)
    assert _run("out = H.blurpPose({awake: true, energy: null}).glow") == pytest.approx(1)


@NODE
def test_without_a_card_the_sleep_toggle_decides_then_nothing():
    assert _run("out = H.blurpPose(null, {loaded: true, on: false})")["off"] is True
    assert _run("out = H.blurpPose(null, {loaded: true, on: true})")["asleep"] is False
    p = _run("out = H.blurpPose(null, {loaded: false, on: false})")    # nothing known yet
    assert p["asleep"] is False and p["glow"] == pytest.approx(1)


@NODE
def test_the_look_passes_through():
    assert _run("out = H.blurpPose({awake: true}, null, 1.25).look") == pytest.approx(1.25)


# ---- the eyes ------------------------------------------------------------------------

@NODE
def test_the_eyes_look_forward_without_a_look():
    assert _run("out = H.blurpEyes(null, 100)") == {"dx": 0, "dy": 0}


@NODE
def test_the_eyes_shift_toward_the_look_angle():
    right = _run("out = H.blurpEyes(0, 100)")
    down = _run("out = H.blurpEyes(Math.PI / 2, 100)")
    left = _run("out = H.blurpEyes(Math.PI, 100)")
    assert right["dx"] == pytest.approx(10) and right["dy"] == pytest.approx(0, abs=1e-9)   # the film: s*.1, s*.08
    assert down["dx"] == pytest.approx(0, abs=1e-9) and down["dy"] == pytest.approx(8)
    assert left["dx"] == pytest.approx(-10)


@NODE
def test_the_look_angle_on_the_screen():
    assert _run("out = H.blurpLookAngle([0, 0], [10, 0])") == pytest.approx(0)
    assert _run("out = H.blurpLookAngle([5, 5], [5, 25])") == pytest.approx(math.pi / 2)
    assert _run("out = H.blurpLookAngle(null, [1, 1])") is None
    assert _run("out = H.blurpLookAngle([1, 1], null)") is None
    assert _run("out = H.blurpLookAngle([1, 1], [1.2, 1.1])") is None      # on top of self: forward


@NODE
def test_it_blinks_now_and_then():
    v = _run("out = Array.from({length: 4000}, (_, i) => H.blurpBlink(i * 0.01, 1))")
    assert set(v) == {1, 0.15}
    assert v.count(0.15) < len(v) * 0.1                     # occasionally


@NODE
def test_the_zs_drift_unless_still():
    a = _run("out = H.blurpZs(0.3, false)")
    b = _run("out = H.blurpZs(1.1, false)")
    assert a and b and a != b
    s0 = _run("out = H.blurpZs(0.3, true)")
    s1 = _run("out = H.blurpZs(7.9, true)")
    assert s0 and s0 == s1                                   # reduced motion: one still z


# ---- the drawing ---------------------------------------------------------------------

def _draw(t, pose, still=False):
    return _run("const g = rec(); H.drawBlurp(g, 40, 40, %s, %s, {still: %s}); out = g.log"
                % (t, json.dumps(pose), "true" if still else "false"))


@NODE
def test_awake_it_draws_a_body_two_open_eyes_and_a_smile():
    log = _draw(0.5, {"asleep": False, "glow": 1, "look": None})
    assert sum(1 for c in log if c[0] == "ellipse") == 3     # the body + two eyes
    assert sum(1 for c in log if c[0] == "arc") == 1         # the smile
    assert not any(c[0] == "fillText" for c in log)          # no z's


@NODE
def test_asleep_the_eyes_close_and_zs_drift():
    log = _draw(0.5, {"asleep": True, "glow": 1, "look": None})
    assert sum(1 for c in log if c[0] == "ellipse") == 1     # the body only
    assert sum(1 for c in log if c[0] == "arc") == 3         # two closed eyes + the smile
    assert any(c[0] == "fillText" and c[1] == "z" for c in log)


@NODE
def test_off_it_is_a_dim_blank_body_no_closed_eyes_no_zs():
    log = _draw(0.5, {"asleep": False, "off": True, "glow": 1, "look": None})
    assert sum(1 for c in log if c[0] == "ellipse") == 1     # the body only
    assert not any(c[0] == "arc" for c in log)              # no closed-eye arcs, no smile
    assert not any(c[0] == "fillText" for c in log)         # no z's
    assert next(c[1] for c in log if c[0] == "fill@blur") == 0   # no glow


@NODE
def test_the_glow_dims_as_it_tires():
    def blur(glow):
        log = _draw(0.5, {"asleep": False, "glow": glow, "look": None}, still=True)
        return next(c[1] for c in log if c[0] == "fill@blur")
    assert blur(1) > blur(0.3) > 0


@NODE
def test_the_eyes_move_with_the_look():
    def eyes(look):
        log = _draw(0.5, {"asleep": False, "glow": 1, "look": look}, still=True)
        return [c[1:3] for c in log if c[0] == "ellipse"][1:]
    assert eyes(0) != eyes(None) != eyes(math.pi)
    assert eyes(0)[0][0] > eyes(None)[0][0] > eyes(math.pi)[0][0]


@NODE
def test_it_wobbles_but_a_still_frame_does_not():
    awake = {"asleep": False, "glow": 1, "look": None}
    assert _draw(0.2, awake) != _draw(0.9, awake)
    assert _draw(0.2, awake, still=True) == _draw(5.3, awake, still=True)
    asleep = {"asleep": True, "glow": 0.5, "look": None}
    assert _draw(0.2, asleep, still=True) == _draw(5.3, asleep, still=True)


# ---- the loop: only while shown, the tab visible, motion allowed ---------------------

@NODE
def test_it_runs_only_while_visible_and_moving():
    out = _run("out = [H.blurpRuns(false, 1, false), H.blurpRuns(true, 1, false), H.blurpRuns(false, 0, false),"
               " H.blurpRuns(false, 2, true)]")
    assert out == [True, False, False, False]


LOOP = r"""
let frames = [], cancelled = 0, hidden = %(hidden)s, still = %(still)s, cbs = new Map();
const env = { raf: (f) => { frames.push(f); return frames.length; }, caf: () => { cancelled++; },
  hidden: () => hidden, still: () => still, now: () => 1000,
  observe: (el, cb) => { cbs.set(el, cb); return () => cbs.delete(el); }, colours: () => ({}) };
function canvas() { const g = rec(); return { width: 40, height: 40, clientWidth: 20, clientHeight: 20, g, getContext: () => g }; }
const B = new H.BlurpBodies(env);
const a = canvas();
B.add(a, { asleep: false, glow: 1, look: null });
"""


def _loop(script, hidden=False, still=False):
    return _run(LOOP % {"hidden": json.dumps(hidden), "still": json.dumps(still)} + script)


@NODE
def test_no_frame_until_the_canvas_is_shown():
    out = _loop("const f0 = frames.length; cbs.get(a)(true); out = [f0, frames.length];")
    assert out == [0, 1]


@NODE
def test_hidden_again_the_loop_stops():
    out = _loop("cbs.get(a)(true); cbs.get(a)(false); const f = frames.shift(); f(16); out = [frames.length];")
    assert out == [0]                                       # the pending frame does not ask for another


@NODE
def test_a_hidden_tab_asks_no_frames():
    out = _loop("cbs.get(a)(true); out = [frames.length, a.g.log.length > 0];", hidden=True)
    assert out == [0, True]                                 # one still frame is drawn, no loop


@NODE
def test_reduced_motion_draws_one_still_frame():
    out = _loop("cbs.get(a)(true); const n = a.g.log.length; B.set(a, {asleep: true, glow: 0.5, look: null});"
                "out = [frames.length, n > 0, a.g.log.length > n];", still=True)
    assert out == [0, True, True]                           # redrawn on a change, never looped


@NODE
def test_dropped_canvases_are_forgotten():
    out = _loop("cbs.get(a)(true); B.drop(a); const f = frames.shift(); if (f) f(16); out = [frames.length, cbs.has(a)];")
    assert out == [0, False]


@NODE
def test_off_the_page_it_pauses_and_comes_back():
    out = _loop("cbs.get(a)(true); B.pause(true); const c = cancelled; B.pause(false); out = [c, frames.length];")
    assert out == [1, 2]                                    # cancelled when detached, a new frame when back


# ---- the zoo's cards ------------------------------------------------------------------

@NODE
def test_an_online_zoo_card_carries_its_blurps_body():
    h = _run("out = H.zooCard(%s, 1760000600)" % json.dumps(
        {"id": "laptop", "name": "Laptop", "online": True,
         "card": {"name": "Pip", "level": 3, "state": "asleep", "energy": 0.4}}))
    assert re.search(r'<canvas class="zbody"[^>]*aria-hidden="true"', h)
    assert 'data-asleep="1"' in h and 'data-energy="0.4"' in h


@NODE
def test_an_off_zoo_card_says_off_not_asleep():
    h = _run("out = H.zooCard(%s, 1760000600)" % json.dumps(
        {"id": "laptop", "name": "Laptop", "online": True,
         "card": {"name": "Pip", "level": 3, "mode": "off", "state": "off"}}))
    assert 'data-off="1"' in h and 'data-asleep="0"' in h
    assert "asleep" not in h.replace('data-asleep', '')


@NODE
def test_an_offline_zoo_card_draws_no_body():
    h = _run("out = H.zooCard(%s, 1760000600)" % json.dumps({"id": "p", "name": "Phone", "online": False}))
    assert "zbody" not in h


# ---- the page's wiring (source) --------------------------------------------------------

def test_the_tab_draws_the_body_not_a_baby():
    home = _home()
    btn = home.split('<button class="babybtn"', 1)[1].split("</button>", 1)[0]
    assert '<canvas class="bbody"' in btn and "👶" not in btn.split(">", 1)[1]
    assert 'aria-label="${nm}: its character sheet and what it is doing"' in btn   # the label stays


def test_the_panel_header_draws_the_body():
    home = _home()
    title = home.split('<span class="sptitle">', 1)[1].split("</span>", 1)[0]
    assert '<canvas class="bbody' in title and "👶" not in title and "${nm}" in title


def test_the_page_reads_the_card_and_pauses_when_hidden():
    home = _home()
    assert '"/membrane/self/card"' in home
    body = home.split("class BlurpBodies", 1)[1].split("\n}\n", 1)[0]
    assert "IntersectionObserver" in home and "requestAnimationFrame" in home
    assert "prefers-reduced-motion: reduce" in home
    assert "visibilitychange" in home
    assert "blurpRuns(" in body
