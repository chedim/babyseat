"""Reply feedback (G15 #1029 / G9 #1032, docs/ui-pack-design.md): the chat footer's
👍/👎/retry and the Decision card's per-option impact line."""
import pathlib
import subprocess
import shutil

import pytest

from membrane import reply_feedback as RF

UI = pathlib.Path(__file__).resolve().parent.parent / "membrane" / "ui"


class FakeStore:
    def __init__(self):
        self.docs = {}

    def add(self, cls, name, summary="", links=None):
        d = self.docs.setdefault(name, {"class": cls, "props": {}})
        d["summary"] = summary
        return {"ok": True, "eid": "membrane::Feedback:" + name}

    def edit(self, spec, properties=None, **kw):
        name = spec.split("Feedback:", 1)[-1]
        self.docs[name]["props"].update(properties or {})
        return {"ok": True}


def test_up_lands_as_a_feedback_entity():
    est = FakeStore()
    res = RF.record(est, "msg_1", "up", session="ses_1")
    assert res["ok"] and res["kind"] == "up"
    d = est.docs["feedback:msg_1"]
    assert d["class"] == "Feedback" and d["props"]["kind"] == "up" and d["props"]["session"] == "ses_1"
    assert "helpful" in d["summary"]


def test_down_keeps_the_why_and_a_second_press_replaces():
    est = FakeStore()
    RF.record(est, "msg_2", "up")
    RF.record(est, "msg_2", "down", why="  too long  ")
    d = est.docs["feedback:msg_2"]
    assert d["props"]["kind"] == "down" and d["props"]["why"] == "too long"
    assert d["summary"].endswith("not helpful: too long")


@pytest.mark.parametrize("mid,kind", [("", "up"), ("m", "meh"), ("x" * 201, "up")])
def test_bad_input_is_refused(mid, kind):
    assert RF.record(FakeStore(), mid, kind)["ok"] is False


def test_no_store_is_an_error_not_a_crash():
    assert RF.record(None, "m", "up") == {"ok": False, "error": "entity layer unavailable"}


def test_the_ui_draws_the_footer_and_the_impact():
    panel = (UI / "panel.js").read_text()
    assert "_fbHtml(info.id)" in panel and 'data-fb="' in panel and "/membrane/feedback" in panel
    dec = (UI / "decisions.js").read_text()
    assert "impactHtml(card, o)" in dec and "impact not stated" in dec


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_impact_lines_render():
    src = (UI / "decisions.js").read_text()
    start = src.index("function impactOf(")
    end = src.index("/** How an open card's one variable is entered")
    js = ("const esc = (s) => String(s).replace(/[&<>\"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
          + src[start:end] +
          "console.log(impactHtml({var:{impact:{a:'restarts <the> seat'}}}, 'a'));"
          "console.log(impactHtml({var:{}}, 'b'));")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == '<span class="imp">→ restarts &lt;the&gt; seat</span>'
    assert out[1] == '<span class="imp none">impact not stated</span>'
