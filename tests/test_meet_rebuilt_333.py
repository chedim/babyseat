"""#333 part 3e: MEET AN OTHER (rung 6), rebuilt on delayed contingency; the
new toy unlock order (#337's, mapped to skills).

An other answers Blurp's acts late and on its own:
- A REPLY LOOK at a thing is a look with no nudge since the previous look,
  where that previous look already came after Blurp's nudge (a check: an
  answered nudged look outside the audits). Its own immediate effect was seen
  at the check, so a change now happened after Blurp saw what it did. The
  record marks it `follow: true`.
- THE FOLLOW-UP: while meet is practised, after a check the thing is looked
  at again FOLLOW_S later (a scheduled look, a scaffold until Blurp times its
  own looks), and not before.
- THE TEST over its last reply looks at one thing (the best thing counts):
  1. looks: at least RUNG6_LOOKS of them;
  2. lift: the change share beats the thing's own tempo prediction p at the
     same gap: z = sum(o - p) / sqrt(sum p(1 - p)) >= RUNG6_Z and
     mean(o - p) >= RUNG6_MARGIN;
  3. audit: among the reply changes that carry `by`, at least
     RUNG13_AUDIT_SHARE are not `self` (at least RUNG13_AUDIT_MIN of them).
     A changed look at the chat thing carries the person (`heard` lines are
     theirs by construction).
- Prerequisites (reach, nudge); built; its pass line carries `ladder: 3`.
- Reply looks, like nudged looks, are kept out of the tempo and the rate fit,
  so "its own tempo" stays the thing's own.

The toys unlock by skill (toy_unlocks.UNLOCKS: kind -> achievement id):
tower with feel, slow with look, fast with kinds, burst with patience, die and
drift with shift, the ball and then the mirror with first reach; body and
rattle are dropped from the order (a seat that has them keeps them)."""
import json
import math
import os
import random
import sys
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

from tests._self_cap import world  # noqa: E402

DIM = 16
T0 = 1_700_000_000.0
NS = MEMBRANE_NS + "Component:"
BALL = NS + "toy:ball"
DRIFT = NS + "toy:drift"
B1, B2 = NS + "toy:tower:b1", NS + "toy:tower:b2"
CHAT = CIO.eid_of("alice")


def _follows(eid, n, change, p, by, t0=T0, gap=30.0):
    """n reply looks at eid, each after an answered check (the check's line
    first), the way the seat records them: the check (nudged 1, changed by
    self) then the follow-up `gap` s later (nudged 0, follow, the tempo
    prediction p at that gap). change(i) -> the follow-up found a change;
    by(i) -> its changers (or None)."""
    out = []
    for i in range(n):
        t = t0 + i * 600.0
        out.append({"ts": t, "kind": "reach", "eid": eid, "p": 0.1, "p_n": 0.9, "nudged": 1,
                    "answer": "changed", "by": ["self"]})
        x = bool(change(i))
        pi = p(i) if callable(p) else p
        r = {"ts": t + gap, "kind": "reach", "eid": eid, "p": pi, "p_n": pi, "nudged": 0, "follow": True,
             "dt": gap, "answer": "changed" if x else "unchanged"}
        b = by(i) if x else None
        if b is not None:
            r["by"] = b
        out.append(r)
    return out


# ---- the approved values and the tree -------------------------------------------------------

def test_the_approved_values():
    assert SL.FOLLOW_S == 30.0
    assert SL.RUNG6_LOOKS == 20
    assert SL.RUNG6_Z == 3.0
    assert SL.RUNG6_MARGIN == 0.10


def test_meet_is_built_on_look_and_first_reach():
    a = SL.ACHIEVEMENTS["meet"]
    assert a["built"] is True and a["rung"] == 6
    assert a["prereqs"] == ("reach", "nudge")
    assert "meet" in SL.BUILT
    assert SL.PROGRESS[6] is SL._rung6
    assert SL.SKILL_PASS[6] == SL.RUNG6_MARGIN
    order = list(SL.ACHIEVEMENTS)
    assert order.index("nudge") < order.index("meet")          # a prerequisite comes first
    assert SS.BUILT_AT["meet"]["pr"] == 479                    # rebuilt: built from this PR, not #187's
    import datetime as _dt
    assert SS.BUILT_AT["meet"]["merged"] == _dt.datetime(2026, 10, 23, 8, 18, 12,      # #479 merged
                                                         tzinfo=_dt.timezone.utc).timestamp()   # until its merge's


# ---- the test over the record ---------------------------------------------------------------

def test_a_ball_rolled_back_at_each_follow_up_passes():
    p = SL._rung6(_follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"]))
    assert p["passed"], p
    assert p["eid"] == BALL
    assert p["crit"] == {"looks": 1.0, "lift": 1.0, "audit": 1.0}
    assert p["skill"] == pytest.approx(0.9)                     # mean(o - p)


def test_a_ball_nobody_touches_does_not_pass():
    p = SL._rung6(_follows(BALL, 30, lambda i: False, 0.1, lambda i: None))
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0 and p["crit"]["lift"] == 0.0


def test_a_change_its_own_nudge_made_fails_the_audit():
    """A tower block Blurp knocked again (through the block below) before the
    follow-up: the change is there, but it is Blurp's own."""
    p = SL._rung6(_follows(B2, 20, lambda i: True, 0.1, lambda i: ["self"]))
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0 and p["crit"]["lift"] == 1.0
    assert p["crit"]["audit"] == 0.0


def test_a_drift_on_its_own_clock_has_no_lift_over_its_tempo():
    rng = random.Random(11)
    lam = 0.02
    p_gap = round(1.0 - math.exp(-lam * 30.0), 4)               # its own tempo at the gap (~0.45)
    flips = [rng.random() < p_gap for _ in range(40)]
    p = SL._rung6(_follows(DRIFT, 40, lambda i: flips[i], p_gap, lambda i: ["world"]))
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0
    assert p["crit"]["lift"] < 1.0


def test_the_chat_passes_when_the_person_answers_sent_lines():
    p = SL._rung6(_follows(CHAT, 20, lambda i: i % 10 != 3, 0.15, lambda i: ["alice"]))
    assert p["passed"], p
    assert p["eid"] == CHAT


def test_19_reply_looks_do_not_pass():
    p = SL._rung6(_follows(BALL, 19, lambda i: True, 0.1, lambda i: ["alice"]))
    assert not p["passed"] and p["crit"]["looks"] < 1.0


def test_both_bars_of_the_lift_are_needed():
    # mean(o - p) 0.10 but z under 3: p 0.5, 12 of 20 changed
    a = SL._rung6(_follows(BALL, 20, lambda i: i < 12, 0.5, lambda i: ["alice"]))
    assert a["crit"]["lift"] < 1.0 and not a["passed"]
    # z over 3 but mean(o - p) under 0.10: p 0.01, 2 of 20 changed
    b = SL._rung6(_follows(BALL, 20, lambda i: i < 2, 0.01, lambda i: ["alice"]))
    assert b["z"] >= SL.RUNG6_Z and b["margin"] < SL.RUNG6_MARGIN
    assert b["crit"]["lift"] < 1.0 and not b["passed"]


def test_the_audit_needs_at_least_5_changes_carrying_by():
    recs = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"] if i >= 16 else None)
    p = SL._rung6(recs)                                         # 4 carry `by`
    assert not p["passed"] and p["crit"]["audit"] < 1.0
    recs = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"] if i >= 15 else None)
    assert SL._rung6(recs)["passed"]                            # 5 do


def test_the_audit_allows_one_change_in_ten_with_self_among_the_changers():
    ok = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice", "self"] if i == 4 else ["alice"])
    assert SL._rung6(ok)["passed"]
    bad = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["self"] if i % 5 == 0 else ["alice"])
    assert not SL._rung6(bad)["passed"]


def test_only_answered_reply_looks_outside_audits_count():
    recs = _follows(BALL, 19, lambda i: True, 0.1, lambda i: ["alice"])
    t = T0 + 90_000
    recs += [{"ts": t, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "answer": "changed",
              "by": ["alice"]},                                              # not a reply look
             {"ts": t + 1, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "follow": True,
              "answer": "none"},                                             # no answer
             {"ts": t + 2, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "follow": True,
              "answer": "changed", "by": ["alice"], "audit": True},          # inside an audit
             {"ts": t + 3, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "follow": True,
              "answer": "changed", "by": ["alice"], "audit5": "pause"}]
    assert not SL._rung6(recs)["passed"]


def test_the_window_is_the_last_20_reply_looks():
    old = _follows(BALL, 40, lambda i: False, 0.1, lambda i: None)
    new = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"], t0=T0 + 100_000)
    assert SL._rung6(old + new)["passed"]


def test_the_best_thing_counts():
    recs = _follows(DRIFT, 30, lambda i: i % 2 == 0, 0.5, lambda i: ["world"]) + \
        _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"], t0=T0 + 100_000)
    p = SL._rung6(recs)
    assert p["passed"] and p["eid"] == BALL


def test_no_reply_looks_no_pass():
    p = SL._rung6([])
    assert not p["passed"] and p["skill"] == 0.0
    assert set(p["crit"]) == {"looks", "lift", "audit"}


def test_the_old_pushes_count_for_nothing():
    """The old wiring's chat pushes and answered looks are not reply looks."""
    old = [{"ts": T0 + i, "kind": "push", "to": CHAT} for i in range(20)]
    old += [{"ts": T0 + 100 + i, "kind": "reach", "eid": CHAT, "p": 0.1, "answer": "changed"} for i in range(40)]
    p = SL._rung6(old)
    assert not p["passed"] and p["crit"]["looks"] == 0.0


# ---- the follow-up on a seat (scripted readings) ------------------------------------------------

A, B = NS + "a", NS + "b"


class _Field:
    def __init__(self):
        v = np.random.default_rng(1).normal(size=DIM)
        self.cur = v / np.linalg.norm(v)

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Script:
    """A thing's answers, in order, one per look (`unchanged` after)."""

    def __init__(self):
        self.q = {}

    def __call__(self, srv, ent, now):
        q = self.q.get(ent.eid) or []
        ch = q.pop(0) if q else False
        if ch is None:
            return {"fired": False, "reason": "did not fire"}
        return {"fired": True, "state": {"v": 1}, "changed": bool(ch)}


def _scripted(tmp_path, monkeypatch, isolate=("reach", "nudge", "meet")):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    WA._getter_paused.clear()
    script = _Script()
    monkeypatch.setattr(SL, "_read_entity", script)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(tmp_path / "entities.json"), shared=False)
    srv._models_path = str(tmp_path / "models.json")
    srv.efield = _Field()
    est._srv = srv
    for name in ("a", "b"):
        e = QuantumEntity(eid=NS + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    world(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in (1, 2, 13):
        SL._append(srv, {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9, "ladder": SL.LADDER_V})
    rt = SL._rt(srv)
    rt["mode"] = "on"
    rt["isolate"] = set(isolate)
    srv.script = script
    return srv


@pytest.fixture
def seat(tmp_path, monkeypatch):
    return _scripted(tmp_path, monkeypatch)


def _looks(srv, eid=None):
    return [r for r in SL._read(srv) if r.get("kind") == "reach" and (eid is None or r["eid"] == eid)]


def _nudge(srv, eid, now):
    SL._nudge_once(srv, 0, now, eid, np.zeros(DIM))


def _reach(srv, now, answers=None):
    """One look (whichever thing the loop picks), answering `answers[eid]`."""
    for e, ch in (answers or {}).items():
        srv.script.q[e] = [ch]
    n0 = len(_looks(srv))
    SL._reach_once(srv, srv.entity_store, SL._entity(srv), 0, now)
    got = _looks(srv)
    return got[-1] if len(got) > n0 else None


def _only(srv, eid, now, changed):
    """A look at eid alone (the other thing out of the store)."""
    others = [e for e in (A, B) if e != eid]
    keep = {e: srv.entity_store.docs.pop(e) for e in others}
    try:
        return _reach(srv, now, {eid: changed})
    finally:
        srv.entity_store.docs.update(keep)


def test_the_look_after_a_check_with_no_nudge_since_is_a_reply_look(seat):
    first = _only(seat, A, T0, False)
    assert not first.get("follow")                              # no check before it
    _nudge(seat, A, T0 + 1)
    check = _only(seat, A, T0 + 2, True)
    assert check["nudged"] == 1 and not check.get("follow")
    reply = _only(seat, A, T0 + 2 + SL.FOLLOW_S, True)
    assert reply["nudged"] == 0 and reply["follow"] is True
    after = _only(seat, A, T0 + 200, False)
    assert not after.get("follow")                              # its previous look was the reply look


def test_a_look_after_a_nudge_since_the_check_is_a_check_not_a_reply(seat):
    _nudge(seat, A, T0)
    _only(seat, A, T0 + 1, True)
    _nudge(seat, A, T0 + 5)
    r = _only(seat, A, T0 + 1 + SL.FOLLOW_S, True)
    assert r["nudged"] == 1 and not r.get("follow")


def test_an_unanswered_check_is_followed_by_no_reply_look(seat):
    _nudge(seat, A, T0)
    chk = _only(seat, A, T0 + 1, None)                          # the reading did not fire: no answer
    assert chk["nudged"] == 1 and chk["answer"] == "none"
    r = _only(seat, A, T0 + 1 + SL.FOLLOW_S, True)
    assert r["nudged"] == 0 and not r.get("follow")


def test_a_checked_thing_waits_for_its_follow_up_and_then_comes_first(seat):
    _nudge(seat, A, T0)
    _only(seat, A, T0 + 1, True)                                # the check
    for i in range(6):                                          # before FOLLOW_S: never A
        r = _reach(seat, T0 + 2 + i * 4, {A: True, B: False})
        assert r is not None and r["eid"] == B
    r = _reach(seat, T0 + 1 + SL.FOLLOW_S, {A: True, B: False})   # due: the follow-up comes first
    assert r["eid"] == A and r["follow"] is True


def test_without_meet_practised_there_is_no_hold_and_no_schedule(tmp_path, monkeypatch):
    srv = _scripted(tmp_path, monkeypatch, isolate=("reach", "nudge"))
    _nudge(srv, A, T0)
    _only(srv, A, T0 + 1, True)
    seen = {(_reach(srv, T0 + 2 + i, {A: True, B: False}) or {}).get("eid") for i in range(40)}
    assert A in seen                                            # looked at before FOLLOW_S


def test_a_reply_look_is_kept_out_of_the_tempo_and_the_rate_fit(seat):
    _only(seat, A, T0, False)
    _only(seat, A, T0 + 10, False)
    before = json.loads(json.dumps(SL._tempo(seat)[A]))
    _nudge(seat, A, T0 + 15)
    _only(seat, A, T0 + 20, True)                               # the check
    r = _only(seat, A, T0 + 20 + SL.FOLLOW_S, True)             # the reply look
    assert r["follow"] is True
    after = SL._tempo(seat)[A]
    assert {k: v for k, v in after.items() if k != "last"} == {k: v for k, v in before.items() if k != "last"}
    assert after["last"] == T0 + 20 + SL.FOLLOW_S
    assert not SL._fittable(r)
    assert "timed" not in r and "steer" not in r and "watch5" not in r


def test_a_reply_looks_p_is_its_tempo_at_the_gap(seat, monkeypatch):
    monkeypatch.setattr(SL, "_lam_of", lambda srv, recs, eid: 0.01)
    _nudge(seat, A, T0)
    _only(seat, A, T0 + 1, True)
    r = _only(seat, A, T0 + 1 + SL.FOLLOW_S, True)
    assert r["follow"] is True
    assert r["p"] == pytest.approx(1.0 - math.exp(-0.01 * SL.FOLLOW_S), abs=1e-4)


def test_the_follow_up_survives_a_restart(seat):
    _nudge(seat, A, T0)
    _only(seat, A, T0 + 1, True)
    SL._RT.pop(id(seat), None)
    SL.bind(seat)
    world(seat)
    SL._rt(seat)["mode"] = "on"
    SL._rt(seat)["isolate"] = {"reach", "nudge", "meet"}
    assert _reach(seat, T0 + 10, {A: True, B: False})["eid"] == B
    r = _reach(seat, T0 + 1 + SL.FOLLOW_S, {A: True, B: False})
    assert r["eid"] == A and r["follow"] is True


# ---- a changed look at the chat thing carries the person ----------------------------------------

def test_a_changed_look_at_the_chat_thing_carries_the_person(tmp_path, monkeypatch):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    monkeypatch.setattr(SL, "_read_entity", lambda srv, ent, now: {"fired": True, "state": {"heard_n": 1},
                                                                    "changed": True})
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(tmp_path / "entities.json"), shared=False)
    srv._models_path = str(tmp_path / "models.json")
    srv.efield = _Field()
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    CIO.entity(srv, "alice")
    world(srv, CHAT)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    SL._append(srv, {"ts": 2.0, "kind": "rung", "rung": 2, "skill": 0.9})
    SL._rt(srv)["mode"] = "on"
    SL._rt(srv)["isolate"] = {"reach"}
    r = _reach(srv, T0)
    assert r["eid"] == CHAT and r["answer"] == "changed"
    assert r["by"] == ["alice"]


# ---- the real toys: a ball, a tower ---------------------------------------------------------------

@pytest.fixture
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    yield tmp_path / "data"


def _toy_seat(toys):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.efield = _Field()
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in (1, 2, 13):
        SL._append(srv, {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9, "ladder": SL.LADDER_V})
    rt = SL._rt(srv)
    rt["mode"] = "on"
    rt["isolate"] = {"reach", "nudge", "meet"}
    for k in toys:
        assert TB.add(srv, k, locked_ok=True)["ok"]
    world(srv)
    return srv


def _play(srv, eid, rounds, back=None, t0=T0):
    """Each round: Blurp nudges `eid`, checks it a second later, the person
    (maybe) plays back `back(i)` s after the nudge, and the loop looks again
    until the follow-up is on the record. -> the follow-ups."""
    out = []
    for i in range(rounds):
        t = t0 + i * 400.0
        _nudge(srv, eid, t)
        assert _reach(srv, t + 1)["eid"] == eid                   # the check
        d = back(i) if back else None
        if d is not None:
            assert TB.poke(TB.BALL, "roll", by="alice", now=t + d)["ok"]
        assert _reach(srv, t + 1 + SL.FOLLOW_S / 2) is None      # held: nothing else to look at
        r = _reach(srv, t + 1 + SL.FOLLOW_S)
        assert r is not None and r["eid"] == eid and r["follow"] is True, r
        out.append(r)
    return out


def test_a_ball_the_person_rolls_back_5_to_20_s_after_its_roll_passes(_data):
    srv = _toy_seat(("ball",))
    rng = random.Random(4)
    ups = _play(srv, BALL, SL.RUNG6_LOOKS, back=lambda i: rng.uniform(5.0, 20.0))
    assert all(r["answer"] == "changed" and r["by"] == ["alice"] for r in ups)
    p = SL._rung6(SL._read(srv))
    assert p["passed"], p
    assert p["eid"] == BALL


def test_a_ball_nobody_touches_does_not_pass_on_a_seat(_data):
    srv = _toy_seat(("ball",))
    ups = _play(srv, BALL, SL.RUNG6_LOOKS)
    assert all(r["answer"] == "unchanged" for r in ups)
    assert not SL._rung6(SL._read(srv))["passed"]


def test_a_tower_whose_blocks_fall_together_does_not_pass(_data):
    """Its nudge topples the block and those above at once: the check sees
    it, the follow-up finds nothing new."""
    srv = _toy_seat(("tower",))
    for e in list(srv.entity_store.docs):
        if e.startswith(NS + "toy:tower") and e != B1:
            srv.entity_store.docs.pop(e)                         # look at the bottom block only
    ups = _play(srv, B1, SL.RUNG6_LOOKS)
    assert all(r["answer"] == "unchanged" for r in ups)
    assert not SL._rung6(SL._read(srv))["passed"]


# ---- the pass line ---------------------------------------------------------------------------

def _record_seat(record):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    p = SL.record_path(srv)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in record))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _lines(srv):
    return [json.loads(ln) for ln in SL.record_path(srv).read_text().splitlines() if ln.strip()]


def test_the_pass_line_carries_ladder_3(_data):
    record = [{"ts": T0 - 100, "kind": "birth", "by": "operator"},
              {"ts": T0 - 90, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9},
              {"ts": T0 - 89, "kind": "achieve", "id": "reach", "rung": 2, "skill": 0.9},
              {"ts": T0 - 88, "kind": "achieve", "id": "nudge", "rung": 13, "skill": 0.9, "ladder": 3},
              {"ts": T0 - 80, "kind": "accept_migrated", "ids": ["feel", "reach", "nudge"]}] + \
        _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"])
    srv = _record_seat(record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    line = next(r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") == "meet")
    assert line["ladder"] == 3 and line["rung"] == 6 and line["eid"] == BALL
    assert "meet" in SL._earned(SL._read(srv))


def test_no_pass_before_first_reach_is_accepted(_data):
    record = [{"ts": T0 - 100, "kind": "birth", "by": "operator"},
              {"ts": T0 - 90, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9},
              {"ts": T0 - 89, "kind": "achieve", "id": "reach", "rung": 2, "skill": 0.9},
              {"ts": T0 - 80, "kind": "accept_migrated", "ids": ["feel", "reach"]}] + \
        _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"])
    srv = _record_seat(record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    assert not [r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") == "meet"]


# ---- the new unlock order ------------------------------------------------------------------

def test_the_unlock_table_is_by_skill():
    assert TU.UNLOCKS == {"tower": "feel", "slow": "reach", "fast": "kinds", "burst": "patience",
                          "die": "shift", "drift": "shift", "ball": "nudge", "mirror": "nudge",
                          "blocks": "mirror"}
    assert set(TU.DROPPED) == {"body", "rattle"}
    assert "body" not in TU.UNLOCKS and "rattle" not in TU.UNLOCKS


def _unlock_seat():
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    SL._append(srv, {"ts": 1000.0, "kind": "birth", "by": "operator"})
    SL._append(srv, {"ts": 1001.0, "kind": "accept_migrated", "ids": []})
    return srv


def _earn(srv, i, ts, accept=True):
    SL._append(srv, {"ts": ts, "kind": "achieve", "id": i, "rung": SL.ACHIEVEMENTS[i]["rung"], "skill": 0.9,
                     "ladder": SL.LADDER_V})
    if accept:
        SL._append(srv, {"ts": ts + 1, "kind": "accept", "id": i, "by": "operator"})


def _unlocks(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "unlock"]


def test_a_new_blurp_gets_the_tower_when_feel_is_accepted(_data):
    srv = _unlock_seat()
    TU.sync(srv, now=1002.0)
    assert _unlocks(srv) == [] and not TB.list_toys()           # birth unlocks nothing now
    _earn(srv, "feel", 2000.0, accept=False)
    TU.sync(srv, now=2001.0)
    assert _unlocks(srv) == []                                  # earned, not yet accepted
    SL._append(srv, {"ts": 2100.0, "kind": "accept", "id": "feel", "by": "operator"})
    TU.sync(srv, now=2101.0)
    rows = _unlocks(srv)
    assert [(r["id"], r["toys"]) for r in rows] == [("feel", ["tower"])]
    assert {t["kind"] for t in TB.list_toys()} == {"tower"}


def test_the_unlock_lines_follow_the_table(_data):
    srv = _unlock_seat()
    t = 2000.0
    for i in ("feel", "reach", "kinds", "patience", "shift", "nudge", "mirror"):
        _earn(srv, i, t)
        TU.sync(srv, now=t + 2)
        t += 100.0
    got = [(r["id"], r["rung"], r["toys"]) for r in _unlocks(srv)]
    assert got == [("feel", 1, ["tower"]), ("reach", 2, ["slow"]), ("kinds", 3, ["fast"]),
                   ("patience", 4, ["burst"]), ("shift", 5, ["die", "drift"]),
                   ("nudge", 13, ["ball", "mirror"]), ("mirror", 9, ["blocks"])]
    kinds = {t["kind"] for t in TB.list_toys()}
    assert {"tower", "slow", "fast", "burst", "die", "drift", "ball", "mirror"} <= kinds
    assert "body" not in kinds                                  # dropped: never provisioned


def test_old_unlock_lines_stay_valid_and_no_toy_is_taken_away(_data):
    srv = _unlock_seat()
    assert TB.add(srv, "body", locked_ok=True)["ok"]           # a seat that has a body keeps it
    SL._append(srv, {"ts": 1500.0, "kind": "unlock", "rung": 6, "id": "meet", "toys": ["ball", "mirror"]})
    _earn(srv, "feel", 2000.0)
    _earn(srv, "nudge", 2100.0)
    TU.sync(srv, now=2200.0)
    rows = _unlocks(srv)
    assert not any("ball" in r["toys"] or "mirror" in r["toys"] for r in rows[1:])   # named once, ever
    assert "body" in {t["kind"] for t in TB.list_toys()}
    assert not TU.grandfathered(SL._read(srv), "body")


def test_the_dock_lists_the_next_locked_toys_by_skill(_data):
    srv = _unlock_seat()
    rows = TU.locked_rows(SL._read(srv))
    assert [r["kind"] for r in rows] == ["tower", "slow"]
    assert rows[0]["unlock_id"] == "feel" and rows[0]["rung_name"] == "feel itself"


# ---- the sheet -------------------------------------------------------------------------------

def test_the_sheet_says_meets_three_stages():
    recs = _follows(BALL, 20, lambda i: True, 0.1, lambda i: ["alice"])
    progs = {6: SL._rung6(recs)}
    crits = {c["key"]: c for c in SS._criteria(6, recs, progs)}
    assert list(crits) == ["looks", "lift", "audit"]
    assert crits["looks"]["text"] == "20/20 reply looks at one thing"
    assert "reply lift over its tempo +0.90" in crits["lift"]["text"]
    assert crits["audit"]["text"].startswith("not its own: 20/20")
    assert SS.SKILL_LABEL[6] == "reply lift over its tempo"
    assert "reply looks at one thing" in SS._needs()[6]
