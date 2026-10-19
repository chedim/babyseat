"""#289: a rung achieved is unmissable -- a celebration over membrane home, a
short chime and a browser notification, once per rung per viewer.

The page decides with pure helpers (membrane/ui/home.js, HomeLogic):
rungToCelebrate(level, last, lines) is the once-per-rung rule (the seat's
void-aware passed rung against the last one this viewer celebrated); the
caption and the notification say "<name> reached rung N: <rung name>"; the
chime plays only with the bell on and after a user gesture; a notification
only in a secure context, with permission, the bell on; the bell's title says
why when notifications can't work here.
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


def _rule(level, last, lines=()):
    return _js("H.rungToCelebrate(%s, %s, %s)" % (json.dumps(level), json.dumps(last), json.dumps(list(lines))))


# ---- once per rung ---------------------------------------------------------------------

@NODE
def test_a_new_pass_while_open_celebrates_once():
    lines = [{"kind": "rung", "rung": 4, "ts": 100}]
    assert _rule(4, 3, lines) == {"rung": 4, "last": 4}
    assert _rule(4, 4, lines) == {"rung": None, "last": 4}        # the next tick: nothing again


@NODE
def test_a_pass_reached_while_away_celebrates_once_on_the_next_visit():
    # the rung line scrolled out of the recent window: the seat's level carries it
    assert _rule(5, 3, []) == {"rung": 5, "last": 5}               # one celebration, the highest
    assert _rule(5, 5, []) == {"rung": None, "last": 5}


@NODE
def test_a_reload_never_re_fires():
    lines = [{"kind": "rung", "rung": 6, "ts": 100}]
    assert _rule(6, 6, lines) == {"rung": None, "last": 6}
    assert _rule(6, "6", lines) == {"rung": None, "last": 6}       # localStorage hands back a string


@NODE
def test_a_first_visit_never_celebrates_the_backlog():
    lines = [{"kind": "rung", "rung": n, "ts": 100 + n} for n in range(6, 0, -1)]
    assert _rule(6, None, lines) == {"rung": None, "last": 6}
    assert _rule(0, None, []) == {"rung": None, "last": 0}


@NODE
def test_a_voided_pass_never_celebrates():
    # the seat's level is void-aware (#254): a voided pass never raises it
    assert _rule(6, 6, [{"kind": "rung_void", "rung": 7, "ts": 200}, {"kind": "rung", "rung": 7, "ts": 100}]) \
        == {"rung": None, "last": 6}
    # a stale level that still counts it: the record's void line vetoes, and nothing is remembered
    lines = [{"kind": "rung_void", "rung": 7, "ts": 200}, {"kind": "rung", "rung": 7, "ts": 100}]
    assert _rule(7, 6, lines) == {"rung": None, "last": 6}
    # a void the seat has already applied: the level comes back down, quietly
    assert _rule(6, 7, lines) == {"rung": None, "last": 6}
    # a later real pass of that rung is a new pass
    again = [{"kind": "rung", "rung": 7, "ts": 300}] + lines
    assert _rule(7, 6, again) == {"rung": 7, "last": 7}


@NODE
def test_an_unknown_level_changes_nothing():
    assert _rule(None, 4, []) == {"rung": None, "last": 4}
    assert _rule(None, None, []) == {"rung": None, "last": None}


@NODE
def test_the_memory_is_per_seat_and_per_self():
    a = _js("H.rungKey('Argus', 'Blurp')")
    assert a.startswith("membrane.home.rung")
    assert a != _js("H.rungKey('Argus', 'Zed')") and a != _js("H.rungKey('Bob', 'Blurp')")


# ---- the words -------------------------------------------------------------------------

@NODE
def test_the_caption_and_the_notification_say_the_rung():
    assert _js("H.celebrateText('Blurp', 7, 'take turns')") == "Blurp reached rung 7: take turns!"
    assert _js("H.rungNotifyText('Blurp', 7, 'take turns')") == "Blurp reached rung 7: take turns"
    # no name given: the ladder's own
    assert _js("H.celebrateText('Zed', 3)") == "Zed reached rung 3: tell things apart!"


# ---- the chime, the notification, the bell ---------------------------------------------

@NODE
def test_the_chime_waits_for_a_gesture_and_the_bell():
    assert _js("H.chimeOk({ on: true, gesture: true })") is True
    assert _js("H.chimeOk({ on: true, gesture: false })") is False
    assert _js("H.chimeOk({ on: false, gesture: true })") is False


@NODE
def test_the_chime_is_short_and_synthesized():
    notes = _js("H.chimeNotes()")
    assert 2 <= len(notes) <= 6
    assert all(n["f"] > 200 and n["dur"] > 0 for n in notes)
    assert max(n["t"] + n["dur"] for n in notes) <= 2.0


@NODE
def test_a_notification_needs_a_secure_context_permission_and_the_bell():
    ok = {"on": True, "secure": True, "api": True, "permission": "granted"}
    assert _js("H.notifyReady(%s)" % json.dumps(ok)) is True
    for k, v in (("on", False), ("secure", False), ("api", False), ("permission", "default"), ("permission", "denied")):
        assert _js("H.notifyReady(%s)" % json.dumps(dict(ok, **{k: v}))) is False, k


@NODE
def test_the_bell_says_why_notifications_cannot_work():
    v = lambda **kw: _js("H.bellView(%s)" % json.dumps(dict({"on": True, "secure": True, "api": True, "permission": "default"}, **kw)))
    assert v()["icon"] == "🔔" and v()["ask"] is True                # a click asks (a user gesture)
    assert v(on=False)["icon"] == "🔕" and v(on=False)["ask"] is False
    assert "https or localhost" in v(secure=False)["title"] and v(secure=False)["ask"] is False
    assert "no notifications" in v(api=False)["title"] and v(api=False)["ask"] is False
    assert "blocked" in v(permission="denied")["title"] and v(permission="denied")["ask"] is False
    assert v(permission="granted")["ask"] is False and "notifications on" in v(permission="granted")["title"]
    # asked once and the prompt dismissed: the next tap mutes, it never nags
    assert v(asked=True)["ask"] is False and "mute" in v(asked=True)["title"]


# ---- the page wiring --------------------------------------------------------------------

def test_the_page_wires_the_bell_the_overlay_and_the_notification():
    home = (UI / "home.js").read_text()
    assert 'class="rungbell"' in home                               # the toggle, in the top bar
    assert "isSecureContext" in home and "Notification.requestPermission" in home
    assert home.count("Notification.requestPermission") == 1        # only from the bell's click
    assert "tag:" in home                                           # a tag per rung: never a duplicate
    assert ".celebrate" in home and "pointer-events:none" in home.replace(" ", "")
    assert "prefers-reduced-motion" in home
    assert "createOscillator" in home                               # synthesized, no asset files
    assert "membrane.home.bell" in home


def test_the_amoeba_celebrates():
    f3 = (UI / "field3d.js").read_text()
    assert "selfCelebrate(" in f3
