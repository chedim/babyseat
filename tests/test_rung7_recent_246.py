"""#246: rung 7's question gets the other's RECENT reply rate next to the
all-time one.

Live (2026-10-19): rung 7 had its 20 pairs and waited on skill, -0.34 -- the
model leaned "they'll answer" while its question carried only the all-time
base rate. Now the prompt also carries the share of the last
RUNG7_RECENT reply checks answered (the same small Jeffreys prior, so 0 of 0
is 0.5), the time since the other last spoke, both labelled `recent` and
`all-time`, and a sentence that rhythms change. Only the INPUT changed: the
test (RUNG7_MARGIN, RUNG7_LOOKS), the pairing, the caps and the parsing did
not. status()['rung7'] carries `recent_rate` for the panel.

The replay (experiments/rung7_recent/) is honest about what this buys on
the live record: see test_the_replays_verdict_is_pinned.
"""
import json
import os
import re
import sys
import tempfile
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

# THE LIVE SEQUENCE (copied from the record, 2026-10-18; no live file is read
# here): the other's 77 reply checks, oldest first (1 = they had spoken by the
# check), and habit's p on each of the last 20.
LIVE = "10011010101101111100011011111010110100001001100011110010100010110010111010111"
HABIT_LAST20 = [0.4094, 0.4956, 0.478, 0.4486, 0.4144, 0.4204, 0.3726, 0.473, 0.3514, 0.1888,
                0.2706, 0.2214, 0.2278, 0.2691, 0.261, 0.2792, 0.2604, 0.2666, 0.2853, 0.298]


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


def _history(srv, outcomes, t0=10_000.0, habit=None):
    t = t0
    for i, o in enumerate(outcomes):
        SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})
        SL._append(srv, {"ts": t + 120, "kind": "reach", "n": 2, "eid": RESP,
                         "p": habit[i] if habit else 0.2, "reply": True,
                         "answer": "changed" if o else "unchanged"})
        t += 300
    return t


def _rate(prompt, label):
    m = re.search(label + r"(?: base)? rate (\d\.\d+)", prompt)
    assert m, prompt
    return float(m.group(1))


# ---- 1. the prompt carries both rates, labelled -----------------------------------

def test_the_prompt_labels_recent_and_all_time_rates():
    srv = _seat()
    now = _history(srv, [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert "all-time" in p and "recent" in p
    assert _rate(p, "all-time") == pytest.approx(6.5 / 13, abs=0.006)
    assert _rate(p, "recent") == pytest.approx(0.5 / 7, abs=0.006)        # 0 of the last 6
    assert f"0 of the last {SL.RUNG7_RECENT}" in p
    assert "6 of 12" in p
    assert "rhythm" in p.lower() and "recent" in p.lower()               # rhythms change: weigh recent
    assert "base rate" in p                                              # #217's anchor kept


def test_the_prompt_says_how_long_since_they_last_spoke():
    srv = _seat()
    now = _history(srv, [1, 0, 0])                    # spoke at the first check only
    spoke = 10_000.0 + 120
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert f"{round(now - spoke)}s ago" in p


def test_heard_ts_on_chat_in_is_the_last_time_they_spoke():
    srv = _seat()
    now = _history(srv, [0, 0])
    doc = srv.entity_store.docs[RESP]
    pp = doc.properties or {}
    pp["heard_ts"] = now - 42.0
    doc.properties = pp
    assert "42s ago" in SL._rung7_prompt(srv, RESP, 9, now)


def test_the_prompt_still_carries_no_chat_text():
    srv = _seat()
    now = _history(srv, [1, 0])
    CIO.heard(srv, CIO.OPERATOR, "my secret words")
    p = SL._rung7_prompt(srv, RESP, 9, now)
    assert "secret" not in p and "self is here" not in p


# ---- 2. the recent rate's math ---------------------------------------------------

@pytest.mark.parametrize("outcomes,rate", [
    ([], 0.5),                                   # 0 of 0: the prior, not 0/0
    ([0], 0.25),
    ([1, 1, 1, 1, 1, 1], 6.5 / 7),
    ([0, 0, 0, 0, 0, 0], 0.5 / 7),
    ([1] * 10 + [0] * 6, 0.5 / 7),               # only the last RUNG7_RECENT count
    ([0] * 10 + [1, 0, 1, 0, 1, 0], 3.5 / 7),
])
def test_the_recent_rate_uses_the_last_few_checks_with_a_prior(outcomes, rate):
    assert SL.RUNG7_RECENT == 6
    srv = _seat()
    _history(srv, outcomes)
    r, k, n = SL._recent_rate(SL._read(srv), RESP)
    assert r == pytest.approx(rate)
    assert n == min(len(outcomes), SL.RUNG7_RECENT)
    assert 0.0 < r < 1.0


# ---- 3. status exposes it --------------------------------------------------------

def test_status_exposes_recent_rate():
    srv = _seat()
    assert "recent_rate" in SL.status(srv)["rung7"]
    _history(srv, [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    r7 = SL.status(srv)["rung7"]
    assert r7["recent_rate"] == pytest.approx(0.5 / 7, abs=1e-3)
    assert r7["base_rate"] == pytest.approx(6.5 / 13, abs=1e-3)


# ---- 4. the live sequence: a recent-reading fake vs an all-time-only fake ---------

def _replay(model):
    """Each live reply check, asked through the real prompt: the fake answers
    from the rates it was told; returns (outcome, p) per check."""
    srv = _seat()
    t = 10_000.0
    out = []
    habit = [0.2] * (len(LIVE) - 20) + HABIT_LAST20
    for i, ch in enumerate(LIVE):
        SL._append(srv, {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": RESP, "asked": True})
        p = model(SL._rung7_prompt(srv, RESP, 1, t))
        o = int(ch)
        SL._append(srv, {"ts": t + 120, "kind": "reach", "n": 2, "eid": RESP, "p": habit[i], "reply": True,
                         "answer": "changed" if o else "unchanged", "p_llm": p})
        out.append((o, p))
        t += 300
    return out, SL._rung7(SL._read(srv))


def _all_time(prompt):
    return _rate(prompt, "all-time")


def _recent_reader(prompt):
    """Weighs the recent window over the all-time one (what the sentence asks)."""
    return 0.67 * _rate(prompt, "recent") + 0.33 * _rate(prompt, "all-time")


def _brier(xs):
    return sum((p - o) ** 2 for o, p in xs) / len(xs)


def test_a_recent_reading_fake_beats_an_all_time_only_fake_on_the_rungs_window():
    """Over the last RUNG7_LOOKS live reply checks -- the window rung 7 reads --
    the fake that reads the recent rate scores better than the all-time one."""
    rec, s_rec = _replay(_recent_reader)
    alt, s_alt = _replay(_all_time)
    w = SL.RUNG7_LOOKS
    assert _brier(rec[-w:]) < _brier(alt[-w:])
    assert s_rec["skill"] > s_alt["skill"]
    assert s_rec["skill"] >= SL.RUNG7_MARGIN            # habit there is worse than a coin


def test_the_replays_verdict_is_pinned():
    """Measured, not claimed: over ALL 77 live checks the recent window does
    NOT help -- the person's 2-minute replies are close to a fair coin with no
    run structure (P(1|1)=0.50, P(1|0)=0.62), so the all-time rate, which
    averages more, is the better anchor. The win above is a small one."""
    rec, _ = _replay(_recent_reader)
    alt, _ = _replay(_all_time)
    assert _brier(alt) < _brier(rec)
    assert abs(_brier(rec[-20:]) - _brier(alt[-20:])) < 0.01


# ---- 5. the test itself did not move ---------------------------------------------

def test_the_rung7_test_is_unchanged():
    assert SL.RUNG7_MARGIN == 0.15
    assert SL.RUNG7_LOOKS == 20
    assert SL.RUNG7_CHECK_S == 120.0
    assert SL.RUNG7_GRACE_S == 60.0
    assert SL.RUNG7_STALE_S == 600.0
    assert SL._parse_p("VERDICT: 0.3") == pytest.approx(0.3)
