"""#278: the ball's own clock -- turns in seconds, not half-hours.

With the ball (#264) self's turns ran on the chat's clock: at least 30
cycles (~5 min) apart, up to 180 (~30 min), waiting for a moment in bins of
0-2 / 2-10 / 10-30 / 30+ min since the person last acted. Serve-and-return
with an object is seconds. So the toy medium gets its own clock:

  - bins of seconds since the person last acted (TOY_TURN_BINS), a turn at
    most every couple of cycles (TOY_MEET_EVERY), an overdue cap of a few
    minutes (TOY_MEET_MAX); the chat medium keeps today's clock;
  - the reply check of a turn with the ball comes in time: as soon as the
    ball is back on self's side, or TOY_CHECK_S after the roll; a reply is a
    PERSON's roll within TOY_CHECK_S of self's (a later roll is no reply,
    whenever the check comes);
  - self never rolls again before its last roll's check came, and waits for
    the ball to come back: the cap fetches an unreturned one. Only the cycles
    self could have rolled in count as its waiting time (dwell);
  - turns under the new clock carry `clock: 2`, and rungs 7 and 8 read only
    the current clock's turns: old-clock bins never mix with new ones;
  - the page names the toy clock's windows (0-20s ...).
"""
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from tests._self_cap import cap  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
BALL = MEMBRANE_NS + "Component:" + TB.BALL
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _sim():
    spec = importlib.util.spec_from_file_location("rung7_sim_278", ROOT / "experiments" / "rung7_turns" / "sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SIM = _sim()


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=16)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _seat(limit=7):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_Field())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    CIO.entity(srv, CIO.OPERATOR)
    SL.bind(srv)
    TB.bind(srv)
    cap(srv, 1)
    for r in (1, 2, 3, 4, 5, 6):
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    cap(srv, limit)
    TU.sync(srv)                                  # the ball, unlocked at rung 6 (#267)
    return srv


class _Player:
    """A person at the dock: when self rolls the ball to them and `back(age)`
    says so, they roll it back `delay` s later (toybox.poke, as the page)."""

    def __init__(self, back, delay=7.0, seed=1):
        self.back, self.delay, self.rng = back, delay, random.Random(seed)
        self.due = None

    def tick(self, t):
        if self.due is not None and t >= self.due:
            TB.poke(TB.BALL, "roll", by="alice", now=self.due)
            self.due = None

    def saw_turn(self, t, age):
        if self.rng.random() < self.back(age):
            self.due = t + self.delay


def _run(srv, cycles, player=None, t0=10_000.0, monkeypatch=None):
    real_roll = TB.roll_self

    def roll(name, now=None):
        out = real_roll(name, now=now)
        if player is not None and out.get("ok"):
            last = TB.last_person_poke()
            player.saw_turn(now, None if last is None else now - last["ts"])
        return out
    monkeypatch.setattr(TB, "roll_self", roll)
    monkeypatch.setattr(SL, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model at rung 7")))
    for i in range(cycles):
        t = t0 + i * SL.PACE_S
        if player is not None:
            player.tick(t)
        SL.cycle(srv, now=t)
    return t0 + cycles * SL.PACE_S


def _turns(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "push"]


def _checks(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "reach" and r.get("reply")]


# ---- 1. the toy clock's constants; the chat keeps its own -------------------------------

def test_the_toy_medium_has_its_own_clock_and_the_chat_keeps_todays():
    assert SL.TOY_TURN_BINS == (20.0, 60.0, 180.0)
    assert SL.TOY_TURN_NAMES == ("0-20s", "20-60s", "1-3m", "3m+")
    assert len(SL.TOY_TURN_BINS) == len(SL.TURN_BINS)                  # the same bandit, the same 4 windows
    assert SL.TOY_TURN_BINS[0] >= 2 * SL.PACE_S                          # the first window spans two cycles
    assert 1 <= SL.TOY_MEET_EVERY / SL.PACE_S <= 3                       # a turn at most every couple of (10 s) cycles (#343: s)
    assert SL.TOY_MEET_MAX <= 600.0                                      # an overdue cap of a few minutes
    assert SL.TOY_MEET_MAX > SL.TOY_TURN_BINS[-1] + SL.TOY_CHECK_S       # ...that still lets 3m+ be chosen
    assert SL.TOY_CHECK_S < SL.TOY_TURN_BINS[1]
    assert SL.TOY_CLOCK == 2
    # the chat medium: today's clock, untouched
    assert (SL.MEET_EVERY, SL.MEET_MAX) == (300.0, 1800.0)                # #343: 30 and 180 cycles, in s
    assert SL.TURN_BINS == (120.0, 600.0, 1800.0) and SL.TURN_NAMES == ("0-2m", "2-10m", "10-30m", "30m+")
    assert SL.RUNG8_CHECK_S == 120.0


def test_the_bins_of_each_clock():
    toy = SL._clock(SL.TOY_CLOCK)
    assert [SL._turn_bin(a, toy["bins"]) for a in (0.0, 19.9, 20.0, 59.0, 60.0, 179.0, 180.0, None)] == \
        [0, 0, 1, 1, 2, 2, 3, 3]
    chat = SL._clock(1)
    assert chat["bins"] == SL.TURN_BINS and chat["names"] == SL.TURN_NAMES
    assert (chat["every"], chat["max"]) == (SL.MEET_EVERY, SL.MEET_MAX)
    assert (toy["every"], toy["max"]) == (SL.TOY_MEET_EVERY, SL.TOY_MEET_MAX)
    assert [SL._turn_bin(a) for a in (100.0, 200.0, 1000.0, 5000.0)] == [0, 1, 2, 3]   # the default: the chat's
    assert SL._clock_of({"kind": "push", "medium": "toy", "clock": 2}) == SL.TOY_CLOCK
    assert SL._clock_of({"kind": "push", "medium": "toy"}) == 1                  # #264's turns: the chat's clock
    assert SL._clock_of({"kind": "push"}) == 1


# ---- 2. the pace on a seat: a rally in seconds ---------------------------------------------


def test_the_check_comes_as_soon_as_the_ball_is_back(monkeypatch):
    srv = _seat()
    _run(srv, 200, player=_Player(lambda age: 1.0, delay=4.0), monkeypatch=monkeypatch)
    recs = SL._read(srv)
    turns = [r for r in recs if r.get("kind") == "push"]
    for p in turns[:-1]:
        chk = SL._first_look_after(recs, BALL, p["ts"])
        assert chk is not None and chk["reply"] and chk["replied"] is True
        assert chk["ts"] - p["ts"] <= SL.PACE_S + 1e-6                     # the next cycle, not TOY_CHECK_S later


def test_only_a_persons_roll_within_the_horizon_is_a_reply():
    srv = _seat()
    TB.roll_self(TB.BALL, now=1000.0)
    push = {"ts": 1000.0, "kind": "push", "medium": "toy", "clock": SL.TOY_CLOCK, "to": BALL, "toy": TB.BALL}
    TB.poke(TB.BALL, "roll", by="alice", now=1000.0 + SL.TOY_CHECK_S + 5.0)
    assert SL._replied(srv, push, 1000.0 + SL.TOY_CHECK_S + 60.0, "changed") is False   # too late, whenever checked
    TB.roll_self(TB.BALL, now=2000.0)
    push = dict(push, ts=2000.0)
    TB.poke(TB.BALL, "roll", by="alice", now=2000.0 + SL.TOY_CHECK_S - 1.0)
    assert SL._replied(srv, push, 2000.0 + SL.TOY_CHECK_S + 60.0, "unchanged") is True
    assert SL._replied(srv, push, 2000.0 + 5.0, "changed") is False      # not yet, by that check


def test_the_hold_releases_when_the_ball_is_back_or_at_the_horizon():
    srv = _seat()
    TB.roll_self(TB.BALL, now=1000.0)
    push = {"ts": 1000.0, "kind": "push", "medium": "toy", "clock": SL.TOY_CLOCK, "to": BALL, "toy": TB.BALL}
    assert SL._hold_reply(srv, push, 1010.0) is True                     # with them, under the horizon
    assert SL._hold_reply(srv, push, 1000.0 + SL.TOY_CHECK_S) is False    # the horizon
    TB.poke(TB.BALL, "roll", by="alice", now=1005.0)
    assert SL._hold_reply(srv, push, 1010.0) is False                    # back: check now
    chat = {"ts": 1000.0, "kind": "push", "to": "x"}
    assert SL._hold_reply(srv, chat, 1100.0) is True                     # the chat keeps its 120 s
    old = {"ts": 1000.0, "kind": "push", "medium": "toy", "to": BALL, "toy": TB.BALL}
    assert SL._hold_reply(srv, old, 1010.0) is True                      # a #264 turn keeps the chat's horizon


def test_dwell_counts_only_the_cycles_self_could_roll_in(monkeypatch):
    srv = _seat()
    _run(srv, 600, player=_Player(lambda age: 0.6, delay=6.0, seed=5), monkeypatch=monkeypatch)
    recs = SL._read(srv)
    ts = [r for r in recs if r.get("kind") == "push"]
    for a, b in zip(ts, ts[1:]):
        chk = SL._first_look_after(recs, BALL, a["ts"])
        free = int(round((b["ts"] - chk["ts"]) / SL.PACE_S)) + 1          # from its check on, the ball back
        assert sum(b["dwell"]) <= free
        if chk["replied"]:
            assert sum(b["dwell"]) == max(1, b["n"] - max(chk["n"], a["n"] + SL.TOY_MEET_EVERY / SL.PACE_S) + 1)


# ---- 3. a clean start: the window reads the current clock only --------------------------------

def _clock_record(old=40, new=12):
    recs = [{"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V} for r in range(1, 7)]
    t = 1000.0
    for i in range(old):                                           # #264's turns, on the chat's clock
        recs.append({"ts": t, "kind": "push", "n": 1 + 30 * i, "medium": "toy", "to": BALL, "toy": TB.BALL,
                     "bin": 0, "aim": 0, "best": 0, "dwell": [1, 0, 0, 0], "spoke_age": 30.0})
        recs.append({"ts": t + 120.0, "kind": "reach", "eid": BALL, "reply": True, "medium": "toy",
                     "answer": "changed", "replied": True, "p": 0.5})
        t += 300.0
    for i in range(new):                                           # #278: the ball's own clock
        recs.append({"ts": t, "kind": "push", "n": 50_000 + 3 * i, "medium": "toy", "clock": 2, "to": BALL,
                     "toy": TB.BALL, "bin": 1, "aim": 1, "best": 1, "dwell": [0, 2, 0, 0], "spoke_age": 30.0})
        recs.append({"ts": t + 10.0, "kind": "reach", "eid": BALL, "reply": True, "medium": "toy", "clock": 2,
                     "answer": "changed", "replied": i % 3 != 0, "p": 0.5})
        t += 30.0
    return recs


def test_new_turns_carry_the_clock_and_the_window_starts_fresh_with_it():
    recs = _clock_record()
    rows = SL._turn_rows(recs, BALL)
    assert len(rows) == 12 and all(r["bin"] == 1 for r in rows)          # no old-clock bin 0 mixed in
    assert [r["o"] for r in SL._rung8_rows(recs, BALL)] == [0.0 if i % 3 == 0 else 1.0 for i in range(12)]
    assert SL._base_rate(recs, BALL)[2] == 12
    out = SL._rung7(recs)
    assert out["eid"] == BALL and out["n"] == 12
    st = SL._rung7_stats(recs)
    assert st["medium"] == "toy" and st["clock"] == SL.TOY_CLOCK and st["checked"] == 12
    assert [b["bin"] for b in st["bins"]] == list(SL.TOY_TURN_NAMES)
    assert st["best"] == "20-60s"


def test_the_old_clocks_turns_still_read_as_they_were_until_a_new_one():
    recs = _clock_record(new=0)
    assert len(SL._turn_rows(recs, BALL)) == 40
    st = SL._rung7_stats(recs)
    assert st["clock"] == 1 and [b["bin"] for b in st["bins"]] == list(SL.TURN_NAMES)
    assert SL._turn_rows(recs + _clock_record(old=0, new=3)[6:], BALL)[0]["bin"] == 1


def test_the_test_reads_the_bar_of_the_turns_clock():
    d = [(1.0, 0.3, 0.2, 1.0)] * 50 + [(0.0, 0.3, 0.1, 0.0)] * 50       # strong in both halves
    toy, chat = SL._turn_window(d, SL.TOY_CLOCK), SL._turn_window(d, 1)
    assert toy["payoff_z"] == chat["payoff_z"]
    assert SL._clock(SL.TOY_CLOCK)["pay_z"] >= SL.RUNG7_PAY_Z            # never looser than the chat's bar
    assert SL._clock(SL.TOY_CLOCK)["cont_z"] >= SL.RUNG7_CONT_Z
    assert SL._clock(1)["pay_z"] == SL.RUNG7_PAY_Z


# ---- 4. the simulation runs the toy clock ---------------------------------------------------

def test_the_simulation_plays_at_the_toy_clock():
    rows = SIM.drive_toy("rhythm", "policy", 24, 1)
    gaps = [b["ts"] - a["ts"] for a, b in zip(rows, rows[1:])]
    assert len(rows) / 24 > 10                                          # turns an hour: the old clock gave ~4
    assert min(gaps) >= SL.TOY_MEET_EVERY                               # #343: s (was cycles x PACE_S)
    assert max(gaps) <= SL.TOY_MEET_MAX + SL.TOY_CHECK_S
    assert all(r["bin"] == SL._turn_bin(r["age"], SL.TOY_TURN_BINS) for r in rows)
    assert ("session", "policy") in SIM.TOY_CASES


@pytest.mark.parametrize("seed", [1, 2])
def test_a_session_player_lets_the_policy_pass(seed):
    rows = SIM.drive_toy("session", "policy", 168, seed)
    assert any(w["passed"] for _, w in SIM.windows(rows))


@pytest.mark.parametrize("seed", range(3))
def test_chance_still_never_passes_at_the_new_pace(seed):
    for kind in ("coin", "clock", "echo"):
        assert not any(w["passed"] for _, w in SIM.windows(SIM.drive_toy(kind, "policy", 168, seed))), kind


# ---- 5. the sheet and the page name the toy clock -------------------------------------------

def test_the_sheet_counts_the_toy_clocks_pace():
    assert SS._dur(SL.TOY_MEET_EVERY) in SS._needs()[7] and "ball" in SS._needs()[7]   # #343: "20 s"
    floors = SS.fastest_cycles()
    assert floors[7] == SL.RUNG7_PUSHES * SL.TOY_MEET_EVERY / SL.PACE_S                 # cycles at the old pace
    assert floors[8] == SL.RUNG8_LOOKS * SL.TOY_MEET_EVERY / SL.PACE_S


def _js(expr, f="panel.js"):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestPage(unittest.TestCase):
    def test_a_toy_clock_turn_names_its_window_in_seconds(self):
        r = {"kind": "push", "medium": "toy", "clock": 2, "to": BALL, "aim": 0, "bin": 1, "why": "aim"}
        line = _js("L.selfLine(%s)" % json.dumps(r))
        self.assertIn("20-60s", line)
        self.assertIn("after they played", line)
        old = {"kind": "push", "medium": "toy", "to": BALL, "aim": 0, "bin": 1, "why": "aim"}
        self.assertIn("2-10m", _js("L.selfLine(%s)" % json.dumps(old)))
        chat = {"kind": "push", "to": "x", "aim": 0, "bin": 0, "why": "aim"}
        self.assertIn("0-2m after they spoke", _js("L.selfLine(%s)" % json.dumps(chat)))


@pytest.fixture(autouse=True)
def _rebuilt_333(monkeypatch):
    """#333: meet, turns, expect and mirror are not built until they are rebuilt
    on the nudge (part 3); the scoring and the readers tested here run on the
    tree as part 3 will build it, their passes on ladder 3 (the loop's old acts
    -- meet's push, the ball's turns, the asks at a push -- are gone)."""
    from tests._self_cap import rebuilt
    rebuilt(monkeypatch)
