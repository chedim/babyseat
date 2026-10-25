"""THE OUT-OF-TOKENS PAUSE on the page (tests/test_quota_pause.py is the seat).

  * the chat (membrane/ui/panel.js: the normal chat and the cockpit's): a
    message held on a paused model shows a centered popup, "<model> is out
    of tokens · it resumes at HH:MM (in X h Y m)", with Resume now;
  * membrane home's top bar (membrane/ui/home.js): a ⚠ beside the ▶/⏸ while
    any model is paused, its hover naming each paused mind, its model, when it
    resumes, the parked goals, and Resume now; gone when all resume.

Pure helpers, driven by node with TZ=UTC (the clock reads in local time).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

NOW = 1_800_000_000                       # 2027-01-15 08:00:00 UTC
LATER = NOW + 2 * 3600 + 5 * 60           # 10:05 UTC, in 2 h 5 m

STATUS = {"now": NOW, "paused": [
    {"model": "claude-agent:sonnet", "until": LATER, "resumes_at": LATER, "kind": "reset",
     "reason": "You've hit your session limit", "minds": ["Chat", "Goal worker"],
     "chats": [{"sessionID": "ses_a", "messageID": "msg_1", "text": "hello", "title": "a chat"}],
     "goals": [{"eid": "goal:tidy", "name": "tidy the <docs>"}]},
    {"model": "glm:cloud", "until": None, "probe_at": NOW + 59, "resumes_at": NOW + 59, "kind": "probe",
     "reason": "HTTP 429", "minds": ["Idle thinker"], "chats": [], "goals": []},
]}


def _node(file, expr):
    js = "const L=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / file), expr)
    return json.loads(subprocess.check_output(["node", "-e", js], env=dict(os.environ, TZ="UTC")))


@NODE
def test_the_line_says_the_model_the_clock_and_how_long():
    assert _node("panel.js", "L.quotaLine('claude-agent:sonnet', %d, %d)" % (LATER, NOW * 1000)) == \
        "claude-agent:sonnet is out of tokens · it resumes at 10:05 (in 2 h 5 m)"
    assert _node("panel.js", "L.quotaClock(%d, %d)" % (NOW + 59, NOW * 1000)) == \
        {"at": "08:00", "in": "1 m"}
    assert _node("panel.js", "L.quotaClock(%d, %d)" % (NOW - 5, NOW * 1000))["in"] == "any moment"


@NODE
def test_the_chat_finds_its_held_message():
    got = _node("panel.js", "L.quotaForChat(%s, 'ses_a')" % json.dumps(STATUS))
    assert got["model"] == "claude-agent:sonnet"
    assert _node("panel.js", "L.quotaForChat(%s, 'ses_b')" % json.dumps(STATUS)) is None
    assert _node("panel.js", "L.quotaForChat(null, 'ses_a')") is None


@NODE
def test_the_popup_offers_resume_now():
    html = _node("panel.js", "L.quotaPopupHtml(%s, %d)" % (json.dumps(STATUS["paused"][0]), NOW * 1000))
    assert "is out of tokens · it resumes at 10:05 (in 2 h 5 m)" in html
    assert 'data-qresume="claude-agent:sonnet"' in html
    assert "Resume now" in html
    assert 'role="alertdialog"' in html


@NODE
def test_the_warning_is_gone_when_nothing_is_paused():
    v = _node("home.js", "L.quotaBadgeView({paused: []}, %d)" % (NOW * 1000))
    assert v["shown"] is False


@NODE
def test_the_warning_names_each_paused_mind():
    v = _node("home.js", "L.quotaBadgeView(%s, %d)" % (json.dumps(STATUS), NOW * 1000))
    assert v["shown"] is True
    html = v["html"]
    for s in ("Goal worker", "Idle thinker", "Chat", "claude-agent:sonnet", "glm:cloud",
              "resumes at 10:05", "tidy the &lt;docs&gt;", "Resume now", 'data-qresume="glm:cloud"'):
        assert s in html, s
    assert "2 models" in v["label"]


@NODE
def test_the_badge_resumes_a_model_and_reads_again():
    js = """
    const calls = [];
    const el = () => ({ hidden: true, innerHTML: "", textContent: "", title: "", attrs: {},
                        setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {} });
    let st = %s;
    const b = new L.QuotaBadge({ wrap: el(), btn: el(), pop: el(),
      get: async (p) => { calls.push(["get", p]); return st; },
      post: async (p, body) => { calls.push(["post", p, body]); st = { paused: [] }; return st; },
      now: () => %d });
    (async () => {
      await b.load();
      const shown = !b.wrap.hidden;
      await b.resume("glm:cloud");
      process.stdout.write(JSON.stringify({ shown, after: !b.wrap.hidden, calls }));
    })();
    """ % (json.dumps(STATUS), NOW * 1000)
    js = "const L=require(%r);" % str(UI / "home.js") + js
    out = json.loads(subprocess.check_output(["node", "-e", js], env=dict(os.environ, TZ="UTC")))
    assert out["shown"] is True and out["after"] is False
    assert ["post", "/membrane/quota/resume", {"model": "glm:cloud"}] in out["calls"]
    assert out["calls"][0] == ["get", "/membrane/quota"]


@NODE
def test_the_stats_window_lists_the_paused_models():
    html = _node("home.js", "L.statsRows({paused: %s})" % json.dumps(STATUS["paused"]))
    assert "Out of tokens" in html and "claude-agent:sonnet" in html and "resumes at 10:05" in html
    assert "Out of tokens" not in _node("home.js", "L.statsRows({})")
