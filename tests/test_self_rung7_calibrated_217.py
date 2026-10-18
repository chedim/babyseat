"""#217: rung 7's model predictions were overconfident and mostly unpaired.

Live (2026-10-18, after #216): 7 pushes asked the model; it answered
0.976 / 0.99 / 0.85 / 0.85 "will have spoken" against reply checks that went
0, 0, 1, 1, 0, 1, 0. The prompt told it nothing about how often the other
actually answers by the reply check -- and its history listed pre-#216 looks
made 300-1600 s after a push ("they had spoken" x14), so it read "they always
answer". Three answers never paired: they were off-task prose ("Membrane is a
tool ...", no number), landed 28-75 s after the push -- none was late for the
120 s check, but a slow escalated turn can be, so the reply check now waits a
bounded grace for an answer still in flight.

Now:
- the prompt gives the BASE RATE (reply checks the other answered, with a
  Jeffreys prior so the first push is 0.5, not 0/0) and the recent reply
  checks only (same horizon), and asks for ONE calibrated probability, saying
  a confident miss is penalized;
- the answer is parsed robustly (a decimal, a percent, after VERDICT) and
  prose with stray numbers (#161, 120 s) is not a probability;
- the reply check waits up to RUNG7_CHECK_S + RUNG7_GRACE_S while the asked
  question has no answer yet, and pairs only an answer that landed after the
  push and before the look;
- status() carries rung 7's diagnostics: pairs per asked push, the model's
  Brier against habit's.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
RESP = MEMBRANE_NS + "Component:chat:in:operator"
UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"

# THE LIVE RECORD (copied, 2026-10-18; no live file is read here): the reply
# checks after the 7 asked pushes -- outcome (1 = they had spoken), habit's p
# on that look, and the model's p_llm where one paired (None: unparseable).
LIVE = [(0, 0.0926, None), (0, 0.1306, None), (1, 0.1306, None), (1, 0.1868, 0.976),
        (0, 0.22, 0.99), (1, 0.2202, 0.85), (0, 0.2198, 0.85)]
# the off-task answers the live model gave (first line, as llm.answered keeps it)
OFF_TASK = ["Membrane is a tool designed to assist AI systems in maintaining a consistent and up-to-date "
            "understanding of their environment.",
            "The user's interactions are designed to mirror the actions of a living entity (#161) within the field.",
            "Good day! I am Memo, a friendly AI companion."]


class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=DIM)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    WA._getter_paused.clear()
    monkeypatch.setattr(LLM, "_calls", [])
    monkeypatch.setattr(LLM, "_echo", {})


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_Field())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    CIO.entity_in(srv, CIO.OPERATOR)
    SL.bind(srv)
    SL.set_limit(srv, 1)
    for r in (1, 2, 3, 4, 5, 6):
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    SL.set_limit(srv, 7)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _land(srv, ts, text):
    doc = srv.entity_store.docs[RESP]
    pp = doc.properties or {}
    pp["llm_answer"] = {"ts": ts, "text": text, "caller": "self"}
    doc.properties = pp


def _base_rate(prompt):
    m = re.search(r"base rate (\d\.\d+)", prompt)
    assert m, prompt
    return float(m.group(1))


def _history(srv, outcomes, t0=10_000.0, other_looks=()):
    """Write pushes + their reply checks into the record (as the loop does)."""
    t = t0
    for d, ans in other_looks:                  # pre-#216: a look long after a push, not a reply check
        SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP})
        SL._append(srv, {"ts": t + d, "kind": "reach", "n": 2, "eid": RESP, "p": 0.02, "answer": ans})
        t += d + 10
    for o in outcomes:
        SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})
        SL._append(srv, {"ts": t + 120, "kind": "reach", "n": 2, "eid": RESP, "p": 0.2, "reply": True,
                         "answer": "changed" if o else "unchanged"})
        t += 300
    return t


# ---- 1. calibration: the prompt carries the base rate ---------------------------

def test_the_prompt_gives_the_base_rate_with_a_prior():
    srv = _seat()
    p0 = SL._rung7_prompt(srv, RESP, 1, 5000.0)
    assert _base_rate(p0) == pytest.approx(0.5, abs=0.006)          # no checks yet: 0.5, not 0/0
    now = _history(srv, [0, 0, 1, 1, 0, 1, 0])
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert _base_rate(p) == pytest.approx((3 + 0.5) / (7 + 1), abs=0.006)
    assert "3 of 7" in p
    assert "calibrated" in p.lower() and "probability" in p
    assert "penal" in p.lower()                                     # a confident miss costs


def test_the_prompt_reads_reply_checks_only_not_late_looks():
    """The live prompt listed pre-#216 looks 300-1600 s after a push ('they had
    spoken' x14): a different horizon, and the reason it said 0.98."""
    srv = _seat()
    now = _history(srv, [0, 0], other_looks=[(1180, "changed"), (879, "changed"), (578, "changed")])
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert "1180" not in p and "879" not in p
    assert _base_rate(p) == pytest.approx(0.5 / 3, abs=0.006)        # 0 of 2 reply checks


def test_the_prompt_carries_no_chat_text():
    srv = _seat()
    now = _history(srv, [1, 0])
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert "self is here" not in p


def test_a_base_rate_echoing_model_answers_the_base_rate_at_the_look(monkeypatch):
    """The prompt, end to end: a fake model that echoes the base rate it was
    told lands that number, and the reply check pairs it."""
    srv = _seat()
    _history(srv, [1, 0, 0, 0], t0=100.0)
    asked = []

    def model(srv_, eid, prompt):
        asked.append(prompt)
        _land(srv_, clock["t"] + 5.0, f"VERDICT: {_base_rate(prompt):.3f}")
        return {"rc": 0, "text": f"queued: a loop takes it up on {eid}"}

    clock = {"t": 0.0}
    monkeypatch.setattr(SL, "_ask_llm", model)
    monkeypatch.setattr(SL, "_push_chat", lambda *a, **k: None)
    monkeypatch.setattr(SL, "_read_entity", lambda s, e, now: {"fired": True, "state": {"v": now}, "changed": False})
    t0 = 5000.0
    for i in range(30):
        clock["t"] = t0 + i * SL.PACE_S
        SL.cycle(srv, now=clock["t"])
    assert asked
    looks = [r for r in _record(srv) if r.get("kind") == "reach" and r.get("eid") == RESP and r["ts"] > t0]
    assert looks and looks[0].get("reply") is True
    assert looks[0]["p_llm"] == pytest.approx((1 + 0.5) / (4 + 1), abs=0.001)


# ---- 1b. the answer, parsed robustly ---------------------------------------------

@pytest.mark.parametrize("text,p", [
    ("0.900", 0.9),
    ("VERDICT: 0.64 (Based on a histogram: spoken 8 out of 14 times after 120 seconds, about 0.57 or 57%.)", 0.64),
    ("VERDICT -- 85%", 0.85),
    ("VERDICT: 35 percent", 0.35),
    ("VERDICT: .3", 0.3),
    ("VERDICT: 1", 1.0),
    ("VERDICT: 0", 0.0),
    ("Probability: 0.25", 0.25),
])
def test_a_probability_is_read_as_a_number_or_a_percent(text, p):
    assert SL._parse_p(text) == pytest.approx(p)


@pytest.mark.parametrize("text", OFF_TASK + [
    "VERDICT: they will speak within 120s",          # 120 is no probability
    "VERDICT -- unclear",
    "",
])
def test_prose_with_stray_numbers_is_not_a_probability(text):
    assert SL._parse_p(text) is None


# ---- 2. lateness: a bounded wait for an answer in flight -------------------------

def _late_world(monkeypatch, lag):
    """Self asks at the push; the answer lands `lag` seconds later."""
    clock = {"t": 0.0}
    pushes = []

    def model(srv_, eid, prompt):
        pushes.append(clock["t"])
        _land(srv_, clock["t"] + lag, "VERDICT: 0.3")
        return {"rc": 0, "text": f"queued: a loop takes it up on {eid}"}

    monkeypatch.setattr(SL, "_ask_llm", model)
    monkeypatch.setattr(SL, "_push_chat", lambda *a, **k: None)
    monkeypatch.setattr(SL, "_read_entity", lambda s, e, now: {"fired": True, "state": {"v": now}, "changed": False})
    srv = _seat()
    t0 = 5000.0
    for i in range(SL.MEET_EVERY - 1):                 # one push, its reply check, before the next push
        clock["t"] = t0 + i * SL.PACE_S
        SL.cycle(srv, now=clock["t"])
    looks = [r for r in _record(srv) if r.get("kind") == "reach" and r.get("eid") == RESP and r["ts"] > t0]
    return pushes, looks


def test_an_early_answer_is_checked_at_the_usual_time(monkeypatch):
    pushes, looks = _late_world(monkeypatch, lag=10.0)
    assert len(pushes) == 1 and looks
    assert looks[0]["ts"] - pushes[0] < SL.RUNG7_CHECK_S + 2 * SL.PACE_S    # no extra wait
    assert looks[0].get("p_llm") == pytest.approx(0.3)


def test_an_answer_landing_within_the_grace_is_paired(monkeypatch):
    lag = SL.RUNG7_CHECK_S + SL.RUNG7_GRACE_S / 2
    pushes, looks = _late_world(monkeypatch, lag=lag)
    assert looks and looks[0].get("reply") is True
    assert looks[0]["ts"] - pushes[0] >= lag                    # it waited for the answer...
    assert looks[0]["ts"] - pushes[0] <= SL.RUNG7_CHECK_S + SL.RUNG7_GRACE_S + 2 * SL.PACE_S
    assert looks[0].get("p_llm") == pytest.approx(0.3)          # ...and paired it


def test_an_answer_landing_after_the_grace_is_not_paired(monkeypatch):
    lag = SL.RUNG7_CHECK_S + SL.RUNG7_GRACE_S + 60.0
    pushes, looks = _late_world(monkeypatch, lag=lag)
    assert looks and looks[0].get("reply") is True
    assert looks[0]["ts"] - pushes[0] <= SL.RUNG7_CHECK_S + SL.RUNG7_GRACE_S + 2 * SL.PACE_S   # bounded
    assert looks[0].get("p_llm") is None                       # it hadn't landed by the look
    assert not [r for r in looks if r.get("p_llm") is not None]  # and no later look pairs it


def test_the_grace_stays_under_the_stale_bound():
    assert SL.RUNG7_GRACE_S > 0
    assert SL.RUNG7_CHECK_S + SL.RUNG7_GRACE_S < SL.RUNG7_STALE_S


# ---- 3. fit to the live data -----------------------------------------------------

def test_a_base_rate_model_beats_an_always_098_one_on_the_live_outcomes():
    """Replay the live reply checks: the base-rate model answers what the prompt
    told it at each push; the overconfident one always 0.98."""
    def brier(model):
        srv = _seat()
        t = 10_000.0
        for o, habit, _ in LIVE:
            SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})
            p = model(SL._rung7_prompt(srv, RESP, 1, t))
            SL._append(srv, {"ts": t + 120, "kind": "reach", "n": 2, "eid": RESP, "p": habit, "reply": True,
                             "answer": "changed" if o else "unchanged", "p_llm": p})
            t += 300
        return SL.status(srv)["rung7"]["brier_model"], SL.status(srv)["rung7"]["brier_habit"]

    b_rate, b_habit = brier(_base_rate)
    b_conf, _ = brier(lambda prompt: 0.98)
    assert b_rate < b_conf
    assert b_conf - b_rate > 0.15                     # not a near-tie
    assert b_habit < b_conf                           # the live complaint: habit beat the confident model


# ---- 4. status: the operator sees rung 7 ------------------------------------------

def test_status_reports_pairs_per_asked_push_and_both_briers():
    srv = _seat()
    t = 10_000.0
    for o, habit, p_llm in LIVE:
        SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})
        rec = {"ts": t + 120, "kind": "reach", "n": 2, "eid": RESP, "p": habit, "reply": True,
               "answer": "changed" if o else "unchanged"}
        if p_llm is not None:
            rec["p_llm"] = p_llm
        SL._append(srv, rec)
        t += 300
    SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})  # in flight
    r7 = SL.status(srv)["rung7"]
    assert r7["asked"] == 8 and r7["checked"] == 7 and r7["paired"] == 4
    assert r7["pair_rate"] == pytest.approx(4 / 7, abs=1e-3)
    pairs = [x for x in LIVE if x[2] is not None]
    bh = sum((o - h) ** 2 for o, h, _ in pairs) / len(pairs)
    bm = sum((o - m) ** 2 for o, _, m in pairs) / len(pairs)
    assert r7["brier_habit"] == pytest.approx(bh, abs=1e-3)
    assert r7["brier_model"] == pytest.approx(bm, abs=1e-3)
    assert r7["base_rate"] == pytest.approx(3.5 / 8, abs=1e-3)


def test_status_without_asks_is_quiet():
    srv = _seat()
    r7 = SL.status(srv)["rung7"]
    assert r7["asked"] == 0 and r7["paired"] == 0
    assert r7["brier_model"] is None and r7["brier_habit"] is None


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_panel_title_shows_rung_seven():
    st = {"self": {"mode": "on", "limit": 7, "rung": 6, "bar": 0.1, "next": 7,
                   "ladder": [{"rung": 7, "name": "understand an other", "state": "current"}],
                   "rung7": {"asked": 8, "checked": 7, "paired": 4, "pair_rate": 0.5714,
                             "brier_model": 0.4366, "brier_habit": 0.3253, "base_rate": 0.4375}}}
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(L.selfView(%s)))'
          % (str(UI / "panel.js"), json.dumps(st)))
    v = json.loads(subprocess.check_output(["node", "-e", js]))
    assert "4 of 7" in v["title"] and "0.44" in v["title"] and "0.33" in v["title"]
