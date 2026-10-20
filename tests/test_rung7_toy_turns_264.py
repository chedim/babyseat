"""#264: rung 7 through play -- self takes turns with a ball, chat goes quiet.

Pre-verbal serve-and-return happens with objects (rolling a ball back and
forth), not words. So:

  - a new toybox kind, `ball`: its state is whose side it is on, its last
    mover and when; every change is tagged `by`; exactly one shared ball is
    provisioned once (idempotent), so the operator doesn't have to add it;
  - self's turn is a field-only act: it rolls the ball to the other
    (`by: self`, through its own `self::roll`), never a chat line, no model,
    no shell; the bandit (rung 7, #258) picks WHEN, over the time since the
    other last acted (a person's last poke on any toy), with today's caps;
  - the other's turn: a PERSON rolls it back (never self, the world or the
    model); a reply is their roll after self's turn, by the reply check;
  - every turn records its medium; chat turns and toy turns never share a
    rung 7 or rung 8 window -- the window starts fresh with the ball;
  - chat goes quiet from rung 7 until rung 11 (speak) opens, and rung 6's
    holding check reads its chat record as it stood at the switch, so the
    silence can't make it slip;
  - the dock shows the ball first and whose side it is on, its popover says
    "roll it back"; the amoeba ripples on self's turn and bounces / droops on
    the reply check.
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
NB = len(SL.TURN_BINS) + 1
RESP = MEMBRANE_NS + "Component:chat:in:operator"
BALL = MEMBRANE_NS + "Component:" + TB.BALL
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _sim():
    spec = importlib.util.spec_from_file_location("rung7_sim_264", ROOT / "experiments" / "rung7_turns" / "sim.py")
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


# ---- 1. the ball: a toybox kind ------------------------------------------------------

def test_the_ball_is_in_the_kit_and_starts_on_selfs_side():
    assert "ball" in TB.KINDS and TB.KINDS["ball"]["clock"] is False
    assert TB.ACTIONS["ball"] == ("roll",)
    st = TB.create("ball", "toy:ball", now=100.0)
    assert st["side"] == "self" and st["by"] is None and st["at"] is None
    assert TB.read("toy:ball", by="self", now=101.0) == {"side": "self"}     # minimal: whose side only


def test_self_rolls_it_to_the_other_and_a_person_rolls_it_back():
    TB.create("ball", "toy:ball", now=100.0)
    out = TB.roll_self("toy:ball", now=110.0)
    assert out["ok"] and out["reading"] == {"side": "other"}
    st = TB.state("toy:ball")
    assert (st["side"], st["by"], st["at"]) == ("other", "self", 110.0)
    back = TB.poke("toy:ball", "roll", by="alice", now=150.0)
    assert back["ok"] and back["by"] == "alice" and back["reading"] == {"side": "self"}
    st = TB.state("toy:ball")
    assert (st["side"], st["by"], st["at"]) == ("self", "alice", 150.0)
    assert (st["other_by"], st["other_at"]) == ("alice", 150.0)
    assert [(e["by"], e["what"], e["side"]) for e in st["log"]] == [("self", "roll", "other"), ("alice", "roll", "self")]
    assert st["counts"]["self"] == 1 and st["counts"]["alice"] == 1


def test_a_person_cannot_roll_it_when_self_has_it():
    TB.create("ball", "toy:ball", now=100.0)
    out = TB.poke("toy:ball", "roll", by="alice", now=120.0)
    assert not out["ok"] and "self's side" in out["error"]
    assert TB.state("toy:ball")["log"] == []


def test_nobody_else_can_roll_as_self_and_the_world_never_rolls_it():
    TB.create("ball", "toy:ball", now=100.0)
    TB.roll_self("toy:ball", now=110.0)
    out = TB.poke("toy:ball", "roll", by="self", now=120.0)             # a page that calls itself self
    assert out["ok"] and out["by"] == "person:self"
    TB.roll_self("toy:ball", now=130.0)
    assert TB.tick("toy:ball", now=10_000.0) is False                    # no clock of its own
    assert {e["by"] for e in TB.state("toy:ball")["log"]} == {"self", "person:self"}


def test_a_model_roll_is_tagged_model_and_is_not_the_others_turn():
    TB.create("ball", "toy:ball", now=100.0)
    TB.roll_self("toy:ball", now=110.0)
    out = TB.poke("toy:ball", "roll", by="model", now=120.0)
    assert out["ok"] and out["by"] == "model"
    st = TB.state("toy:ball")
    assert st["side"] == "self" and st["by"] == "model"
    assert st["other_at"] is None                                        # not a person: no turn of the other's
    assert TB.last_person_poke() is None


def test_a_rolled_ball_replays_under_its_seed(tmp_path, monkeypatch):
    def run(d):
        monkeypatch.setenv("MEMBRANE_DATA_DIR", str(d))
        TB._forget()
        TB.create("ball", "toy:ball", params={"seed": 7}, now=0.0)
        for i in range(6):
            TB.roll_self("toy:ball", now=10.0 + 100 * i)
            if i % 2 == 0:
                TB.poke("toy:ball", "roll", by="alice", now=50.0 + 100 * i)
            TB.read("toy:ball", by="self", now=60.0 + 100 * i)
        st = TB.state("toy:ball")
        return {k: st[k] for k in ("side", "by", "at", "seed", "other_at", "counts", "log")}
    a, b = run(tmp_path / "a"), run(tmp_path / "b")
    assert a == b and a["seed"] == 7


def test_a_look_never_moves_the_ball():
    TB.create("ball", "toy:ball", now=100.0)
    TB.roll_self("toy:ball", now=110.0)
    for t in (120.0, 130.0):
        assert TB.read("toy:ball", by="self", now=t) == {"side": "other"}
    assert len(TB.state("toy:ball")["log"]) == 1


def test_the_others_last_act_is_a_persons_poke_on_any_toy():
    TB.create("ball", "toy:ball", now=0.0)
    TB.create("die", "toy:die", now=0.0)
    assert TB.last_person_poke() is None
    TB.poke("toy:die", "roll", by="model", now=10.0)
    TB.tick("toy:die", now=99999.0)                                      # the world
    TB.roll_self("toy:ball", now=20.0)
    assert TB.last_person_poke() is None
    TB.poke("toy:die", "roll", by="alice", now=30.0)
    assert TB.last_person_poke() == {"ts": 30.0, "by": "alice", "toy": "toy:die"}
    TB.poke("toy:ball", "roll", by="", now=40.0)                          # '' is the operator: a person
    assert TB.last_person_poke()["by"] == "operator" and TB.last_person_poke()["toy"] == "toy:ball"
    assert TB.is_person("alice") and TB.is_person("operator") and TB.is_person("person:self")
    for not_a_person in ("self", "world", "model", "", None):
        assert not TB.is_person(not_a_person)


def test_list_and_the_dock_read_whose_side():
    TB.create("ball", "toy:ball", now=0.0)
    TB.roll_self("toy:ball", now=5.0)
    row = next(r for r in TB.list_toys() if r["name"] == "toy:ball")
    assert row["kind"] == "ball" and row["reading"] == "other"
    TB.poke("toy:ball", "roll", by="alice", now=6.0)                  # writes the last-person mark
    assert [r["name"] for r in TB.list_toys()] == ["toy:ball"]          # a mark is not a toy


# ---- 2. one shared ball, provisioned once --------------------------------------------

def _seat(passed=(1, 2, 3, 4, 5, 6), limit=7, ball=True):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_Field())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    CIO.entity_in(srv, CIO.OPERATOR)
    SL.bind(srv)
    TB.bind(srv)
    cap(srv, 1)
    for r in passed:
        line = {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9}
        if r >= 7:
            line["ladder"] = SL.LADDER_V
        SL._append(srv, line)
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    cap(srv, limit)
    if ball:
        TU.sync(srv)                       # the unlocks of the rungs passed (#267): the ball at rung 6
    return srv


def _rung(srv, n):
    line = {"ts": 2.0, "kind": "rung", "rung": n, "skill": 0.9}
    if n >= 7:
        line["ladder"] = SL.LADDER_V
    SL._append(srv, line)


class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=16)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _balls(srv):
    return [e for e in srv.entity_store.docs.values() if ((e.properties or {}).get("toybox") or {}).get("kind") == "ball"]


def _ball_names():
    return [r["name"] for r in TB.list_toys() if r["kind"] == "ball"]


def test_the_rung_six_unlock_switches_on_exactly_one_ball_once():
    srv = _seat(passed=(1, 2, 3, 4, 5), limit=6, ball=False)
    TU.sync(srv)
    assert _ball_names() == []                                      # locked before rung 6
    _rung(srv, 6)
    TU.sync(srv)
    assert _ball_names() == []                                      # #299: earned, not accepted yet: still locked
    SL.accept(srv, "meet")                                          # the operator's accept syncs the unlock
    TU.sync(srv)
    line = next(r for r in SL._read(srv) if r.get("kind") == "unlock" and r.get("rung") == 6)
    assert line["toys"] == ["ball"] and line["added"] == [TB.BALL]
    assert _ball_names() == [TB.BALL] and len(_balls(srv)) == 1
    TU.sync(srv)
    TU.sync(srv)
    assert _ball_names() == [TB.BALL] and len(_balls(srv)) == 1
    assert not [r for r in SL._read(srv) if r.get("kind") == "unlock_built"]


def test_a_removed_ball_stays_removed_and_an_existing_one_is_kept(tmp_path, monkeypatch):
    srv = _seat()
    TB.remove(srv, TB.BALL)
    TU.sync(srv)
    assert _ball_names() == [] and not _balls(srv)                  # the operator's removal stands
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "other"))
    srv2 = _seat(ball=False)
    TB.add(srv2, "ball", name="toy:red-ball")
    TU.sync(srv2)
    assert _ball_names() == ["toy:red-ball"]                        # a ball is there already
    assert SL._ball(srv2)[1] == "toy:red-ball"


def test_a_seat_that_unlocked_the_ball_before_it_was_built_gets_it_once(tmp_path):
    # #267 wrote rung 6's unlock line while the ball was a name only: nothing added
    srv = _seat(ball=False)
    SL._append(srv, {"ts": 3.0, "kind": "unlock", "rung": 6, "toys": ["ball"], "late": True})
    TU.sync(srv)
    assert _ball_names() == [TB.BALL]
    built = [r for r in SL._read(srv) if r.get("kind") == "unlock_built"]
    assert len(built) == 1 and built[0]["toys"] == ["ball"] and built[0]["added"] == [TB.BALL]
    TB.remove(srv, TB.BALL)
    TU.sync(srv)
    assert _ball_names() == []                                      # not brought back
    assert len([r for r in SL._read(srv) if r.get("kind") == "unlock_built"]) == 1



# ---- 3. self's turn: a field-only act -----------------------------------------------

class _Player:
    """A person at the toy dock: rolls the ball back `delay` seconds after
    self's turn when `back(age)` says so (age: how long since they last
    acted), through toybox.poke -- the same call the page makes."""

    def __init__(self, back, by="alice", delay=10.0, seed=1, play_every=None):   # #278: a return within TOY_CHECK_S
        self.back, self.by, self.delay, self.rng = back, by, delay, random.Random(seed)
        self.due = None
        self.play_every, self.next_play = play_every, None

    def tick(self, t):
        if self.due is not None and t >= self.due:
            TB.poke(TB.BALL, "roll", by=self.by, now=self.due)
            self.due = None
        if self.play_every:                                 # they play with another toy now and then
            if self.next_play is None:
                self.next_play = t + self.rng.uniform(0, self.play_every)
            if t >= self.next_play:
                TB.poke("toy:die", "roll", by=self.by, now=t)
                self.next_play = t + self.rng.uniform(0.5, 1.5) * self.play_every

    def saw_turn(self, t, age):
        if self.rng.random() < self.back(age):
            self.due = t + self.delay


def _run(srv, cycles, player=None, t0=10_000.0, monkeypatch=None, chat=None):
    real_roll = TB.roll_self

    def roll(name, now=None):
        out = real_roll(name, now=now)
        if player is not None and out.get("ok"):
            last = TB.last_person_poke()
            player.saw_turn(now, None if last is None else now - last["ts"])
        return out
    monkeypatch.setattr(TB, "roll_self", roll)
    monkeypatch.setattr(SL, "_push_chat", chat or (lambda *a, **k: (_ for _ in ()).throw(AssertionError("chat is quiet"))))
    monkeypatch.setattr(SL, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model at rung 7")))
    for i in range(cycles):
        t = t0 + i * SL.PACE_S
        if player is not None:
            player.tick(t)
        SL.cycle(srv, now=t)
    return t0 + cycles * SL.PACE_S


def _turns(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "push"]


def test_self_rolls_the_ball_through_its_own_method_never_chat_or_shell(monkeypatch):
    srv = _seat()
    import subprocess as _sp
    monkeypatch.setattr(_sp, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no shell")))
    calls = []
    import membrane.night as N
    real = N.method_call

    def spy(srv_, est, a, b, eid, meth, args):
        calls.append((eid, meth, dict(args)))
        return real(srv_, est, a, b, eid, meth, args)
    monkeypatch.setattr(N, "method_call", spy)
    _run(srv, 400, monkeypatch=monkeypatch)
    ts = _turns(srv)
    assert len(ts) >= 2
    for p in ts:
        assert p["medium"] == "toy" and p["to"] == BALL and p["toy"] == TB.BALL
        assert p["eid"] == SL._eid()                                    # the act is self's own
        assert p["why"] in ("aim", "overdue") and p["aim"] in range(NB) and len(p["dwell"]) == NB
        assert "spoke_age" not in p                                    # nobody has acted yet
    rolls = [c for c in calls if c[1] == "roll"]
    assert len(rolls) == len(ts) and all(c[0] == SL._eid() and c[2] == {"toy": TB.BALL} for c in rolls)
    log = TB.state(TB.BALL)["log"]
    assert [e["by"] for e in log] == ["self"] * len(ts)
    assert "roll" in SL.METHODS and SL.METHODS["roll"] == "self::roll {{toy}}"


def test_selfs_roll_refuses_anything_but_the_shared_ball(monkeypatch):
    srv = _seat()
    TB.add(srv, "die", name="toy:die")
    SL._ctx.cycle = {"srv": srv, "act": "roll", "now": 5.0}
    try:
        assert SL.from_command("roll toy:die")["rc"] != 0
        assert SL.from_command("roll " + TB.BALL)["rc"] == 0
    finally:
        SL._ctx.cycle = None
    SL._ctx.cycle = None
    assert SL.from_command("roll " + TB.BALL)["rc"] != 0                 # only its own loop rolls it
    assert [e["by"] for e in TB.state(TB.BALL)["log"]] == ["self"]
    assert TB.state("toy:die")["log"] == []


def test_the_turns_keep_the_balls_pace(monkeypatch):
    # #278: the ball's own clock (was the chat's MEET_EVERY..MEET_MAX)
    srv = _seat()
    _run(srv, 1500, player=_Player(lambda age: 0.7), monkeypatch=monkeypatch)
    ns = [p["n"] for p in _turns(srv)]
    assert len(ns) >= 6
    assert all(b - a >= SL.TOY_MEET_EVERY for a, b in zip(ns, ns[1:]))  # never faster than the roll cap
    assert all(b - a <= SL.TOY_MEET_MAX for a, b in zip(ns, ns[1:]))


def test_no_ball_no_turn_and_still_no_chat(monkeypatch):
    srv = _seat()
    TB.remove(srv, TB.BALL)                                         # the operator took it away
    _run(srv, 200, monkeypatch=monkeypatch)
    assert _turns(srv) == [] and _ball_names() == []


# ---- 4. the other's turn: a person's roll, after self's, by the check ------------------

def test_the_reply_check_reads_a_persons_roll_back(monkeypatch):
    srv = _seat()
    _run(srv, 1500, player=_Player(lambda age: 0.6), monkeypatch=monkeypatch)
    recs = SL._read(srv)
    checks = [r for r in recs if r.get("kind") == "reach" and r.get("reply")]
    assert checks and all(r["eid"] == BALL and r["medium"] == "toy" and isinstance(r["replied"], bool)
                          for r in checks)
    assert any(r["replied"] for r in checks) and not all(r["replied"] for r in checks)
    later = [p for p in _turns(srv) if "spoke_age" in p]
    assert later                                                    # the other's last act, as self knew it


def _turn(ts):
    return {"ts": ts, "kind": "push", "medium": "toy", "to": BALL, "toy": TB.BALL}


def test_only_a_persons_roll_is_a_reply():
    srv = _seat()
    TB.roll_self(TB.BALL, now=1000.0)
    push = _turn(1000.0)
    assert SL._replied(srv, push, 1120.0, "changed") is False         # nobody rolled it back
    TB.poke(TB.BALL, "roll", by="model", now=1050.0)
    assert SL._replied(srv, push, 1120.0, "changed") is False         # the model's roll is not the other's turn
    TB.roll_self(TB.BALL, now=2000.0)
    push = _turn(2000.0)
    TB.poke(TB.BALL, "roll", by="alice", now=2050.0)
    assert SL._replied(srv, push, 2120.0, "unchanged") is True
    assert SL._replied(srv, push, 2040.0, "changed") is False         # not yet, by that check


def test_a_roll_from_before_the_turn_is_no_reply():
    srv = _seat()
    TB.roll_self(TB.BALL, now=900.0)
    TB.poke(TB.BALL, "roll", by="alice", now=950.0)
    TB.roll_self(TB.BALL, now=1000.0)
    assert SL._replied(srv, _turn(1000.0), 1120.0, "changed") is False


def test_a_toy_poke_by_a_person_is_the_others_last_act_but_no_reply():
    srv = _seat()
    TB.add(srv, "die", name="toy:die")
    TB.roll_self(TB.BALL, now=1000.0)
    TB.poke("toy:die", "roll", by="alice", now=1030.0)
    assert SL._replied(srv, _turn(1000.0), 1120.0, "changed") is False
    assert SL._last_acted(srv, 1100.0) == 1030.0
    TB.poke("toy:die", "roll", by="model", now=1060.0)
    assert SL._last_acted(srv, 1100.0) == 1030.0                    # the model is not the other


# ---- 5. the bandit learns the window from ball replies, either way ---------------------

def _best(rows):
    k, n = SL._turn_post(rows)
    return int(np.argmax((k + 1) / (n + 2)))


def _share(rows, b, last=100):
    tail = rows[-last:]
    return sum(r["bin"] == b for r in tail) / len(tail)


@pytest.mark.parametrize("seed", [1, 2])
def test_the_policy_learns_to_roll_soon_after_a_rhythmic_player_acts(seed):
    rows = SIM.drive_toy("rhythm", "policy", 72, seed)
    assert _best(rows) == 0                                             # within 20 s of their last act (#278)
    # #278: on the ball's clock self rolls only once the ball is back, so its
    # first moment after their roll back IS 0-20s -- a timer lands there too
    # (the reason rung 7 can't credit this player: see experiments/rung7_turns)
    assert _best(SIM.drive_toy("rhythm", "timer", 72, seed)) == 0


@pytest.mark.parametrize("seed", [1, 2])
def test_the_policy_learns_to_wait_for_a_late_player(seed):
    rows = SIM.drive_toy("late", "policy", 168, seed)
    assert _best(rows) == 2                                             # 10-30 min after their last act
    assert _share(rows, 0) < 0.2
    timer = SIM.drive_toy("late", "timer", 168, seed)                   # lands there by itself, less often
    assert _share(rows, 2, 300) > _share(timer, 2, 300) + 0.05
    assert sum(r["replied"] for r in rows[-300:]) / 300 > sum(r["replied"] for r in timer[-300:]) / 300


def test_on_a_seat_it_learns_the_window_from_real_rolls(monkeypatch, tmp_path):
    # the same real path (self::roll, the ball's files, the reply check): a
    # player who also rolls a die every ~20 min, and rolls the ball back only
    # when the turn comes 2-10 min after their last act -- or only 10-30 min after
    def learned(back, seed):
        monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / f"seat{seed}"))   # a seat of its own
        srv = _seat()
        TB.add(srv, "die", name="toy:die")
        _run(srv, 1500, player=_Player(back, seed=seed, play_every=1200.0), monkeypatch=monkeypatch)   # #278: ~4 h at the ball's pace
        rows = SL._turn_rows(SL._read(srv), BALL)
        return rows, _best(rows)
    # #278: the ball's windows -- 20-60 s, or only 1-3 min, after their last act
    soon, b1 = learned(lambda age: 0.9 if age is not None and 20.0 <= age < 60.0 else 0.05, 3)
    late, b2 = learned(lambda age: 0.9 if age is not None and 60.0 <= age < 180.0 else 0.05, 4)
    assert len(soon) >= 25 and len(late) >= 25
    assert (b1, b2) == (1, 2)


# ---- 6. the test: a rhythmic partner passes, a coin and a timer don't --------------------

def _ever_passes(rows):
    return any(w["passed"] for _, w in SIM.windows(rows))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_session_player_lets_the_policy_pass(seed):
    # #278: on the ball's clock the rhythmic player's best moment is the first
    # one (a timer's); a person who plays in sessions passes (experiments/rung7_turns)
    assert _ever_passes(SIM.drive_toy("session", "policy", 168, seed))


@pytest.mark.parametrize("seed", range(4))
def test_a_coin_never_passes(seed):
    assert not _ever_passes(SIM.drive_toy("coin", "policy", 168, seed))


@pytest.mark.parametrize("seed", range(3))
def test_a_timer_never_passes(seed):
    assert not _ever_passes(SIM.drive_toy("rhythm", "timer", 120, seed))      # self on a timer
    assert not _ever_passes(SIM.drive_toy("clock", "policy", 120, seed))      # a player on a timer
    assert not _ever_passes(SIM.drive_toy("echo", "policy", 120, seed))       # a machine that always returns it


# ---- 7. chat turns and toy turns never share a window -----------------------------------

def _mixed_record(chat=150, toy=30):
    recs = [{"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9} for r in range(1, 7)]
    t = 1000.0
    for i in range(chat):
        recs.append({"ts": t, "kind": "push", "n": 1 + 30 * i, "to": RESP, "bin": 0, "aim": 0, "best": 0,
                     "dwell": [1, 0, 0, 0], "spoke_age": 30.0})
        recs.append({"ts": t + 120.0, "kind": "reach", "eid": RESP, "reply": True, "answer": "changed",
                     "replied": True, "p": 0.5})
        t += 300.0
    for i in range(toy):
        recs.append({"ts": t, "kind": "push", "n": 10_000 + 30 * i, "medium": "toy", "to": BALL, "toy": TB.BALL,
                     "bin": 1, "aim": 1, "best": 1, "dwell": [0, 1, 0, 0], "spoke_age": 300.0})
        recs.append({"ts": t + 120.0, "kind": "reach", "eid": BALL, "reply": True, "medium": "toy",
                     "answer": "changed", "replied": i % 2 == 0, "p": 0.5})
        t += 300.0
    return recs


def test_the_window_starts_fresh_with_the_ball():
    recs = _mixed_record()
    out = SL._rung7(recs)
    assert out["eid"] == BALL and out["n"] == 30
    assert out["crit"]["pushes"] == pytest.approx(30 / SL.RUNG7_PUSHES)
    first_toy = next(r["ts"] for r in recs if r.get("medium") == "toy")
    assert all(r["ts"] >= first_toy for r in SL._turn_rows(recs, BALL))
    r8 = SL._rung8(recs)
    assert r8["eid"] == BALL and r8["crit"]["looks"] == pytest.approx(30 / SL.RUNG8_LOOKS)
    st = SL._rung7_stats(recs)
    assert st["medium"] == "toy" and st["checked"] == 30


def test_rows_of_another_medium_never_pair_even_at_one_target():
    # belt and braces: a chat push and a toy turn at the same target never
    # land in one window -- the medium of the current turn decides
    recs = [{"ts": 10.0, "kind": "push", "to": "x", "bin": 0, "aim": 0, "best": 0, "dwell": [1, 0, 0, 0]},
            {"ts": 130.0, "kind": "reach", "eid": "x", "reply": True, "answer": "changed", "replied": True},
            {"ts": 400.0, "kind": "push", "medium": "toy", "to": "x", "bin": 1, "aim": 1, "best": 1,
             "dwell": [0, 1, 0, 0]},
            {"ts": 520.0, "kind": "reach", "eid": "x", "reply": True, "medium": "toy", "answer": "changed",
             "replied": False}]
    rows = SL._turn_rows(recs, "x")
    assert [r["replied"] for r in rows] == [False]
    assert [r["o"] for r in SL._rung8_rows(recs, "x")] == [0.0]
    assert SL._turn_rows(recs, "x", medium="chat")[0]["replied"] is True
    assert SL._base_rate(recs, "x")[2] == 1


def test_rung_eight_reads_the_toy_turns_replied():
    recs = _mixed_record()
    rows = SL._rung8_rows(recs, BALL)
    assert [r["o"] for r in rows] == [1.0 if i % 2 == 0 else 0.0 for i in range(30)]
    assert all(r["gap"] == 300.0 for r in rows)


def test_a_toy_turn_without_spoke_age_has_no_gap():
    # self's own roll changes the ball: a look reading `changed` is no act of the other's
    recs = [{"ts": 100.0, "kind": "reach", "eid": BALL, "answer": "changed", "p": 0.5},
            {"ts": 200.0, "kind": "push", "medium": "toy", "to": BALL, "toy": TB.BALL},
            {"ts": 320.0, "kind": "reach", "eid": BALL, "reply": True, "medium": "toy", "answer": "changed",
             "replied": False, "p": 0.5}]
    assert SL._rung8_rows(recs, BALL)[0]["gap"] is None


# ---- 8. chat goes quiet; rung 6 doesn't slip because of it ------------------------------

class _Chat:
    """The person in chat: imperfect, variable answers while `talking`,
    silence after (what a quiet chat may bring)."""

    def __init__(self, seed=7):
        self.rng, self.talking = random.Random(seed), True

    def __call__(self, srv, ent, now):
        if "chat:in" in ent.eid:
            return {"fired": True, "state": {"x": 1}, "changed": self.talking and self.rng.random() < 0.4}
        if (ent.properties or {}).get("toybox"):
            return WA.entity_read_back(srv, ent, force=False, by="self")
        return {"fired": True, "state": {"v": 1}, "changed": False}


def test_chat_pushes_stop_and_rung_six_holds_through_the_silence(monkeypatch):
    srv = _seat(passed=(1, 2, 3, 4, 5), limit=6)
    chat = _Chat()
    monkeypatch.setattr(SL, "_read_entity", chat)
    lines = []
    t = _run(srv, 600, monkeypatch=monkeypatch, chat=lambda s, text, person="": lines.append(text))
    assert lines and 6 in SL._passed(SL._read(srv))                  # rung 6, met in chat as before
    pushed = len(lines)
    cap(srv, 7)                                             # rung 7: turns with the ball
    chat.talking = False                                             # ...and the chat falls silent
    _run(srv, 1500, t0=t, monkeypatch=monkeypatch, chat=lambda s, text, person="": lines.append(text))
    assert len(lines) == pushed                                      # not one more chat line
    recs = SL._read(srv)
    toy = [r for r in recs if r.get("kind") == "push" and r.get("medium") == "toy"]
    assert toy and all(r["to"] == BALL for r in toy)
    st = SL.status(srv)
    assert st["rung"] == 6 and st["slipping"] is False
    assert SL._rung6(recs)["passed"] and SL._rung6(recs)["eid"] == RESP
    # a long silence WOULD read rung 6 slipping: the chat's last looks all
    # `unchanged`, predicted near-perfectly -- a body. Read as of the switch,
    # it holds; read over everything (no turn with the ball), it slips
    end = recs[-1]["ts"]
    silent = [{"ts": end + 60.0 * i, "kind": "reach", "eid": RESP, "answer": "unchanged", "p": 0.02}
              for i in range(1, 26)]
    assert SL._rung6(recs + silent)["passed"] is True
    talk = [r for r in recs if not (r.get("kind") == "push" and r.get("medium") == "toy")]
    assert SL._split(talk + silent)[RESP] == "body"
    assert SL._rung6(talk + silent)["passed"] is False


def test_until_rung_eleven_opens():
    assert SL.SPEAK_RUNG == 11 and SL.RUNG_NAMES[11] == "speak"
    assert SL.SPEAK_RUNG in SL.LADDER                                 # #311: built -- chat stays quiet until it OPENS


def test_a_seat_below_rung_seven_still_meets_in_chat(monkeypatch):
    srv = _seat(passed=(1, 2, 3, 4, 5), limit=6)
    monkeypatch.setattr(SL, "_read_entity", _Chat())
    lines = []
    _run(srv, 200, monkeypatch=monkeypatch, chat=lambda s, text, person="": lines.append(text))
    assert lines and all(not r.get("medium") for r in _turns(srv))
    assert TB.peek(TB.BALL) is None or TB.state(TB.BALL)["log"] == []     # no ball before rung 6, no roll


# ---- 9. the dock and the sheet ------------------------------------------------------------

def test_the_dock_puts_the_ball_first_and_says_whose_side(monkeypatch):
    srv = _seat()
    TB.add(srv, "die", name="toy:a-die")
    TB.roll_self(TB.BALL, now=None)
    d = SS.dock(srv, fresh=True)
    assert d["toys"][0]["kind"] == "ball" and d["toys"][0]["name"] == TB.BALL
    assert d["toys"][0]["reading"] == {"side": "other"}
    assert d["toys"][0]["last"]["by"] == "self"


def test_the_quest_log_says_turns_are_with_the_ball(monkeypatch):
    srv = _seat()
    _run(srv, 600, player=_Player(lambda age: 0.6), monkeypatch=monkeypatch)
    s = SS.sheet(srv, fresh=True)
    assert s["quest"]["rung"] == 7
    first = s["quest"]["criteria"][0]["text"]
    assert "ball" in first and "turns" in first
    assert s["header"]["lines_sent"] == 0                          # a roll is not a line
    assert not any(o["who"].endswith("toy:ball") for o in s["relationships"]["others"])
    assert "ball" in SS._needs()[7]


def test_the_status_carries_the_medium(monkeypatch):
    srv = _seat()
    _run(srv, 600, player=_Player(lambda age: 0.6), monkeypatch=monkeypatch)
    st = SL.status(srv)
    assert st["rung7"]["medium"] == "toy"
    assert st["meets"]["medium"] == "toy" and st["meets"]["other"] == BALL


# ---- 10. the page: the dock's ball, its popover, the amoeba (node) -----------------------

def _js(expr, f="panel.js"):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _ball(side="other", **kw):
    t = {"name": "toy:ball", "kind": "ball", "reading": {"side": side}, "health": "ok", "looks": 3,
         "changes": {"self": 2, "alice": 1}, "last": {"by": "self", "what": "roll", "age_s": 2}}
    t.update(kw)
    return t


@NODE
class TestDockBall(unittest.TestCase):
    def test_the_glyph_says_whose_side(self):
        g = _js("L.toyGlyph(%s)" % json.dumps(_ball("other")))
        self.assertIn("on your side", g["label"])
        self.assertIn("side-other", g["html"])
        g = _js("L.toyGlyph(%s)" % json.dumps(_ball("self")))
        self.assertIn("with Blurp", g["label"])
        self.assertIn("side-self", g["html"])

    def test_the_popover_offers_roll_it_back_only_on_your_side(self):
        h = _js("L.toyPopoverHtml(%s, null)" % json.dumps(_ball("other")))
        self.assertIn('data-poke-action="roll"', h)
        self.assertIn("roll it back", h)
        self.assertIn('data-poke-toy="toy:ball"', h)
        h = _js("L.toyPopoverHtml(%s, null)" % json.dumps(_ball("self")))
        self.assertNotIn("data-poke-action", h)
        self.assertIn("Blurp has the ball", h)

    def test_the_dock_html_draws_it_first_as_given(self):
        h = _js("L.toyDockHtml(%s)" % json.dumps([_ball(), {"name": "toy:die", "kind": "die", "reading": {"value": 2}}]))
        self.assertLess(h.index('data-dock-toy="toy:ball"'), h.index('data-dock-toy="toy:die"'))
        self.assertIn("g-ball", h)

    def test_the_card_says_turns_with_the_ball(self):
        st = {"self": {"ladder": [{"rung": 7, "name": "take turns", "state": "current"}], "rung": 6, "mode": "on",
                       "limit": 7, "cycles": 9, "rung7": {"medium": "toy", "checked": 4, "bins": []}}}
        self.assertIn("turns with the ball", _js("L.selfCardHtml(%s)" % json.dumps(st)))


@NODE
class TestAmoebaOnBallTurns(unittest.TestCase):
    def _kinds(self, r):
        return [a["kind"] for a in _js("L.amoebaActs(%s)" % json.dumps(r), "field3d.js")]

    def test_selfs_turn_ripples(self):
        self.assertEqual(self._kinds({"kind": "push", "medium": "toy", "to": BALL, "toy": "toy:ball"}),
                         ["push", "listen"])

    def test_the_reply_check_bounces_on_a_roll_back_and_droops_without(self):
        # the ball's look can read `unchanged` though they rolled it back (self
        # last saw it on its own side) and `changed` though they didn't (self's
        # own roll): the check's `replied` decides
        self.assertEqual(self._kinds({"kind": "reach", "eid": BALL, "reply": True, "medium": "toy",
                                      "answer": "unchanged", "replied": True}), ["bounce"])
        self.assertEqual(self._kinds({"kind": "reach", "eid": BALL, "reply": True, "medium": "toy",
                                      "answer": "changed", "replied": False}), ["droop"])
        self.assertEqual(self._kinds({"kind": "reach", "reply": True, "answer": "changed"}), ["bounce"])


class TestDocs(unittest.TestCase):
    def test_selfgrowth_says_how_to_play_rung_seven(self):
        doc = (ROOT / "docs" / "selfgrowth.md").read_text()
        self.assertIn("roll it back", doc)
        self.assertIn("`ball`", doc)
        self.assertIn("natural rhythm", doc)
