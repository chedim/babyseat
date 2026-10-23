"""The ⚙ window's "Background work" section (operator, 2026-10-23).

The minds' model pickers moved out of the chat's 🧠 strip ("Minds ->
background loops", badly named and placed) into the ⚙ options window: two
rows, "Goal worker" (the loops' model) and "Idle thinker" (the cortex's),
each with a credentials choice shown only when its model is a claude-agent
rung ("Same as the chat" by default, "API key", "CLI login"). Each change
posts one key to /membrane/options (the seat routes it to minds_models).
Driven in node with stubs, like tests/test_options_window_344.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

BG = {"loops": "qwen3:8b", "cortex": "qwen3:8b", "loops_auth": "chat", "cortex_auth": "chat",
      "models": ["qwen3:8b", "qwen3:14b", "claude-agent:sonnet"], "claude": ["claude-agent:sonnet"],
      "chat_auth": "login"}
OPTS = {"self_cpu_share": 0.5, "defaults": {"self_cpu_share": 0.5}, "current_tau_s": 180.0,
        "current_tau_note": "changing it needs the operator's approval", "energy": None,
        "writable": ["self_cpu_share", "loops", "cortex", "loops_auth", "cortex_auth"], "background": BG}

HARNESS = r"""
const H = require(%(home)r);
function el() {
  const attrs = {}, cls = new Set();
  return { hidden: true, textContent: "", innerHTML: "", value: "",
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    classList: { toggle(c, on) { on ? cls.add(c) : cls.delete(c); }, contains(c) { return cls.has(c); } },
    focus() {} };
}
const calls = [];
let reply = %(reply)s, refuse = %(refuse)s;
const seat = {
  async get(p) { calls.push(["GET", p]); return JSON.parse(JSON.stringify(reply)); },
  async post(p, body) {
    calls.push(["POST", p, body]);
    if (refuse) throw new Error(refuse);
    reply = JSON.parse(JSON.stringify(reply));
    Object.assign(reply.background, body);
    return JSON.parse(JSON.stringify(reply));
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


def _rows(bg):
    js = "const H=require(%r); process.stdout.write(JSON.stringify(H.backgroundRows(%s)))" % (
        str(UI / "home.js"), json.dumps(bg))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_the_section_has_the_two_minds_with_their_hints():
    html = _rows(BG)
    assert "Background work" in html
    assert "Goal worker" in html and "the model that works your open goals" in html
    assert "Idle thinker" in html and "thinks ahead between your messages" in html
    assert 'data-opt="loops"' in html and 'data-opt="cortex"' in html
    assert html.count('value="claude-agent:sonnet"') == 2
    assert "background loops" not in html


@NODE
def test_credentials_show_only_for_a_claude_model():
    html = _rows(BG)
    assert 'data-opt="loops_auth"' not in html and 'data-opt="cortex_auth"' not in html
    html = _rows(dict(BG, loops="claude-agent:sonnet"))
    assert 'data-opt="loops_auth"' in html and 'data-opt="cortex_auth"' not in html
    assert "Same as the chat" in html and "API key" in html and "CLI login" in html
    assert "Same as the chat (CLI login)" in html          # names what the chat uses now
    assert 'value="chat" selected' in html                 # the default


@NODE
def test_no_seat_no_section():
    assert _rows(None) == ""


@NODE
def test_the_window_paints_the_section():
    out = _run("await w.toggle(); return { html: body.innerHTML };")
    assert "Goal worker" in out["html"] and 'type="range"' in out["html"]   # the share stays


@NODE
def test_a_pick_posts_one_key_and_repaints():
    out = _run("""
      await w.toggle();
      const saved = await w.setOption("loops", "claude-agent:sonnet");
      const saved2 = await w.setOption("loops_auth", "api-key");
      return { calls, saved: !!saved, html: body.innerHTML, msg: msg.textContent };""")
    posts = [c for c in out["calls"] if c[0] == "POST"]
    assert posts == [["POST", "/membrane/options", {"loops": "claude-agent:sonnet"}],
                     ["POST", "/membrane/options", {"loops_auth": "api-key"}]]
    assert out["saved"] is True
    assert 'data-opt="loops_auth"' in out["html"] and 'value="api-key" selected' in out["html"]
    assert "saved" in out["msg"]


@NODE
def test_a_refusal_is_shown():
    out = _run("""
      await w.toggle();
      const saved = await w.setOption("loops", "nope:1b");
      return { saved, msg: msg.textContent, html: body.innerHTML };""", refuse="loops: membrane doesn't see 'nope:1b'")
    assert out["saved"] is None
    assert "nope:1b" in out["msg"]
    assert 'value="qwen3:8b" selected' in out["html"]


def test_the_chats_minds_strip_points_to_the_gear():
    src = (UI / "panel.js").read_text()
    assert "data-mind=" not in src                         # the old pickers are gone
    assert "Background work" in src                        # a pointer to ⚙ instead
