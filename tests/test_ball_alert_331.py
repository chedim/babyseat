"""#331: self rolling the ball to you is hard to miss. Membrane home raises an
alert once per roll of self's (a toast with a Roll it back button, the dock's
ball glowing, the bell's chime, the tab title flashing and an OS notification
while the tab is hidden), and never for your own roll-back, never twice for
one roll, never loudly for a ball that was already on your side when the page
opened (it only glows).

The page decides with pure helpers (membrane/ui/home.js, HomeLogic):
ballAlert(prev, toys) reads the dock's toys against what this page saw last
(prev null: the page's first read). A roll is identified by self's change
count on the ball (the dock's `changes.self`), so a fetch-and-re-roll of an
overdue ball is a new roll and your optimistic roll-back is none. Display-only:
nothing here posts anything but the person's own roll.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr, f="home.js", name="H"):
    js = ('const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (name, str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def ball(side, n_self, by="self", name="toy:ball", locked=False):
    t = {"name": name, "kind": "ball", "reading": {"side": side}, "changes": {"self": n_self},
         "last": {"by": by, "what": "roll", "age_s": 1.0} if by else None}
    if locked:
        t = {"name": name, "kind": "ball", "locked": True}
    return t


DIE = {"name": "toy:die", "kind": "die", "reading": {"value": 3}, "changes": {"self": 9}, "last": {"by": "self"}}


def _alert(prev, toys):
    return _js("H.ballAlert(%s, %s)" % (json.dumps(prev), json.dumps(toys)))


# ---- once per roll of self's ---------------------------------------------------------

@NODE
def test_selfs_roll_alerts_once():
    first = _alert(None, [ball("self", 4, by="alice"), DIE])
    assert first["alert"] == [] and first["yours"] == []
    rolled = _alert(first["seen"], [ball("other", 5), DIE])
    assert rolled["alert"] == ["toy:ball"] and rolled["yours"] == ["toy:ball"]
    again = _alert(rolled["seen"], [ball("other", 5), DIE])          # the next poll: the same roll
    assert again["alert"] == [] and again["yours"] == ["toy:ball"]


@NODE
def test_a_ball_already_on_your_side_at_page_load_only_glows():
    out = _alert(None, [ball("other", 5)])
    assert out["alert"] == [] and out["yours"] == ["toy:ball"]


@NODE
def test_your_own_roll_back_never_alerts():
    seen = _alert(None, [ball("other", 5)])["seen"]
    back = _alert(seen, [ball("self", 5, by="alice")])
    assert back["alert"] == [] and back["yours"] == []


@NODE
def test_the_optimistic_paint_and_its_undo_never_alert():
    # #325: the dock paints the roll-back at once (the seat's counts untouched),
    # and a failed poke puts the old toys back: the same roll, no new alert
    seen = _alert(None, [ball("other", 5)])["seen"]
    guess = _alert(seen, [ball("self", 5)])
    undo = _alert(guess["seen"], [ball("other", 5)])
    assert guess["alert"] == [] and undo["alert"] == [] and undo["yours"] == ["toy:ball"]


@NODE
def test_an_overdue_fetch_and_re_roll_is_a_new_roll():
    seen = _alert(None, [ball("self", 5)])["seen"]
    seen = _alert(seen, [ball("other", 6)])["seen"]
    out = _alert(seen, [ball("other", 7)])
    assert out["alert"] == ["toy:ball"]


@NODE
def test_a_roll_and_return_between_two_reads_is_not_news():
    seen = _alert(None, [ball("self", 5)])["seen"]
    out = _alert(seen, [ball("self", 6, by="alice")])
    assert out["alert"] == [] and out["yours"] == []


@NODE
def test_only_self_rolling_it_alerts():
    seen = _alert(None, [ball("self", 5)])["seen"]
    assert _alert(seen, [ball("other", 5, by="model")])["alert"] == []


@NODE
def test_a_seat_without_counts_alerts_on_the_side_changing():
    t = lambda side, by="self": [{"name": "toy:ball", "kind": "ball", "reading": {"side": side}, "last": {"by": by}}]
    seen = _alert(None, t("self", "alice"))["seen"]
    out = _alert(seen, t("other"))
    assert out["alert"] == ["toy:ball"]
    assert _alert(out["seen"], t("other"))["alert"] == []


@NODE
def test_locked_balls_and_other_toys_are_ignored():
    out = _alert({}, [ball("other", 1, locked=True), DIE])
    assert out == {"alert": [], "yours": [], "seen": {}}
    assert _alert(None, None) == {"alert": [], "yours": [], "seen": {}}


# ---- the words, the chime, the title ---------------------------------------------------

@NODE
def test_the_alert_names_self():
    assert _js('H.ballAlertText("Blurp")') == "⚽ Blurp rolled you the ball"
    assert "roll it back" in _js("H.ballNotifyBody()")


@NODE
def test_the_ball_has_its_own_short_chime():
    notes = _js("H.ballChimeNotes()")
    assert 1 <= len(notes) <= 3
    assert max(n["t"] + n["dur"] for n in notes) < 1.0
    assert notes != _js("H.chimeNotes()")


@NODE
def test_the_title_flashes_between_the_alert_and_the_page_title():
    assert _js('H.titleFlash("membrane", "⚽ your turn", 0)') == "⚽ your turn"
    assert _js('H.titleFlash("membrane", "⚽ your turn", 1)') == "membrane"
    assert _js('H.titleFlash("membrane", "⚽ your turn", 2)') == "⚽ your turn"


# ---- the wiring ------------------------------------------------------------------------

def _src(f):
    return (UI / f).read_text()


def _body(src, head):
    b = src[src.index(head):]
    return b[:b.index("\n    }\n")]


def test_the_panel_hands_selfs_cycles_to_the_page():
    body = _src("panel.js")
    cyc = body[body.index('if (ev.type === "self.cycle"'):]
    assert "ai-self-cycle" in cyc[:cyc.index('if (ev.type === "membrane.name")')]


def test_home_checks_the_ball_on_selfs_cycle_even_while_hidden():
    src = _src("home.js")
    assert 'addEventListener("ai-self-cycle"' in src
    tick = _body(src, "async _dockTick(")
    assert "force" in tick.split("\n")[0], "the cycle's check reads the toys while the tab is hidden"


def test_the_paint_decides_the_alert_so_the_optimistic_roll_back_dismisses_it():
    paint = _body(_src("home.js"), "_paintDock(toys) {")
    assert "ballAlert(" in paint


def test_the_toast_rolls_it_back_through_the_docks_own_poke():
    src = _src("home.js")
    assert 'class="ballnote"' in src
    assert "_onToyPoke(" in _body(src, "_onBallNote(")


def test_clicking_the_notification_focuses_the_page():
    n = _body(_src("home.js"), "_notify(text, tag, body")
    assert "onclick" in n and "focus()" in n


def test_the_glow_stands_still_under_reduced_motion():
    src = _src("home.js")
    i = src.index("@keyframes ballglow")
    assert "prefers-reduced-motion: no-preference" in src[max(0, i - 400):i]


def test_one_chime_method_the_bells():
    # a later same-named method silently replaced the bell's chime (the wake
    # word's two notes, never muted): the ball's chime must be the bell's
    import re
    src = _src("home.js")
    defs = re.findall(r"^    (?:async )?_chime\(", src, re.M)
    assert len(defs) == 1, "two _chime methods: the later one wins"
    assert "chimeOk(" in _body(src, "_chime(notes)")
