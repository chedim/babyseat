"""#470: the decision cockpit -- the decision board turned 90 degrees.

The time axis is vertical: the future (open cards) piles up above the center
input bar, the cards in flight sit on the bar's line, the past (decided cards)
falls below it. The center column is the goals, a small field under the bar,
the bar itself and the chat below it, newest first. The layout logic is pure
(DecisionLogic in membrane/ui/decisions.js) and runs here under node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr, f="decisions.js", name="D"):
    js = ('const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (name, str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def card(eid, status="open", title=None, **kw):
    return {"eid": eid, "title": title or eid, "description": "", "status": status, **kw}


BOARD = {
    "cards": [
        card("c:release", title="Release when", var={"type": "string"}),
        card("c:limit", title="Turn limit", var={"type": "int", "unit": "s"}, pick=1800),
        card("c:banner", "in_flight", title="Banner window"),
        card("c:alpha", "in_flight", title="Alpha"),
        card("c:old", "decided", title="Watchdog", value=300, set_at="2026-10-20T10:00:00Z"),
        card("c:new", "decided", title="Converge", value=True, set_at="2026-10-23T10:00:00Z",
             chat="ses_a", message="msg_9"),
        card("c:mid", "decided", title="Middle", value=1, set_at="2026-10-21T10:00:00Z", chat="ses_b"),
    ],
    "edges": [{"from": "c:limit", "to": "c:release", "rel": "blocks"}],
}


@NODE
def test_the_bands_run_future_now_past_each_nearest_the_bar_first():
    b = _js("D.bands(%s)" % json.dumps(BOARD))
    # the future: the first thing to decide is nearest the bar (the limit blocks the release)
    assert [c["eid"] for c in b["future"]] == ["c:limit", "c:release"]
    assert [c["eid"] for c in b["now"]] == ["c:alpha", "c:banner"]
    # the past: the newest decision is nearest the bar
    assert [c["eid"] for c in b["past"]] == ["c:new", "c:mid", "c:old"]


@NODE
def test_cards_alternate_left_and_right_two_to_a_row():
    out = _js('D.place(["a", "b", "c", "d", "e"])')
    assert [(p["card"], p["side"], p["row"]) for p in out] == [
        ("a", "left", 0), ("b", "right", 0), ("c", "left", 1), ("d", "right", 1), ("e", "left", 2)]
    left = _js('D.sideLists(["a", "b", "c"])')
    assert left == {"left": ["a", "c"], "right": ["b"]}


@NODE
def test_the_future_grows_toward_the_bar_and_the_past_fades_away():
    f0, f3 = _js('D.depthStyle(0, "future")'), _js('D.depthStyle(3, "future")')
    assert f0 == {"scale": 1, "tilt": 0, "opacity": 1}
    assert f3["scale"] < 1 and f3["tilt"] > 0 and f3["opacity"] < 1      # farther up: smaller, tilted back
    p0, p3 = _js('D.depthStyle(0, "past")'), _js('D.depthStyle(3, "past")')
    assert p0 == {"scale": 1, "tilt": 0, "opacity": 1}
    assert p3["scale"] < 1 and p3["tilt"] < 0 and p3["opacity"] < f3["opacity"]   # falling away fades more
    far = _js('D.depthStyle(40, "past")')
    assert far["scale"] >= 0.5 and far["opacity"] > 0                    # never vanishes outright
    assert _js('D.depthStyle(0, "now")') == {"scale": 1, "tilt": 0, "opacity": 1}


@NODE
def test_each_new_decision_pushes_the_oldest_out_of_view():
    past = ["n1", "n2", "n3", "n4", "n5"]
    assert _js('D.pushOut(%s, 2)' % json.dumps(past)) == {"shown": ["n1", "n2", "n3", "n4"], "gone": 1}
    assert _js('D.pushOut(%s, 2)' % json.dumps(["n0"] + past)) == {"shown": ["n0", "n1", "n2", "n3"], "gone": 2}
    assert _js('D.pushOut([], 3)') == {"shown": [], "gone": 0}


def goal(eid, born, active=False):
    return {"eid": eid, "title": eid, "active": active, "born": born}


@NODE
def test_the_goals_stack_on_the_bar_oldest_nearest_newest_on_top():
    # round 2: the goals sit right above the input bar and grow upward like the
    # open cards' pile -- index 0 is nearest the bar (the oldest), the newest on top
    goals = [goal("g:new", 300), goal("g:old", 100), goal("g:mid", 200, active=True)]
    out = _js("D.goalStack(%s, 5)" % json.dumps(goals))
    assert [g["eid"] for g in out["stack"]] == ["g:old", "g:mid", "g:new"] and out["more"] == 0
    assert [g["eid"] for g in out["stack"] if g["active"]] == ["g:mid"]       # still marked


@NODE
def test_a_full_goal_stack_keeps_the_active_and_the_newest_and_counts_the_rest():
    goals = [goal("g:1", 100, active=True), goal("g:2", 200), goal("g:3", 300), goal("g:4", 400), goal("g:5", 500)]
    out = _js("D.goalStack(%s, 3)" % json.dumps(goals))
    # the active one stays however old; the rest of the room goes to the newest; still oldest nearest the bar
    assert [g["eid"] for g in out["stack"]] == ["g:1", "g:4", "g:5"] and out["more"] == 2
    # a goal with no birth time counts as the oldest
    out = _js("D.goalStack(%s, 5)" % json.dumps([goal("g:x", None), goal("g:y", 10)]))
    assert [g["eid"] for g in out["stack"]] == ["g:x", "g:y"]


def test_the_goals_sit_directly_above_the_bar():
    src = (UI / "decisions.js").read_text()
    rule = _cockpit_rule(src, ".goals ul")
    assert "flex-direction:column-reverse" in rule                     # the stack grows upward from the bar
    ct = src[src.index('<div class="ct">'):src.index('</div></div>', src.index('<div class="ct">'))]
    assert ct.index('class="mark"') < ct.index('class="goals"')        # the babyseat above, the goals last: on the bar


@NODE
def test_a_card_click_loads_its_question_into_the_center_chat():
    fresh = BOARD["cards"][1]
    # no chat yet: the center chat's open session is recorded on the card
    c = _js('D.loadCard(%s, "ses_now")' % json.dumps(fresh))
    assert c["switchTo"] is None and c["record"] == "ses_now"
    assert '"Turn limit"' in c["text"] and "1800" in c["text"]
    # the card has its own chat: the center chat switches to it, nothing is recorded
    c = _js('D.loadCard(%s, "ses_now")' % json.dumps(dict(fresh, chat="ses_card")))
    assert c["switchTo"] == "ses_card" and c["record"] is None


@NODE
def test_an_answer_from_the_cards_chat_names_its_message():
    open_card = dict(BOARD["cards"][1], chat="ses_card")
    assert _js('D.answerLink(%s, "ses_card", "msg_7")' % json.dumps(open_card)) == {"conv": "ses_card", "message": "msg_7"}
    # the center chat shows another session: the card's chat, no message
    assert _js('D.answerLink(%s, "ses_other", "msg_7")' % json.dumps(open_card)) == {"conv": "ses_card", "message": ""}
    assert _js('D.answerLink(%s, "ses_x", "msg_7")' % json.dumps(BOARD["cards"][0])) == {"conv": "", "message": ""}


@NODE
def test_a_past_card_finds_where_it_was_decided():
    new, mid, old = BOARD["cards"][5], BOARD["cards"][6], BOARD["cards"][4]
    # its chat is open in the center: hover scrolls to the message
    assert _js('D.pastLink(%s, "ses_a")' % json.dumps(new)) == {"hover": "scroll", "open": None, "message": "msg_9"}
    # its chat is another session: hover only highlights; a click opens that session (and then scrolls)
    assert _js('D.pastLink(%s, "ses_z")' % json.dumps(new)) == {"hover": "highlight", "open": "ses_a", "message": "msg_9"}
    assert _js('D.pastLink(%s, "ses_a")' % json.dumps(mid)) == {"hover": "highlight", "open": "ses_b", "message": ""}
    assert _js('D.pastLink(%s, "ses_a")' % json.dumps(old)) == {"hover": "highlight", "open": None, "message": ""}


# ---- the chat panel's cockpit mode (panel.js) ---------------------------------------

@NODE
def test_the_reversed_chat_sticks_to_its_top():
    # the newest message is right under the bar: "at the latest" is the top of the scroller
    assert _js('P.shouldStick({scrollHeight: 1000, scrollTop: 0, clientHeight: 300}, 80, true)', "panel.js", "P") is True
    assert _js('P.shouldStick({scrollHeight: 1000, scrollTop: 400, clientHeight: 300}, 80, true)', "panel.js", "P") is False
    assert _js('P.shouldStick({scrollHeight: 1000, scrollTop: 700, clientHeight: 300})', "panel.js", "P") is True


def test_the_panel_has_a_cockpit_mode_not_a_fork():
    src = (UI / "panel.js").read_text()
    assert ":host([cockpit])" in src
    assert "column-reverse" in src
    for m in ("scrollToMessage(", "prefill(", "openSession(", "lastMessageId(", "get sessionId("):
        assert m in src, m


def test_the_cockpit_view_reuses_the_chat_and_the_field():
    src = (UI / "home.js").read_text()
    assert 'setAttribute("cockpit"' in src                 # the real chat element, in cockpit mode
    assert "/membrane/goals" in (UI / "decisions.js").read_text()
    # the decisions view boots the field (a small one), the chat view still doesn't
    assert 'VIEW === "chat") {' in src


def test_the_camera_hint_never_shows_over_the_cockpit():
    """'Blurp is not moving yet' is the 3D view's camera hint; in the cockpit it
    landed right under the input bar (operator, 2026-10-23)."""
    src = (UI / "home.js").read_text()
    assert ".view-decisions .msg" in src


def test_the_chamfered_corners_draw_their_edge():
    """The cards' cut corners are clipped with clip-path, which cut the border
    too: the top-left and bottom-right edges never rendered. Each corner draws
    its own diagonal in the card's edge colour."""
    src = (UI / "decisions.js").read_text()
    assert ".dc::before" in src and ".dc::after" in src
    assert "border-color:var(--edge)" in src


def test_a_reply_runs_newest_part_first_in_the_cockpit():
    """The cockpit reads past at the bottom, latest on top -- also inside one
    reply: its tool calls and words were still oldest-first (operator,
    2026-10-23). The reply's who-line stays its header."""
    src = (UI / "panel.js").read_text()
    assert ":host([cockpit]) .msg.ai { display:flex; flex-direction:column-reverse;" in src
    assert ":host([cockpit]) .msg.ai > .who { order:99; }" in src


def test_the_side_lanes_clear_blurps_tab_and_toys():
    """Blurp's tab and its toy dock float at the left edge: the lanes' side
    margin is 1.5x the first cut's 44px (operator, 2026-10-23)."""
    src = (UI / "decisions.js").read_text()
    assert "padding:8px 66px 0;" in src


def _cockpit_rule(src, sel):
    import re
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", src)
    return m.group(1) if m else ""


def test_the_cockpit_button_is_a_hud_pill_in_both_tones():
    # Stop stopped wearing the panel's red fill (#488); now it is the morphing
    # button's amber tone, the same pill as Send
    src = (UI / "panel.js").read_text()
    send = _cockpit_rule(src, ":host([cockpit]) .composer .btn.send")
    assert "border-radius:999px" in send and "background:transparent" in send and "min-height:48px" in send
    assert "border:1px solid var(--cockpit-accent" in send and "color:var(--cockpit-accent" in send
    amber = _cockpit_rule(src, ':host([cockpit]) .composer .btn.send[data-tone="amber"]')
    assert "color:var(--cockpit-amber" in amber and "border-color:var(--cockpit-amber" in amber


def test_the_field_is_the_cockpits_full_page_background():
    # round 2: the 3D field fills the page behind the whole cockpit and takes no input
    home = (UI / "home.js").read_text()
    dec = (UI / "decisions.js").read_text()
    # the field's host stays where the page put it (full page), not slotted into a small box
    assert "this._host3d.slot" not in home
    assert 'slot name="field"' not in dec and ".fieldbox" not in dec
    # unclickable: its drag / hover / select never fire under the cockpit
    rule = _cockpit_rule(home, ".view-decisions .field")
    assert "pointer-events:none" in rule
    # the cockpit itself is see-through; its panels carry the backing that keeps text legible
    host = _cockpit_rule(dec, ":host")
    assert "background:transparent" in host
    assert "--panel:rgba(" in host
    assert "background:var(--ck-backing" in _cockpit_rule((UI / "panel.js").read_text(), ":host([cockpit]) .scroller")
    # no small-field point cap any more: the page's own adaptive budget
    assert "COCKPIT_FIELD_BUDGET" not in home


@NODE
def test_the_cockpits_one_button_morphs_between_send_and_stop():
    # round 2: one button replaces Queue + Stop in the cockpit
    m = lambda text, busy: _js("P.morphButton(%s, %s)" % (json.dumps(text), json.dumps(busy)), "panel.js", "P")
    assert m("", False) == {"label": "Send", "action": "send", "tone": "cyan"}
    assert m("hello", False) == {"label": "Send", "action": "send", "tone": "cyan"}
    # answering with an empty input: Stop, amber
    assert m("", True) == {"label": "Stop", "action": "stop", "tone": "amber"}
    assert m("  \n ", True) == {"label": "Stop", "action": "stop", "tone": "amber"}
    # answering and the person types: Send, which queues
    assert m("one more thing", True) == {"label": "Send", "action": "queue", "tone": "cyan"}


def test_the_morphing_button_is_wired_in_cockpit_mode_only():
    src = (UI / "panel.js").read_text()
    stop = _cockpit_rule(src, ":host([cockpit]) .composer .btn.stop")
    assert "display:none" in stop                                    # the separate Stop is gone in the cockpit
    amber = _cockpit_rule(src, ':host([cockpit]) .composer .btn.send[data-tone="amber"]')
    assert "--cockpit-line" in amber or "amber" in amber or "#ffb040" in amber
    assert "morphButton(" in src
    # Enter still only sends / queues: the keydown handler calls _submit, never the morph's stop
    assert 'ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); this._submit(); }' in src
