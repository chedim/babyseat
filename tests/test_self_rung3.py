"""#161 RUNG 3: tell things apart.

Self sorts its world into kinds from its own experience -- moving, still,
intermittent, silent -- and acts on the sort: it stops spending its reaches on
things that never answer (62% of its reaches on the live seat, 2026-10-16),
still checking them now and then. The rung passes when its kinds agree across
the two halves of its record, its no-answer share falls under 25%, and rung
2's skill still holds.
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from _self_cap import cap  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


def _dir(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


class _Things:
    """A simulated world of getters, by name: always / never / half / silent.
    Honors the audits' pause like the real getter path."""
    def __init__(self):
        self.looks = {}
        self.flip = set()                      # names whose behaviour is swapped (an unstable world)

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > __import__("time").time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        k = self.looks[name] = self.looks.get(name, 0) + 1
        kind = name.rstrip("0123456789")
        if name in self.flip:
            kind = {"always": "never", "never": "always"}.get(kind, kind)
        if kind == "silent":
            return {"fired": True, "state": None, "changed": None}
        changed = {"always": True, "never": k == 1, "half": k % 2 == 0}[kind]
        return {"fired": True, "state": {"v": k}, "changed": changed}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names=("always", "never", "half", "silent1", "silent2", "silent3"), limit=3, accept=True):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _World(_dir(1))
    est._srv = srv
    for name in names:
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL.bind(srv)
    SL.set_mode(srv, "on")
    for r in (1, 2):                                   # rungs 1 and 2 already earned (their own tests pin them)
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    if accept:
        cap(srv, limit)                                # #299: accepted, and rungs 1..limit run alone
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _reaches(srv):
    return [r for r in _record(srv) if r["kind"] == "reach"]


def test_reach_records_carry_dt(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat(names=("always",))
    _run(srv, 3)
    rs = _reaches(srv)
    assert rs[0]["dt"] is None and rs[1]["dt"] == pytest.approx(SL.PACE_S)


def test_no_answers_are_counted_in_the_tempo(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat(names=("silent1",))
    _run(srv, 4)
    t = SL._tempo(srv)[MEMBRANE_NS + "Component:silent1"]
    assert t["none"] == 4 and t["answered"] == 0


def test_it_sorts_its_world_into_kinds(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat()
    _run(srv, 200)
    world = {k.split(":")[-1]: v for k, v in SL.status(srv)["world"].items()}
    assert world["always"] == "moving"
    assert world["never"] == "still"
    assert world["half"] == "intermittent"
    assert world["silent1"] == "silent"
    kinds = SL._entity(srv).properties["script_data"]["kinds"]
    assert kinds[MEMBRANE_NS + "Component:always"] == "moving"


def test_it_stops_spending_reaches_on_silent_things_but_still_checks_them(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat()
    _run(srv, 240)
    late = _reaches(srv)[-80:]
    none_share = sum(r["answer"] == "none" for r in late) / len(late)
    assert none_share < 0.25                         # it was 50% with three silent things of six
    early = _reaches(srv)[:30]
    assert sum(r["answer"] == "none" for r in early) / len(early) > 0.3
    assert any(r["eid"].split(":")[-1].startswith("silent") for r in late)   # still checked now and then


def test_rung_three_opens_by_its_test(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat()
    _run(srv, 300)
    assert 3 in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]
    assert SL.status(srv)["rung"] == 3


def _reach_recs(pattern):
    """Synthetic reach lines: pattern = [(eid, answer), ...], all sorted (steer)."""
    return [{"kind": "reach", "eid": e, "answer": a, "p": 0.5, "steer": True} for e, a in pattern]


def test_kinds_that_disagree_across_the_halves_fail_the_test():
    # the first half says 'a' always moves and 'b' sits still; the second says the reverse
    first = [("a", "changed"), ("b", "unchanged")] * 20
    second = [("a", "unchanged"), ("b", "changed")] * 20
    out = SL._rung3(_reach_recs(first + second))
    assert out["crit"]["kinds"] == 0.0 and out["passed"] is False
    same = SL._rung3(_reach_recs(first + first))
    assert same["crit"]["kinds"] == 1.0


def test_one_kind_is_not_telling_things_apart():
    out = SL._rung3(_reach_recs([("a", "changed"), ("b", "changed")] * 40))
    assert out["crit"]["kinds"] == 0.0


def _mix(changed_a, total_a, changed_b, total_b):
    """One half's reaches: `changed_a` changed (the rest unchanged) looks at 'a',
    then the same for 'b'."""
    out = [("a", "changed")] * changed_a + [("a", "unchanged")] * (total_a - changed_a)
    out += [("b", "changed")] * changed_b + [("b", "unchanged")] * (total_b - changed_b)
    return _reach_recs(out)


def test_a_small_rate_drift_within_a_kind_does_not_fail_the_test():
    # Component:i live: its change rate drifted across the moving/intermittent
    # line between the two halves (0.55 -> 0.65) -- the same kind, one look's
    # worth of drift. Rate-based, a <=0.25 drift agrees; label-based it failed.
    first = _mix(11, 20, 2, 20)     # a 0.55 changed, b 0.1 changed
    second = _mix(13, 20, 3, 20)    # a 0.65, b 0.15
    out = SL._rung3(first + second)
    assert out["crit"]["kinds"] == 1.0


def test_expect_remembers_what_it_saw_last(monkeypatch):
    # ONE BIT OF MEMORY: the world bursts -- a change usually follows a change,
    # a quiet a quiet. The "half" thing alternates, so the bit predicts the
    # alternation where the flat share (0.5) would say nothing.
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat(names=("half",))
    _run(srv, 50)
    rs = [r for r in _reaches(srv) if r["answer"] in ("changed", "unchanged")]
    assert len(rs) >= 6
    for i in range(3, len(rs)):                     # once warmed up: predict what followed last
        if rs[i - 1]["answer"] == "changed":
            assert rs[i]["p"] < 0.5, (i, rs[i]["p"], rs[i - 1]["answer"])
        else:
            assert rs[i]["p"] > 0.5, (i, rs[i]["p"], rs[i - 1]["answer"])
    t = SL._tempo(srv)[MEMBRANE_NS + "Component:half"]
    assert t["prev"] in ("changed", "unchanged") and "after" in t


def test_rung3_skill_is_carried_by_moving_things_not_a_still_counter():
    # A never-changing counter (like the live w:rung rung-pass counter) is
    # trivially predictable: predicting "it won't change" scores ~1.0, but that
    # is the zero-gap degenerate, not tempo mastery. The skill bar must read
    # the moving thing's real skill instead. "mov" changes 13 of 20 (auditable,
    # p_hat 0.64) yet its predictions only reach ~0.31 skill; "counter" never
    # changes, predicted at p=0.01, so ~0.999 skill.
    recs = ([{"kind": "reach", "eid": "mov", "answer": "changed", "p": 0.6, "steer": True}] * 13
            + [{"kind": "reach", "eid": "mov", "answer": "unchanged", "p": 0.6, "steer": True}] * 7
            + [{"kind": "reach", "eid": "counter", "answer": "unchanged", "p": 0.01, "steer": True}] * 20)
    tempo = {"mov": {"changed": 13, "answered": 20},
             "counter": {"changed": 0, "answered": 20}}
    out = SL._rung3(recs, tempo)
    assert out["skill"] == pytest.approx(0.3095, abs=0.03)   # the moving thing's skill
    assert out["skill"] < 0.9                                # not the counter's ~0.999


def test_an_unaccepted_first_reach_holds_rung_three(monkeypatch):      # #299: the limit's job is the accept's
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat(accept=False)
    assert SL.status(srv)["awaiting"] == ["feel", "reach"]
    _run(srv, 300)
    assert 3 not in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]
    assert not any(r.get("steer") for r in _reaches(srv))      # no sorting practised before reach is accepted


def test_no_new_rung_two_audit_once_rung_two_has_passed(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Things())
    srv = _seat(names=("always", "never"), limit=2)
    _run(srv, 200)
    assert not [r for r in _record(srv) if r["kind"] == "audit"]
