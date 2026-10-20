"""#258: the ladder flips -- rung 7 is TAKE TURNS, rung 8 understand an other.

Self pushed a line to the other on a timer (every MEET_EVERY cycles), not
when the other was there, so the other's replies looked like a coin from
self's side and "predict the other" (now rung 8) had nothing to learn. In
infants serve-and-return turn-taking (~2-6 months) comes before modelling
the other.

Rung 7: self LEARNS when to push. After each push it picks the moment it
will wait for -- a bin of `spoke_age` (how long since the other last
spoke: 0-2 min, 2-10, 10-30, 30+ or never) -- by Thompson sampling on the
reply rate each bin earned (a push's reward: did the other speak after it,
by the reply check), with a floor of exploration, and pushes when the
other's spoke_age is in that bin (or when the wait runs out). Nothing in
the code prefers "right after they speak": an other who answers late
teaches it to wait. The cap is today's: never two pushes closer than
MEET_EVERY cycles. Only timing is learned; the words and the model are
untouched.

The test can fail. Over the last RUNG7_PUSHES pushes with a reply check:
(a) contingency -- the share of pushes in self's learned window (the bin it
believed best at the push) beats the share of the waiting time the other
spent in that window (a push at a shuffled moment), by a z-test and a
margin; (b) payoff -- each push's outcome minus what a timing-blind push in
that interval would have expected (the bins' reply rates as known then,
weighted by the time spent in each), by a t-test and a margin. Both
prequential: a coin-flip other or a timer can't pass.
"""
import math
import os
import random
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
from membrane import self_loop as SL  # noqa: E402
from tests._self_cap import cap  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

RESP = MEMBRANE_NS + "Component:chat:in:operator"
NB = len(SL.TURN_BINS) + 1


# ---- synthetic others, driven at the cycle's pace -----------------------------------

class _Other:
    """An other with a presence rhythm (here ~60 min, away ~90 min) who
    speaks on their own while here. `reply(age)` is the chance they answer
    a push by the reply check, given how long since they last spoke."""

    def __init__(self, kind, seed):
        self.kind, self.rng = kind, random.Random(seed)
        self.here = self.rng.random() < 0.5
        self.last = None

    def p_reply(self, age):
        a = 1e9 if age is None else age
        if self.kind == "early":          # turn-taking: engaged right after they spoke
            return (0.08 + 0.8 * math.exp(-a / 180.0)) if self.here else 0.03
        if self.kind == "late":           # answers pushes that come 10-30 min after they spoke
            return (0.8 if 600.0 <= a < 1800.0 else 0.1) if self.here else 0.03
        if self.kind == "coin":           # timing means nothing
            return 0.5
        raise ValueError(self.kind)

    def step(self, t):
        dt = SL.PACE_S
        if self.here and self.rng.random() < dt / 3600.0:
            self.here = False
        elif not self.here and self.rng.random() < dt / 5400.0:
            self.here = True
        rate = (1 / 1200.0 if self.kind == "late" else 1 / 240.0) if self.here else 0.0
        if self.rng.random() < rate * dt:
            self.last = t


def _drive(kind, pusher, hours, seed):
    """A world at the cycle's pace: self's pushes and the other's replies.
    `pusher`: "policy" (rung 7's learned timing, the real functions), "timer"
    (every MEET_EVERY cycles), "random" (a random delay). Returns the rows
    rung 7's test reads: bin, best, dwell, replied."""
    o = _Other(kind, seed)
    prng = random.Random(seed * 7 + 1)
    rows, pending, aim, best = [], None, None, None
    since, dwell = SL.MEET_EVERY, [0] * NB
    for c in range(int(hours * 3600 / SL.PACE_S)):
        t = c * SL.PACE_S
        o.step(t)
        if pending is not None and t >= pending["ts"] + SL.RUNG8_CHECK_S:
            pending["replied"] = pending.pop("_r")
            pending = None
        since += 1
        if since < SL.MEET_EVERY:
            continue
        age = (t - o.last) if o.last is not None else None
        b = SL._turn_bin(age)
        dwell[b] += 1
        if pusher == "policy":
            if aim is None:
                aim, best = SL._turn_choose(rows, random.Random(f"{seed}:{c}"))
            go = b == aim or since >= SL.MEET_MAX
        elif pusher == "timer":
            go, best = True, 0
        else:
            go, best = prng.random() < 1.0 / SL.MEET_EVERY, 0
        if not go:
            continue
        r = o.rng.random() < o.p_reply(age)
        row = {"ts": t, "bin": b, "best": best, "dwell": dwell, "_r": r, "replied": None}
        rows.append(row)
        if r:
            o.last = t + o.rng.uniform(5.0, SL.RUNG8_CHECK_S)
        pending, since, aim, dwell = row, 0, None, [0] * NB
    return [r for r in rows if r["replied"] is not None]


def _ever_passes(rows):
    """Does the test pass at ANY push of the run (the live test reads every
    window, not just the last)?"""
    d = SL._turn_deltas(rows)
    for j in range(SL.RUNG7_PUSHES, len(d) + 1):
        if SL._turn_window(d[j - SL.RUNG7_PUSHES:j])["passed"]:
            return True
    return False


# ---- 1. the policy learns the window that gets replies, either way --------------------

def _share(rows, b, last=100):
    tail = rows[-last:]
    return sum(r["bin"] == b for r in tail) / len(tail)


def _rate(rows, last=100):
    tail = rows[-last:]
    return sum(r["replied"] for r in tail) / len(tail)


@pytest.mark.parametrize("seed", [1, 2])
def test_the_policy_learns_to_push_soon_after_an_early_answerer_speaks(seed):
    rows = _drive("early", "policy", 72, seed)
    k, n = SL._turn_post(rows)
    assert int(np.argmax((k + 1) / (n + 2))) == 0             # its best window: 0-2 min after they spoke
    assert _share(rows, 0) > 0.5
    assert _share(rows, 0) > 2 * _share(_drive("early", "timer", 72, seed), 0)


@pytest.mark.parametrize("seed", [1, 2])
def test_the_policy_learns_to_wait_for_a_late_answerer(seed):
    # the opposite rhythm: this other answers pushes 10-30 min after they spoke
    rows = _drive("late", "policy", 96, seed)
    k, n = SL._turn_post(rows)
    assert int(np.argmax((k + 1) / (n + 2))) == 2
    assert _share(rows, 2) > 0.4
    assert _share(rows, 0) < 0.2
    # a timer lands 10-30 min after this seldom-speaker often by itself (~0.25-0.35);
    # the policy lands there more, and it pays: more of its pushes get answered
    timer = _drive("late", "timer", 96, seed)
    assert _share(rows, 2) > _share(timer, 2) + 0.1
    assert _rate(rows) > _rate(timer)


# ---- 2. nothing is hard-coded ---------------------------------------------------------

def test_with_no_evidence_every_window_is_equally_likely():
    rng = random.Random(3)
    aims = [SL._turn_choose([], rng)[0] for _ in range(4000)]
    for b in range(NB):
        assert abs(aims.count(b) / 4000 - 1 / NB) < 0.03
    assert SL._turn_choose([], rng)[1] is None                 # no evidence: no window believed best


def test_the_choice_is_symmetric_in_the_bins():
    # whatever bin earned the replies is the one favoured: relabel the bins and
    # the choice follows the evidence, not a built-in preference for "fresh"
    def rows_for(good):
        out = []
        for i in range(60):
            b = i % NB
            out.append({"bin": b, "best": None, "dwell": [1] * NB, "replied": (b == good) == (i % 5 != 0)})
        return out
    for good in range(NB):
        rng = random.Random(good)
        aims = [SL._turn_choose(rows_for(good), rng)[0] for _ in range(2000)]
        assert max(range(NB), key=aims.count) == good
        assert aims.count(good) / 2000 > 0.75
        assert SL._turn_choose(rows_for(good), rng)[1] == good


def test_exploration_keeps_a_floor():
    rows = [{"bin": 0, "best": 0, "dwell": [1] * NB, "replied": True} for _ in range(SL.TURN_MEMORY)]
    rows += [{"bin": b, "best": 0, "dwell": [1] * NB, "replied": False} for b in range(1, NB) for _ in range(30)]
    rng = random.Random(5)
    aims = [SL._turn_choose(rows, rng)[0] for _ in range(4000)]
    for b in range(1, NB):                                      # the losing windows are still measured
        assert aims.count(b) / 4000 >= 0.5 * SL.TURN_EXPLORE / NB


def test_the_source_has_no_scripted_reply_after_they_speak():
    import inspect
    src = inspect.getsource(SL._meet) + inspect.getsource(SL._turn_push) + inspect.getsource(SL._turn_choose)
    assert "_turn_push" in inspect.getsource(SL._meet) and "_turn_choose" in inspect.getsource(SL._turn_push)
    # the push waits for the bin Thompson drew (or the cap), never a constant bin
    for bad in ("== 0 and", "aim = 0", "bin == 0", "b == 0"):
        assert bad not in src


# ---- 3. the test: passable by turn-taking, not by a coin or a timer -------------------

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_turn_taking_other_lets_the_policy_pass(seed):
    rows = _drive("early", "policy", 96, seed)
    assert _ever_passes(rows)


@pytest.mark.parametrize("seed", range(6))
def test_a_coin_flip_other_never_passes(seed):
    assert not _ever_passes(_drive("coin", "policy", 168, seed))


@pytest.mark.parametrize("seed", range(3))
def test_a_timer_never_passes_even_with_a_turn_taker(seed):
    assert not _ever_passes(_drive("early", "timer", 96, seed))
    assert not _ever_passes(_drive("early", "random", 96, seed))


def test_the_score_parts():
    # in-window pushes against the shuffled share, payoff against timing-blind
    rows = _drive("early", "policy", 96, 1)
    w = SL._turn_window(SL._turn_deltas(rows)[-SL.RUNG7_PUSHES:])
    assert w["n"] == SL.RUNG7_PUSHES
    assert w["share"] > w["chance"] + SL.RUNG7_CONT_MARGIN
    assert w["z"] >= SL.RUNG7_CONT_Z
    assert w["payoff"] >= SL.RUNG7_PAY_MARGIN
    assert w["reply_rate"] == pytest.approx(w["blind_rate"] + w["payoff"], abs=1e-9)


def test_a_push_with_no_waiting_has_no_contingency():
    # a timer's push has nothing to choose: its window share IS the shuffled share
    rows = [{"bin": i % NB, "best": i % NB, "dwell": [int(b == i % NB) for b in range(NB)], "replied": True}
            for i in range(SL.RUNG7_PUSHES)]
    w = SL._turn_window(SL._turn_deltas(rows))
    assert w["z"] == 0.0 and w["share"] == w["chance"] == 1.0
    assert not w["passed"]


def test_the_bar_and_window_follow_the_replay():
    # experiments/rung7_turns: 100 pushes, contingency z >= 3 and +0.10 over
    # chance, payoff t >= 4 (a coin-flip other's best window over a week
    # reaches q99 ~4.1) and +0.05
    assert SL.RUNG7_PUSHES == 100
    assert SL.RUNG7_CONT_Z >= 3.0 and SL.RUNG7_CONT_MARGIN >= 0.1
    assert SL.RUNG7_PAY_Z >= 4.0 and SL.RUNG7_PAY_MARGIN > 0.0


# ---- 4. on a seat: the push, its cap, the reply check ---------------------------------

class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=16)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _seat(passed=(1, 2, 3, 4, 5, 6), limit=7):
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
    cap(srv, 1)
    for r in passed:
        line = {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9}
        if r >= 7:
            line["ladder"] = SL.LADDER_V
        SL._append(srv, line)
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    cap(srv, limit)
    return srv


class _Talker:
    """The other on a seat: speaks every `every` seconds (chat_io.heard), and
    answers a push within 60 s when it came under 2 min after they spoke."""

    def __init__(self, srv, every=900.0):
        self.srv, self.every, self.t, self.next = srv, every, 0.0, 0.0
        self.replies = []

    def tick(self, t):
        self.t = t
        due = [r for r in self.replies if r <= t]
        if t >= self.next or due:
            self.replies = [r for r in self.replies if r > t]
            ent = CIO.entity_in(self.srv, CIO.OPERATOR)
            ent.properties["heard_n"] = int(ent.properties.get("heard_n") or 0) + 1
            ent.properties["heard_ts"] = t
            if t >= self.next:
                self.next = t + self.every

    def push(self, srv, text, person=""):
        ent = CIO.entity_in(srv, CIO.OPERATOR)
        h = ent.properties.get("heard_ts")
        if h is not None and self.t - h < 120.0:
            self.replies.append(self.t + 60.0)

    def read(self, srv, ent, now):
        st = (ent.properties or {}).get("heard_n")
        last = getattr(self, "_seen", {}).get(ent.eid)
        self._seen = {**getattr(self, "_seen", {}), ent.eid: st}
        return {"fired": True, "state": {"n": st}, "changed": st is not None and st != last}


def _run(srv, talker, cycles, t0=10_000.0, monkeypatch=None):
    monkeypatch.setattr(SL, "_read_entity", talker.read)
    monkeypatch.setattr(SL, "_push_chat", talker.push)
    monkeypatch.setattr(SL, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model at rung 7")))
    for i in range(cycles):
        t = t0 + i * SL.PACE_S
        talker.tick(t)
        SL.cycle(srv, now=t)


def _pushes(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "push"]


def test_pushes_carry_the_policy_and_hold_the_cap(monkeypatch):
    srv = _seat()
    _run(srv, _Talker(srv), 1500, monkeypatch=monkeypatch)
    ps = _pushes(srv)
    assert len(ps) >= 6
    for p in ps:
        assert p["bin"] in range(NB) and p["aim"] in range(NB)
        assert p["why"] in ("aim", "overdue")
        assert p["why"] == "overdue" or p["bin"] == p["aim"]
        assert len(p["dwell"]) == NB and sum(p["dwell"]) >= 1
        assert not p.get("asked")                                   # the model is rung 8's, not rung 7's
    ns = [p["n"] for p in ps]
    assert all(b - a >= SL.MEET_EVERY for a, b in zip(ns, ns[1:]))  # never more often than the timer did
    assert all(b - a <= SL.MEET_MAX for a, b in zip(ns, ns[1:]))    # and never silent past the wait's cap
    hours = 1500 * SL.PACE_S / 3600.0
    assert len(ps) <= hours * 3600.0 / (SL.MEET_EVERY * SL.PACE_S) + 1


def test_the_reply_check_records_whether_they_spoke_after_the_push(monkeypatch):
    srv = _seat()
    _run(srv, _Talker(srv, every=300.0), 1500, monkeypatch=monkeypatch)
    recs = SL._read(srv)
    checks = [r for r in recs if r.get("kind") == "reach" and r.get("reply")]
    assert checks and all(isinstance(r.get("replied"), bool) for r in checks)
    for c in checks:
        push = next(p for p in reversed(recs) if p.get("kind") == "push" and p["ts"] < c["ts"])
        if not c["replied"]:
            continue
        assert c["ts"] - push["ts"] >= SL.RUNG8_CHECK_S


def test_a_reply_check_does_not_count_lines_from_before_the_push(monkeypatch):
    # they spoke 30 s before the push and never after: the look reads `changed`
    # (self had not looked since), but that is not a reply
    srv = _seat()
    push = {"ts": 1000.0, "kind": "push", "n": 31, "to": RESP, "bin": 0, "aim": 0, "best": None, "dwell": [1, 0, 0, 0]}
    ent = CIO.entity_in(srv, CIO.OPERATOR)
    ent.properties["heard_ts"] = 970.0
    assert SL._replied(srv, push, 1130.0, "changed") is False
    ent.properties["heard_ts"] = 1050.0
    assert SL._replied(srv, push, 1130.0, "unchanged") is True
    ent.properties.pop("heard_ts")
    assert SL._replied(srv, push, 1130.0, "changed") is True        # no heard_ts: the look's answer


def test_rung_seven_reads_the_record(monkeypatch):
    srv = _seat()
    _run(srv, _Talker(srv, every=600.0), 1500, monkeypatch=monkeypatch)
    rows = SL._turn_rows(SL._read(srv), RESP)
    assert rows and all(r["replied"] in (True, False) for r in rows)
    out = SL._rung7(SL._read(srv))
    assert set(out["crit"]) == {"pushes", "contingency", "payoff"}
    assert out["crit"]["pushes"] == pytest.approx(min(1.0, len(rows) / SL.RUNG7_PUSHES))
    assert out["passed"] is False                                    # far too few pushes yet


def test_timer_pushes_never_count_for_rung_seven():
    recs = []
    for i in range(150):
        t = 1000.0 + i * 300.0
        recs.append({"ts": t, "kind": "push", "n": 1 + 30 * i, "to": RESP, "spoke_age": 30.0})
        recs.append({"ts": t + 120.0, "kind": "reach", "eid": RESP, "reply": True, "answer": "changed",
                     "replied": True})
    assert SL._turn_rows(recs, RESP) == []
    assert SL._rung7(recs)["passed"] is False


def test_before_rung_six_passes_the_timer_still_paces_rung_six(monkeypatch):
    srv = _seat(passed=(1, 2, 3, 4, 5), limit=6)
    _run(srv, _Talker(srv), 200, monkeypatch=monkeypatch)
    ps = _pushes(srv)
    assert ps and all(p["n"] % SL.MEET_EVERY == 1 for p in ps)       # rung 6 unchanged
    assert all("aim" not in p and "spoke_age" not in p for p in ps)


# ---- 5. the record: the voided pass, the ladder's numbering ---------------------------

def test_the_voided_old_rung_seven_pass_stays_void_and_counts_for_neither_rung():
    srv = _seat()
    old = {"ts": 100.0, "kind": "rung", "rung": 7, "skill": 0.2207, "eid": RESP}
    SL._append(srv, old)
    SL._append(srv, {"ts": 200.0, "kind": "rung_void", "rung": 7, "voids_ts": 100.0,
                     "why": "#254: rung 7 graded the LLM's guess, not self's"})
    SL._append(srv, {"ts": 200.0, "kind": "rung7_restart", "issue": 254})
    done = SL._passed(SL._read(srv))
    assert 7 not in done and 8 not in done
    st = SL.status(srv)
    assert st["rung"] == 6 and st["next"] == ["turns", "expect", "mirror"] and st["rung_name"] == "meet an other"   # #295: siblings (#312: mirror)
    assert next(r for r in st["ladder"] if r["rung"] == 7)["name"] == "time its turn"     # #300


def test_a_rung_line_from_before_the_flip_does_not_count_above_six():
    # a seat that passed the OLD rung 7 (understand an other) and was never voided:
    # that pass is not a pass of the new rung 7 (take turns), nor of rung 8
    srv = _seat()
    SL._append(srv, {"ts": 300.0, "kind": "rung", "rung": 7, "skill": 0.1})
    assert 7 not in SL._passed(SL._read(srv))
    SL._append(srv, {"ts": 400.0, "kind": "rung", "rung": 7, "skill": 0.1, "ladder": SL.LADDER_V})
    assert 7 in SL._passed(SL._read(srv))


def test_a_new_pass_is_written_as_its_achievement(monkeypatch):
    # #295: a pass is an `achieve` line naming the achievement (no ladder numbering to tag)
    srv = _seat()
    monkeypatch.setattr(SL, "_rung7", lambda recs, tempo=None: {"crit": {"pushes": 1.0}, "skill": 0.2,
                                                               "passed": True, "eid": RESP})
    monkeypatch.setitem(SL.PROGRESS, 7, SL._rung7)
    SL._open_rungs(srv)
    line = [r for r in SL._read(srv) if r.get("kind") == "achieve" and r.get("rung") == 7][-1]
    assert line["id"] == "turns"
    assert 7 in SL._passed(SL._read(srv))


def test_the_numbering():
    assert [SL.RUNG_NAMES[n] for n in range(7)] == [
        "sleep and wake", "feel itself", "first reach", "tell things apart", "patience",
        "notice the world changed", "meet an other"]                  # rungs 0-6 unchanged
    assert SL.RUNG_NAMES[7] == "time its turn" and SL.RUNG_NAMES[8] == "expect a reply"   # #300: named for their tests
    assert SL.RUNG_NAMES[9] == "recognize itself"
    assert sorted(SL.LADDER) == list(range(12))                 # #311: speak; #312: recognize itself; #320: change
    assert SL.PROGRESS[7] is SL._rung7 and SL.PROGRESS[8] is SL._rung8
    for n, fn in ((1, SL._rung1), (2, SL._rung2), (3, SL._rung3), (4, SL._rung4), (5, SL._rung5), (6, SL._rung6)):
        assert SL.PROGRESS[n] is fn
    # rung 8 is the #256 predictor, its constants moved up unchanged
    assert SL.RUNG8_LOOKS == 100 and SL.RUNG8_MARGIN == 0.08
    assert SL.SKILL_PASS[8] == SL.RUNG8_MARGIN and SL.SKILL_PASS[7] == SL.RUNG7_PAY_MARGIN
    assert not hasattr(SL, "RUNG7_LOOKS") and not hasattr(SL, "RUNG7_MARGIN")


def test_rung_eight_needs_meet_not_rung_seven():
    # #295, the skill tree: understand an other (8) and take turns (7) are siblings under meet (6)
    srv = _seat(limit=8)
    assert SL._available(srv, 7) and SL._available(srv, 8)
    srv = _seat(passed=(1, 2, 3, 4, 5), limit=8)
    assert not SL._available(srv, 7) and not SL._available(srv, 8)


# ---- 6. status and the sheet ---------------------------------------------------------

def test_status_shows_both_blocks(monkeypatch):
    srv = _seat()
    _run(srv, _Talker(srv, every=600.0), 1500, monkeypatch=monkeypatch)
    st = SL.status(srv)
    r7 = st["rung7"]
    assert [b["bin"] for b in r7["bins"]] == list(SL.TURN_NAMES)
    assert sum(b["pushes"] for b in r7["bins"]) == r7["checked"]
    for k in ("pushes", "checked", "best", "share", "chance", "z", "payoff", "payoff_z", "reply_rate", "blind_rate"):
        assert k in r7
    r8 = st["rung8"]
    for k in ("asked", "checked", "paired", "brier_self", "brier_mix", "brier_rate", "advisor_weight"):
        assert k in r8
    assert st["skill_rung"] == 7 and st["skill_pass"] == SL.RUNG7_PAY_MARGIN


def test_the_sheet_follows_the_numbering(monkeypatch):
    srv = _seat()
    _run(srv, _Talker(srv, every=600.0), 400, monkeypatch=monkeypatch)
    s = SS.sheet(srv, fresh=True)
    # #300: the attributes are traits, not the skills' scores; each skill is on the card while learned
    sk = {k["id"]: k for k in s["skills"]}
    assert list(sk) == ["turns"] and sk["turns"]["name"] == "time its turn" and sk["turns"]["trend"]["rung"] == 7
    assert sk["turns"]["trend"]["pass"] == SL.RUNG7_PAY_MARGIN
    assert {n["id"]: n for n in s["tree"]}["expect"]["name"] == "expect a reply"   # the limit (7) holds it off the card
    assert s["quest"]["rung"] == 7 and s["quest"]["name"] == "time its turn"
    assert [c["key"] for c in s["quest"]["criteria"]] == ["pushes", "contingency", "payoff"]
    assert "timed lines" in s["quest"]["criteria"][0]["text"]
    months7, what7 = SS.CHILD[7]
    assert 2.0 <= months7 <= 6.0 and "turn" in what7
    assert SS.CHILD[8][0] == 2.5 and "still-face" in SS.CHILD[8][1]   # #300: expects a caregiver's reply (~2-3 mo)
    rows = {r["rung"]: r for r in s["growth"]["rows"]}
    assert rows[7]["name"] == "time its turn" and rows[8]["name"] == "expect a reply"   # #300
    assert rows[7]["built"] and rows[8]["built"] and rows[9]["built"] and rows[10]["built"]   # #312, #320


# ---- 7. rung 8 scores the same outcome: a pre-push line is not a reply ----------------

def test_rung_eight_scores_replied_not_changed():
    # each check read `changed` (self had not looked since their line), but
    # mostly the line came BEFORE the push: no reply, for rung 8 as for rung 7
    recs = []
    for i in range(120):
        t = 1000.0 + i * 300.0
        recs.append({"ts": t, "kind": "push", "to": RESP, "spoke_age": 20.0})
        recs.append({"ts": t + 120.0, "kind": "reach", "eid": RESP, "reply": True, "p": 0.5,
                     "answer": "changed", "replied": i % 3 == 0})
    rows = SL._rung8_rows(recs, RESP)
    assert [r["o"] for r in rows] == [1.0 if i % 3 == 0 else 0.0 for i in range(120)]
    assert SL._base_rate(recs, RESP)[1] == 40                       # not 120: `changed` is not the outcome


def test_a_check_from_before_the_flip_counts_for_neither_rung():
    legacy = {"ts": 1120.0, "kind": "reach", "eid": RESP, "reply": True, "answer": "changed"}
    assert SL._reply_o(legacy) is None
    recs = [{"ts": 1000.0, "kind": "push", "to": RESP}, legacy]
    assert SL._rung8_rows(recs, RESP) == [] and SL._reply_checks(recs, RESP) == []
    assert SL._turn_rows(recs, RESP) == []


def test_on_a_seat_a_line_before_the_push_is_no_reply_for_rung_eight(monkeypatch):
    monkeypatch.setattr(SL, "TURNS", False)                         # the timer: the push lands at cycle 1
    srv = _seat(passed=(1, 2, 3, 4, 5, 6, 7), limit=8)
    t0 = 10_000.0
    ent = CIO.entity_in(srv, CIO.OPERATOR)
    ent.properties["heard_n"] = 0
    seen, clock = {}, {"t": t0}

    def read(srv_, e, now):
        st = (e.properties or {}).get("heard_n")
        last = seen.get(e.eid, 0)
        seen[e.eid] = st
        return {"fired": True, "state": {"n": st}, "changed": st != last}

    def push(srv_, text, person=""):
        # they spoke 30 s before this push, after self's last look at them
        ent.properties["heard_n"] = 1
        ent.properties["heard_ts"] = clock["t"] - 30.0
    monkeypatch.setattr(SL, "_read_entity", read)
    monkeypatch.setattr(SL, "_push_chat", push)
    monkeypatch.setattr(SL, "_ask_llm", lambda *a, **k: {"rc": 1, "text": "off"})
    for i in range(25):                                             # ...and never after it
        clock["t"] = t0 + i * SL.PACE_S
        SL.cycle(srv, now=clock["t"])
    recs = SL._read(srv)
    check = next(r for r in recs if r.get("kind") == "reach" and r.get("reply"))
    assert check["answer"] == "changed" and check["replied"] is False
    assert [r["o"] for r in SL._rung8_rows(recs, RESP)] == [0.0]


@pytest.fixture(autouse=True)
def _chat_medium(monkeypatch):
    """These are the chat medium's tests (a line to chat:out is the turn): #264 moved
    rung 7's turns to the ball by default, so they switch the toy medium off."""
    monkeypatch.setattr(SL, "TOY_TURNS", False)
