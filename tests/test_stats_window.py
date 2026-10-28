"""The 📊 stats window: the seat's tokenomics, read-only.

One serve-port read, GET /membrane/stats (the page calls
/ui/serve/membrane/stats; a route only on the proxy 404s from the page),
with the same read access as /membrane/options. It answers:

  * the Claude rung since boot -- answers, steps, and per answer / first
    step / later step: cache read, cache write, uncached input, output and
    the effective input (0.1 x read + 1.25 x write + uncached), from
    claude_agent.usage_stats (the ledger /membrane/diag/claude-agent reads);
  * today (since local midnight) and the last hour, by mind (the chat, the
    Goal worker, the Idle thinker, other background calls) and by model:
    the foreground from the serve messages' info.tokens (a backward scan of
    each session's tail, stopping at the first row older than the window --
    never the whole history), the background from bg-usage.json's 10-minute
    slots (tallied on write, kept ~a day).
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import bgusage  # noqa: E402
from membrane import claude_agent as CA  # noqa: E402
from membrane import tokenomics as TK  # noqa: E402
from tests.test_nerve_pause_348 import _Env, _seat, _serve  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

NOW = 1_800_000_000.0
MIDNIGHT = NOW - 5 * 3600


def _row(role, created_s, model="qwen3:8b", agent="build", inp=0, out=0, read=0, write=0):
    info = {"id": f"m{created_s}{role}", "role": role, "modelID": model, "agent": agent,
            "time": {"created": int(created_s * 1000)}}
    if role == "assistant":
        info["tokens"] = {"total": inp + out, "input": inp, "output": out, "reasoning": 0,
                          "cache": {"read": read, "write": write}}
    return {"info": info, "parts": []}


# ---- the foreground scan ----------------------------------------------------------

def test_the_foreground_splits_today_and_the_hour_by_mind_and_model():
    msgs = {
        "ses_chat": [
            _row("assistant", MIDNIGHT - 600, inp=999, out=999),          # yesterday: not counted
            _row("user", NOW - 7200),
            _row("assistant", NOW - 7200, inp=100, out=10),               # today, not the last hour
            _row("assistant", NOW - 60, model="claude-agent:sonnet", inp=50, out=5, read=40, write=8),
        ],
        "ses_loop": [
            _row("assistant", NOW - 120, agent="watch: goal-1", inp=300, out=30),
        ],
    }
    fg = TK.foreground(msgs, MIDNIGHT, NOW - 3600)
    today, hour = fg["today"], fg["hour"]
    assert today["minds"]["chat"] == {"calls": 2, "input": 150, "output": 15}
    assert today["minds"]["loops"] == {"calls": 1, "input": 300, "output": 30}
    assert today["models"]["qwen3:8b"] == {"calls": 2, "input": 400, "output": 40}
    assert today["models"]["claude-agent:sonnet"] == {"calls": 1, "input": 50, "output": 5}
    assert hour["minds"]["chat"] == {"calls": 1, "input": 50, "output": 5}
    assert hour["minds"]["loops"]["calls"] == 1
    assert "qwen3:8b" in hour["models"] and hour["models"]["qwen3:8b"]["input"] == 300


def test_the_foreground_scan_stops_at_the_first_old_row():
    # the scan walks each session BACKWARD and stops at the first row older than
    # the window: rows before it are never read (a row there that would crash
    # the tally proves it is not touched)
    class Boom(dict):
        def get(self, *a, **k):
            raise AssertionError("an old row was read")
    rows = [{"info": Boom(), "parts": []}, _row("assistant", MIDNIGHT - 1, inp=7, out=7),
            _row("assistant", NOW - 10, inp=3, out=1)]
    fg = TK.foreground({"s": rows}, MIDNIGHT, NOW - 3600)
    assert fg["today"]["minds"]["chat"] == {"calls": 1, "input": 3, "output": 1}


def test_rows_without_tokens_are_not_calls():
    rows = [_row("assistant", NOW - 10, model="membrane")]           # a minted row, zeros
    fg = TK.foreground({"s": rows}, MIDNIGHT, NOW - 3600)
    assert fg["today"]["minds"] == {} and fg["today"]["models"] == {}


# ---- the background slots ------------------------------------------------------------

def test_background_calls_land_in_ten_minute_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    t = [NOW - 2 * 3600]
    monkeypatch.setattr(bgusage.time, "time", lambda: t[0])
    bgusage.record("thinker", "qwen3:8b", input=100, output=10)
    t[0] = NOW - 300
    bgusage.record("thinker", "qwen3:8b", input=20, output=2)
    bgusage.record("watch", "qwen3:8b", input=5, output=1)
    bgusage.record("claim-judge", "qwen3:1.7b", input=7, output=1)
    bgusage.record("delegate", "qwen3:1.7b", input=3, output=1)
    doc = bgusage.load(bgusage.path())
    assert doc["lanes"]["thinker"]["qwen3:8b"]["calls"] == 2         # the all-time tally stays
    assert all(int(k) % bgusage.SLOT_S == 0 for k in doc["slots"])
    hour = TK.background(doc, NOW - 3600)
    # the Idle thinker is the thinker and its claim judges; the Goal worker's
    # proxy-only degrade is the watch lane; the rest is other background work
    assert hour["minds"]["cortex"] == {"calls": 2, "input": 27, "output": 3}
    assert hour["minds"]["loops"] == {"calls": 1, "input": 5, "output": 1}
    assert hour["minds"]["other"] == {"calls": 1, "input": 3, "output": 1}
    today = TK.background(doc, MIDNIGHT)
    assert today["minds"]["cortex"]["calls"] == 3
    assert today["models"]["qwen3:8b"] == {"calls": 3, "input": 125, "output": 13}


def test_old_slots_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    t = [NOW - 3 * 86400]
    monkeypatch.setattr(bgusage.time, "time", lambda: t[0])
    bgusage.record("thinker", "m", input=1)
    t[0] = NOW
    bgusage.record("thinker", "m", input=1)
    doc = bgusage.load(bgusage.path())
    assert len(doc["slots"]) == 1


# ---- the whole payload ---------------------------------------------------------------

def _claude():
    return {"calls": 2, "input": 200, "cache_read": 10000, "cache_write": 800, "output": 300,
            "per_answer": {"input": 100, "cache_read": 5000, "cache_write": 400, "output": 150},
            "steps": {"steps": 6,
                      "per_first_step": {"input": 60, "cache_read": 1000, "cache_write": 300, "output": 20},
                      "per_later_step": {"input": 10, "cache_read": 2000, "cache_write": 50, "output": 30}}}


def test_the_payload_joins_the_minds_and_says_the_claude_rung_effective_input():
    msgs = {"s": [_row("assistant", NOW - 10, inp=100, out=10)]}
    bg = {"lanes": {}, "slots": {str(int(NOW // 600 * 600)): {"thinker": {"q": {"calls": 1, "input": 4, "output": 2, "reasoning": 0}}}}}
    out = TK.build(msgs, bg, _claude(), NOW, MIDNIGHT)
    assert out["today"]["total"] == {"calls": 2, "input": 104, "output": 12}
    assert out["hour"]["minds"]["cortex"]["calls"] == 1
    c = out["claude"]
    assert (c["answers"], c["steps"]) == (2, 6)
    # 0.1 x read + 1.25 x write + uncached
    assert c["per_answer"]["effective"] == round(0.1 * 5000 + 1.25 * 400 + 100)
    assert c["first_step"]["effective"] == round(0.1 * 1000 + 1.25 * 300 + 60)
    assert c["later_step"]["effective"] == round(0.1 * 2000 + 1.25 * 50 + 10)
    assert c["total"]["effective"] == round(0.1 * 10000 + 1.25 * 800 + 200)
    assert len(json.dumps(out)) < 4000                                  # small


# ---- the serve route -------------------------------------------------------------------

class TestTheStatsRoute(_Env):
    def setUp(self):
        super().setUp()
        self.srv, self.r = _seat(self.tmp.name)
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
        super().tearDown()

    def _go(self, path, method="GET", offbox=False):
        if self.httpd is None:
            self.httpd = _serve(self.srv, self.r, offbox)
        code, b, _ = _req(self.httpd.server_address[1], path, method, None, None)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_the_serve_port_answers_the_window(self):
        now = time.time()
        self.r.messages.msgs["ses_x"] = [_row("assistant", now - 30, inp=120, out=12)]
        bgusage.record("thinker", "qwen3:8b", input=9, output=3)
        code, out = self._go("/membrane/stats")
        self.assertEqual(code, 200, out)
        self.assertEqual(out["today"]["minds"]["chat"], {"calls": 1, "input": 120, "output": 12})
        self.assertEqual(out["hour"]["minds"]["cortex"]["calls"], 1)
        self.assertIn("claude", out)
        self.assertEqual(out["claude"]["answers"], CA.usage_stats()["calls"])
        self.assertLessEqual(out["midnight"], now)

    def test_it_is_read_only(self):
        code, _ = self._go("/membrane/stats", method="POST")
        self.assertNotEqual(code, 200)

    def test_off_box_it_reads_like_the_options(self):
        code_s, _ = self._go("/membrane/stats", offbox=True)
        code_o, _ = _req(self.httpd.server_address[1], "/membrane/options", "GET", None, None)[:2]
        self.assertEqual(code_s, code_o)


# ---- the window (node) ------------------------------------------------------------------

STATS = {"now": NOW, "midnight": MIDNIGHT,
         "today": {"total": {"calls": 12, "input": 123456, "output": 7890},
                   "minds": {"chat": {"calls": 3, "input": 3000, "output": 300},
                             "loops": {"calls": 8, "input": 120000, "output": 7000},
                             "cortex": {"calls": 1, "input": 456, "output": 590}},
                   "models": {"qwen3:8b": {"calls": 10, "input": 100000, "output": 7000},
                              "claude-agent:sonnet": {"calls": 2, "input": 23456, "output": 890}}},
         "hour": {"total": {"calls": 6, "input": 60000, "output": 3000},
                  "minds": {"loops": {"calls": 6, "input": 60000, "output": 3000}},
                  "models": {"qwen3:8b": {"calls": 6, "input": 60000, "output": 3000}}},
         "claude": {"answers": 2, "steps": 6,
                    "total": {"input": 200, "cache_read": 10000, "cache_write": 800, "output": 300, "effective": 2200},
                    "per_answer": {"input": 100, "cache_read": 5000, "cache_write": 400, "output": 150, "effective": 1100},
                    "first_step": {"input": 60, "cache_read": 1000, "cache_write": 300, "output": 20, "effective": 535},
                    "later_step": {"input": 10, "cache_read": 2000, "cache_write": 50, "output": 30, "effective": 273}}}


def _node(expr):
    js = "const H=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "home.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_token_counts_read_short():
    assert _node("[H.tokShort(0), H.tokShort(999), H.tokShort(1234), H.tokShort(123456), H.tokShort(2500000)]") == \
        ["0", "999", "1.2k", "123k", "2.5M"]


@NODE
def test_the_rows_name_the_minds_the_models_and_the_formula():
    html = _node("H.statsRows(%s)" % json.dumps(STATS))
    for s in ("Today", "last hour", "Chat", "Goal worker", "Idle thinker", "qwen3:8b",
              "claude-agent:sonnet", "Claude rung", "per answer", "first step", "later steps",
              "0.1 × read + 1.25 × write + uncached", "123k"):
        assert s in html, s
    # the busiest mind of the last hour stands out
    assert 'class="hot"' in html


@NODE
def test_no_claude_answers_says_so():
    s = dict(STATS, claude=dict(STATS["claude"], answers=0, steps=0))
    assert "no Claude answers since the seat started" in _node("H.statsRows(%s)" % json.dumps(s))


HARNESS = r"""
const H = require(%(home)r);
function el() {
  const attrs = {}, cls = new Set();
  return { hidden: true, textContent: "", innerHTML: "",
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    classList: { toggle(c, on) { on ? cls.add(c) : cls.delete(c); }, contains(c) { return cls.has(c); } },
    focus() {} };
}
const gets = [], timers = {}; let tid = 0;
const btn = el(), panel = el(), body = el(), msg = el();
const w = new H.StatsWindow({ btn, panel, body, msg, get: async (p) => { gets.push(p); return %(reply)s; },
  every: (fn, ms) => { timers[++tid] = ms; return tid; }, stop: (id) => { delete timers[id]; } });
(async () => {
  await w.toggle();
  const opened = { open: w.open, gets: gets.slice(), timers: Object.values(timers), body: body.innerHTML.length > 0,
                   expanded: btn.getAttribute("aria-expanded") };
  w.key({ key: "Escape" });
  process.stdout.write(JSON.stringify({ opened, closed: { open: w.open, timers: Object.keys(timers).length } }));
})().catch((e) => process.stdout.write(JSON.stringify({ crash: String(e && e.stack || e) })));
"""


@NODE
def test_the_window_loads_on_open_refreshes_while_open_and_escape_closes():
    out = json.loads(subprocess.check_output(["node", "-e", HARNESS % {
        "home": str(UI / "home.js"), "reply": json.dumps(STATS)}]))
    assert "crash" not in out, out
    o = out["opened"]
    assert o["open"] and o["gets"] == ["/membrane/stats"] and o["body"] and o["expanded"] == "true"
    assert o["timers"] == [10000]
    assert out["closed"] == {"open": False, "timers": 0}


@NODE
def test_a_lone_row_is_not_marked_hot():
    one = {"chat": {"calls": 1, "input": 5, "output": 1}}
    assert 'class="hot"' not in _node('H.statsTable("mind", [["chat", "Chat"], ["loops", "Goal worker"]], %s, %s)'
                                      % (json.dumps(one), json.dumps(one)))

# ---- a loop turn counts once (561) ----

def test_a_serve_loop_turn_counts_once_not_in_both_the_rows_and_the_watch_tally(tmp_path, monkeypatch):
    """A serve loop turn is a stored row AND a from_turn tally; the window takes it from the row only. The proxy-only degrade (lane watch, no row) still comes from the tally."""
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bgusage.time, "time", lambda: NOW - 60)
    bgusage.from_turn("watch-turn", {"info": {"modelID": "m1", "tokens": {"input": 300, "output": 30}}})
    bgusage.record("watch", "m1", input=5, output=1)
    msgs = {"ses_loop": [_row("assistant", NOW - 60, model="m1", agent="watch: goal-1", inp=300, out=30)]}
    out = TK._assemble(TK.foreground(msgs, MIDNIGHT, NOW - 3600), bgusage.load(bgusage.path()), None, NOW, MIDNIGHT)
    assert out["hour"]["minds"]["loops"] == {"calls": 2, "input": 305, "output": 31}
    assert out["hour"]["total"] == {"calls": 2, "input": 305, "output": 31}
    assert "other" not in out["hour"]["minds"]
