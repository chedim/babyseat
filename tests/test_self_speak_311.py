"""#311: SPEAK -- self's turns carry words from its own record.

Once speak opens (time its turn and expect a reply earned and accepted),
self's turns go back to chat (the SPEAK_RUNG switch, #264). Each push is
either PLAIN ("self is here", the content-free line it always pushed) or
WORDS: a short referential utterance naming the thing of its latest
answered look and what that look found ("lamp: changed", "lamp: still"),
made by a fixed template from its own record. No model composes, picks or
edits a word: the seat's model talks TO self, never AS it (#161). Which
kind a push is is a fair coin per push (seeded by the cycle), drawn only
when self has something to say; a push with nothing to say is "mute" and
counts for nothing.

The test, staged, over the last SPEAK_PUSHES pushes at the current other in
chat that had a reply check:
  pushes  enough of each kind (SPEAK_EACH words, SPEAK_EACH plain);
  truth   every utterance is true of the record: the look it cites is on
          the record before the push, at that thing, with that answer, the
          latest answered look at it, and the words are the template's;
          one false utterance zeroes the stage;
  lift    the other replies more often to words than to plain pushes, at
          the reply check: two-proportion z >= SPEAK_LIFT_Z and a lift of
          at least SPEAK_LIFT_MARGIN;
  timing  it keeps its turn-timing while speaking: rung 7's contingency over
          the same pushes, at rung 7's bar.
"""
import inspect
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
from membrane import self_sheet as SS  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from tests._self_cap import cap  # noqa: E402

RESP = MEMBRANE_NS + "Component:chat:in:operator"
LAMP = MEMBRANE_NS + "Component:lamp"
NB = len(SL.TURN_BINS) + 1


# ---- a synthetic record ------------------------------------------------------------------

def _record(n=120, p_plain=0.3, p_words=0.65, seed=1, timed=True, lie_at=None, mute_every=0,
            medium=None):
    """n pushes at RESP, each after a look at the lamp, with a reply check.
    The kind is a fair coin; the other replies with p_words / p_plain.
    timed: every push lands in the window self believed best (bin 0, a tenth
    of its waiting time) -- rung 7's contingency; untimed: the bin cycles and
    the waiting time is even, so the in-window share is the shuffled one."""
    rng = random.Random(seed)
    recs, t = [], 10_000.0
    for i in range(n):
        ans = "changed" if i % 3 else "unchanged"
        look = {"ts": t, "kind": "reach", "n": i, "eid": LAMP, "p": 0.5, "answer": ans}
        recs.append(look)
        t += 10.0
        push = {"ts": t, "kind": "push", "n": i, "eid": "chat:out", "to": RESP}
        if medium:
            push["medium"] = medium
        if timed:
            push.update(bin=0, aim=0, best=0, dwell=[1, 3, 3, 3], why="aim")
        else:
            push.update(bin=i % NB, aim=i % NB, best=0, dwell=[1] * NB, why="aim")
        if mute_every and i % mute_every == 0:
            push["speech"] = "mute"
            words = False
        else:
            words = rng.random() < 0.5
            push["speech"] = "words" if words else "plain"
        if words:
            push["words"] = SL._speak_words(LAMP, ans)
            push["about"] = {"eid": LAMP, "answer": ans, "ts": look["ts"]}
            if lie_at == i:
                push["about"] = {"eid": LAMP, "answer": "unchanged" if ans == "changed" else "changed",
                                 "ts": look["ts"]}
                push["words"] = SL._speak_words(LAMP, push["about"]["answer"])
        recs.append(push)
        replied = rng.random() < (p_words if words else p_plain)
        recs.append({"ts": t + 130.0, "kind": "reach", "n": i, "eid": RESP, "p": 0.5, "answer": "changed",
                     "reply": True, "replied": replied, **({"medium": medium} if medium else {})})
        t += 600.0
    return recs


def _last_said(recs):
    """The index of the last push with words (inside the test's window)."""
    return max(k for k, r in enumerate(recs) if r.get("kind") == "push" and r.get("speech") == "words")


# ---- 1. the tree ----------------------------------------------------------------------------

def test_speak_is_built_under_turns_and_expect():
    a = SL.ACHIEVEMENTS["speak"]
    assert a["built"] is True and a["rung"] == 11 and a["branch"] == "later"
    assert set(a["prereqs"]) == {"turns", "expect"}
    assert "speak" in SL.BUILT
    assert "time its turn" in a["why_prereqs"] and "expect a reply" in a["why_prereqs"]
    assert SL.PROGRESS[11] is SL._rung11
    assert SL.SKILL_PASS[11] == SL.SPEAK_LIFT_MARGIN
    assert 11 in SL.LADDER and SL.LADDER[11] == "speak"
    assert SL.SPEAK_RUNG == 11


def test_the_child_analogy_is_what_the_test_measures():
    # the test measures first referential words to an other (~12 mo), not
    # autobiographical narrative (~4 y)
    a = SL.ACHIEVEMENTS["speak"]
    assert a["child_age"] == "~12 mo"
    assert "Bates" in a["child_ability"] and "narrative" not in a["child_ability"]
    assert SS.CHILD[11][0] == 12.0 and "Bates" in SS.CHILD[11][1]


def test_the_bar():
    assert SL.SPEAK_PUSHES == 100 and SL.SPEAK_EACH == 40
    assert SL.SPEAK_LIFT_Z >= 3.5 and SL.SPEAK_LIFT_MARGIN >= 0.15


# ---- 2. the words: a template over its own record ------------------------------------------

def test_the_words_name_a_thing_and_what_the_look_found():
    assert SL._speak_words(LAMP, "changed") == "lamp: changed"
    assert SL._speak_words(LAMP, "unchanged") == "lamp: still"
    assert SL._speak_words(MEMBRANE_NS + "Component:toy:dice", "changed") == "dice: changed"
    assert SL._speak_words(LAMP, "none") is None                      # no answer: nothing to say


def test_it_cites_its_latest_answered_look_at_a_thing_not_the_other():
    recs = [{"ts": 1.0, "kind": "reach", "eid": LAMP, "answer": "changed"},
            {"ts": 2.0, "kind": "reach", "eid": LAMP, "answer": "unchanged"},
            {"ts": 3.0, "kind": "reach", "eid": LAMP, "answer": "none"},            # no answer
            {"ts": 4.0, "kind": "reach", "eid": "x", "answer": "changed", "audit": True},   # blind audit
            {"ts": 5.0, "kind": "reach", "eid": RESP, "answer": "changed"}]          # the other itself
    a = SL._speak_about(recs, RESP)
    assert a == {"eid": LAMP, "answer": "unchanged", "ts": 2.0}
    assert SL._speak_about([{"ts": 5.0, "kind": "reach", "eid": RESP, "answer": "changed"}], RESP) is None


def test_no_model_in_the_words():
    # the code that makes a line names nothing of the model's (its docstrings may say so)
    import io
    import tokenize
    src = inspect.getsource(SL._speak_words) + inspect.getsource(SL._speak_about) + inspect.getsource(SL._speak)
    names = {t.string.lower() for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.NAME}
    for bad in ("llm", "_ask", "request", "model"):
        assert not any(bad in n for n in names), bad


def test_the_coin_is_fair_and_seeded():
    recs = [{"ts": 1.0, "kind": "reach", "eid": LAMP, "answer": "changed"}]
    kinds = [SL._speak(recs, RESP, n)["speech"] for n in range(2000)]
    assert abs(kinds.count("words") / 2000 - 0.5) < 0.04
    assert [SL._speak(recs, RESP, n)["speech"] for n in range(50)] == kinds[:50]   # the cycle seeds it
    s = next(SL._speak(recs, RESP, n) for n in range(50) if kinds[n] == "words")
    assert s["text"] == "lamp: changed" and s["words"] == "lamp: changed"
    assert s["about"] == {"eid": LAMP, "answer": "changed", "ts": 1.0}
    p = next(SL._speak(recs, RESP, n) for n in range(50) if kinds[n] == "plain")
    assert p["text"] == SL.SPEAK_PLAIN == "self is here" and "words" not in p
    mute = SL._speak([], RESP, 3)
    assert mute["speech"] == "mute" and mute["text"] == SL.SPEAK_PLAIN


# ---- 3. the test ------------------------------------------------------------------------------

def test_words_that_earn_replies_and_kept_timing_pass():
    out = SL._rung11(_record())
    assert list(out["crit"]) == ["pushes", "truth", "lift", "timing"]
    assert out["passed"] is True, out
    assert out["skill"] == pytest.approx(out["rate_words"] - out["rate_plain"], abs=1e-3)
    assert out["n_words"] >= SL.SPEAK_EACH and out["n_plain"] >= SL.SPEAK_EACH
    assert out["false"] == 0


def test_an_other_who_does_not_care_what_it_says_fails_at_lift():
    out = SL._rung11(_record(p_plain=0.45, p_words=0.45))
    assert out["crit"]["pushes"] == 1.0 and out["crit"]["truth"] == 1.0
    assert out["crit"]["lift"] < 1.0 and out["passed"] is False
    assert SL._staged(out["crit"])[1] == "lift"


def test_one_false_utterance_fails_truth():
    recs = _record(lie_at=None)
    good = SL._rung11(recs)
    assert good["passed"]
    i = _record()[_last_said(_record())]["n"]
    out = SL._rung11(_record(lie_at=i))
    assert out["crit"]["truth"] == 0.0 and out["false"] == 1 and out["passed"] is False


def test_words_citing_a_look_that_was_not_its_latest_are_false():
    recs = _record()
    j = _last_said(recs)
    # a newer answered look at the lamp lands between the cited look and the push
    recs.insert(j, {"ts": recs[j]["ts"] - 1.0, "kind": "reach", "eid": LAMP, "answer": "changed", "p": 0.5})
    out = SL._rung11(recs)
    assert out["false"] == 1 and out["crit"]["truth"] == 0.0


def test_words_the_template_would_not_make_are_false():
    recs = _record()
    j = _last_said(recs)
    recs[j] = {**recs[j], "words": "lamp: on fire"}
    assert SL._rung11(recs)["false"] == 1


def test_a_look_that_is_not_on_the_record_is_false():
    recs = _record()
    j = _last_said(recs)
    recs[j] = {**recs[j], "about": {**recs[j]["about"], "ts": 1.0}}
    assert SL._rung11(recs)["false"] == 1


def test_untimed_words_fail_at_timing():
    out = SL._rung11(_record(timed=False))
    assert out["crit"]["lift"] == 1.0 and out["crit"]["timing"] < 1.0 and out["passed"] is False
    assert SL._staged(out["crit"])[1] == "timing"


def test_too_few_of_a_kind_holds_it_at_pushes():
    out = SL._rung11(_record(n=60))
    assert out["crit"]["pushes"] < 1.0 and out["passed"] is False
    assert SL._staged(out["crit"])[1] == "pushes"


def test_mute_pushes_and_turns_with_the_ball_count_for_nothing():
    recs = _record(mute_every=2)
    rows = SL._speak_rows(recs, RESP)
    assert all(r["speech"] in ("words", "plain") for r in rows) and len(rows) == 60
    assert SL._speak_rows(_record(medium="toy"), RESP) == []


def test_an_empty_record():
    out = SL._rung11([])
    assert out["passed"] is False and list(out["crit"]) == ["pushes", "truth", "lift", "timing"]
    assert out["skill"] == 0.0


# ---- 4. the bar: chance can't pass, words that matter do --------------------------------------

def _ever(p_plain, p_words, n, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        w = bool(rng.random() < 0.5)
        rows.append({"speech": "words" if w else "plain",
                     "replied": bool(rng.random() < (p_words if w else p_plain))})
    for j in range(SL.SPEAK_PUSHES, n + 1):
        s = SL._speak_lift(rows[j - SL.SPEAK_PUSHES:j])
        if s["passed"]:
            return True
    return False


@pytest.mark.parametrize("rate", [0.3, 0.5, 0.7])
def test_an_other_who_replies_alike_to_both_rarely_passes_in_a_week(rate):
    # read at every push like the live loop, ~1000 chat turns (a week)
    hits = sum(_ever(rate, rate, 1000, s) for s in range(60))
    assert hits <= 3


def test_an_other_who_answers_words_more_lets_it_pass():
    assert sum(_ever(0.3, 0.6, 1000, s) for s in range(20)) >= 19


# ---- 5. on a seat ---------------------------------------------------------------------------

class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=16)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _seat(passed=(1, 2, 3, 4, 5, 6, 7, 8), limit=11):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_Field())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=LAMP, cls="Component",
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


class _World:
    """The lamp flips now and then; the other speaks every `every` s and
    answers a push with words more often than a plain one."""

    def __init__(self, srv, every=900.0, p_words=0.9, p_plain=0.2, seed=3):
        self.srv, self.every, self.t, self.next = srv, every, 0.0, 0.0
        self.p_words, self.p_plain = p_words, p_plain
        self.rng = random.Random(seed)
        self.replies, self.lines, self.seen, self.lamp = [], [], {}, 0

    def tick(self, t):
        self.t = t
        if self.rng.random() < 0.3:
            self.lamp += 1
        due = [r for r in self.replies if r <= t]
        if t >= self.next or due:
            self.replies = [r for r in self.replies if r > t]
            ent = CIO.entity_in(self.srv, CIO.OPERATOR)
            ent.properties["heard_n"] = int(ent.properties.get("heard_n") or 0) + 1
            ent.properties["heard_ts"] = t
            if t >= self.next:
                self.next = t + self.every

    def push(self, srv, text, person=""):
        self.lines.append(text)
        p = self.p_plain if text == SL.SPEAK_PLAIN else self.p_words
        if self.rng.random() < p:
            self.replies.append(self.t + 30.0)

    def read(self, srv, ent, now):
        st = (ent.properties or {}).get("heard_n") if ent.eid == RESP else self.lamp
        last = self.seen.get(ent.eid)
        self.seen[ent.eid] = st
        return {"fired": True, "state": {"n": st}, "changed": st is not None and st != last}


def _run(srv, world, cycles, monkeypatch, t0=10_000.0, model=None):
    monkeypatch.setattr(SL, "_read_entity", world.read)
    monkeypatch.setattr(SL, "_push_chat", world.push)
    monkeypatch.setattr(SL, "_ask_llm", model or (lambda *a, **k: {"rc": 1, "text": "off"}))
    for i in range(cycles):
        t = t0 + i * SL.PACE_S
        world.tick(t)
        SL.cycle(srv, now=t)


def test_speak_stays_locked_until_turns_is_accepted():
    srv = _seat(passed=(1, 2, 3, 4, 5, 6, 8))                       # time its turn not earned
    assert not SL._available(srv, SL.SPEAK_RUNG)
    st = SL.status(srv)
    assert "speak" not in st["next"]
    row = {r["id"]: r for r in SS.tree(SL._read(srv), st)}["speak"]
    assert row["state"] == "locked" and row["built"] is True and "time its turn" in row["waiting_on"]
    srv = _seat()
    SL._append(srv, {"ts": 2.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.1})
    assert SL._available(srv, SL.SPEAK_RUNG)                         # cap() accepted what it earned
    # an earned, unaccepted turns opens nothing (#299)
    srv = _seat(passed=(1, 2, 3, 4, 5, 6, 8), limit=11)
    SL._append(srv, {"ts": 2.0, "kind": "achieve", "id": "turns", "rung": 7, "skill": 0.1})
    assert not SL._available(srv, SL.SPEAK_RUNG)
    row = {r["id"]: r for r in SS.tree(SL._read(srv), SL.status(srv))}["speak"]
    assert row["state"] == "locked" and row["waiting_on"].startswith("your accept of ")


def test_on_a_seat_its_turns_go_back_to_chat_with_its_own_words(monkeypatch):
    srv = _seat()
    asked = []

    def model(srv_, eid, prompt):
        asked.append(prompt)
        return {"rc": 1, "text": "off"}
    w = _World(srv)
    _run(srv, w, 2500, monkeypatch, model=model)
    recs = SL._read(srv)
    ps = [r for r in recs if r.get("kind") == "push"]
    assert len(ps) >= 15
    assert all(SL._medium(p) == "chat" and p.get("to") == RESP for p in ps)        # no ball: chat again
    assert all(p["speech"] in ("words", "plain", "mute") for p in ps)
    said = [p for p in ps if p["speech"] == "words"]
    assert said and any(p["speech"] == "plain" for p in ps)
    # what reached the chat is exactly what the record says it pushed
    assert w.lines == [p["words"] if p["speech"] == "words" else SL.SPEAK_PLAIN for p in ps]
    for p in said:
        assert p["words"] == SL._speak_words(p["about"]["eid"], p["about"]["answer"])
        assert p["about"]["eid"] not in (RESP, SL._eid())
    assert SL._rung11(recs)["false"] == 0 and SL._rung11(recs)["crit"]["truth"] == 1.0
    for p in ps:                                                    # the timing is rung 7's policy
        assert p["aim"] in range(NB) and p["why"] in ("aim", "overdue")
    assert asked                                                    # rung 8's advisor is asked...
    assert all(a not in w.lines for a in asked)                     # ...and never speaks


def test_before_speak_opens_the_turns_stay_with_the_ball(monkeypatch):
    srv = _seat(limit=8)
    assert SL._toy_turns(srv) and not SL._available(srv, SL.SPEAK_RUNG)
    srv = _seat()
    assert not SL._toy_turns(srv)


def test_status_and_the_sheet_show_speak(monkeypatch):
    srv = _seat()
    _run(srv, _World(srv), 800, monkeypatch)
    st = SL.status(srv)
    sp = st["speak"]
    for k in ("words", "plain", "mute", "n_words", "n_plain", "rate_words", "rate_plain", "lift", "z", "false",
              "last_words"):
        assert k in sp
    assert "speak" in st["next"]
    s = SS.sheet(srv, fresh=True)
    sk = {k["id"]: k for k in s["skills"]}
    assert "speak" in sk and sk["speak"]["name"] == "speak"
    crit = [c["key"] for c in sk["speak"]["criteria"]]
    assert crit == ["pushes", "truth", "lift", "timing"]
    texts = " | ".join(c["text"] for c in sk["speak"]["criteria"])
    assert "with words" in texts and "true of its record" in texts and "z" in texts
    assert sk["speak"]["trend"]["pass"] == SL.SPEAK_LIFT_MARGIN
    rows = {r["rung"]: r for r in s["growth"]["rows"]}
    assert rows[11]["built"] and rows[11]["name"] == "speak" and rows[11]["fastest_h"] is not None


@pytest.fixture(autouse=True)
def _seed_turns(monkeypatch):
    """The toy medium stays on (the seat's default): speak's opening is what moves the turns to chat."""
    monkeypatch.setattr(SL, "TOY_TURNS", True)
