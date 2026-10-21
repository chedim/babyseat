"""#161 RUNG 6: meet an other.

Self gets its first outward act: push a line to `chat:out`. The thing that
answers it -- imperfectly, variably, with delay -- is an OTHER (layer 2); what
it predicts near-perfectly (zero gap, its own echo) is its BODY. The rung
passes once self has pushed, attended to the answerer's replies, and classed
the answerer as an other, not a body.
"""
import json
import os
import random
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from _self_cap import cap  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
RESP = MEMBRANE_NS + "Component:chat:operator"    # the other self meets


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


class _Chatter:
    """The other: answers self's reaches imperfectly (p of the time 'changed',
    else 'unchanged'), delayed; honours the getter pause like the real path."""
    def __init__(self, p=0.4, seed=7):
        self.p, self.rng, self.looks = p, random.Random(seed), {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > time.time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        self.looks[name] = self.looks.get(name, 0) + 1
        if ":chat:" in ent.eid:
            return {"fired": True, "state": {"heard": self.looks[name]},
                    "changed": self.rng.random() < self.p}
        return {"fired": True, "state": {"v": self.looks[name]}, "changed": False}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names=(), limit=6, passed=(1, 2, 3, 4, 5)):
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
    CIO.entity(srv, CIO.OPERATOR)                   # the other self meets (a real reach target)
    SL.bind(srv)
    cap(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
    cap(srv, limit)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _kind(srv, k):
    return [r for r in _record(srv) if r["kind"] == k]


def _push_recs(to, n=None):
    n = SL.RUNG6_PUSHES if n is None else n
    return [{"kind": "push", "eid": "chat:out:operator", "to": to, "n": i, "ts": float(i)}
            for i in range(n)]


def test_it_reaches_the_other_and_counts_its_answers(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Chatter())
    srv = _seat()
    _run(srv, 40)
    assert [r for r in _kind(srv, "reach") if ":chat:" in r["eid"]]
    t = SL._tempo(srv)[RESP]
    assert t["answered"] > 0


def test_it_classes_the_imperfect_answerer_as_other(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Chatter())
    srv = _seat()
    _run(srv, 200)
    assert SL._split(_record(srv), SL._tempo(srv))[RESP] == "other"
    assert SL.status(srv)["split"][RESP] == "other"


def test_a_perfect_echo_is_a_body_not_an_other():
    # a responder self predicts near-perfectly (zero gap, its own echo) is a
    # BODY -- rung 6 must not open against it
    recs = ([{"kind": "reach", "eid": "echo", "answer": "changed", "p": 0.99, "steer": True}] * 20
            + [{"kind": "reach", "eid": "still", "answer": "unchanged", "p": 0.01, "steer": True}] * 20)
    tempo = {"echo": {"changed": 20, "answered": 20}, "still": {"changed": 0, "answered": 20}}
    assert SL._split(recs, tempo)["echo"] == "body"
    out = SL._rung6(_push_recs("echo") + recs, tempo)
    assert out["crit"]["split"] == 0.0 and out["passed"] is False


def test_an_imperfect_answerer_is_an_other():
    # a responder self can't predict (its answers vary) is an OTHER
    recs = ([{"kind": "reach", "eid": "other", "answer": "changed", "p": 0.5, "steer": True}] * 10
            + [{"kind": "reach", "eid": "other", "answer": "unchanged", "p": 0.5, "steer": True}] * 10
            + [{"kind": "reach", "eid": "still", "answer": "unchanged", "p": 0.01, "steer": True}] * 20)
    tempo = {"other": {"changed": 10, "answered": 20}, "still": {"changed": 0, "answered": 20}}
    assert SL._split(recs, tempo)["other"] == "other"


def test_the_limit_holds_rung_six(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Chatter())
    srv = _seat(limit=5)
    _run(srv, 400)
    assert not _kind(srv, "push")
    assert 6 not in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]


def test_chat_in_is_a_reachable_entity_that_hears():
    srv = _seat()
    resp = CIO.entity(srv, CIO.OPERATOR)
    assert resp is not None and resp.eid.endswith("chat:operator")
    assert (resp.properties or {}).get("getter_script")
    CIO.heard(srv, CIO.OPERATOR, "hello", "sid")
    assert (resp.properties or {}).get("heard_n") == 1


def test_meet_carries_the_exchange_ages_to_the_viewer():
    # the home viewer's beam + flash read push_age / heard_age off status()'s
    # meets: a number right after a push / an answered look, absent before
    srv = _seat()
    now = time.time()
    SL._append(srv, {"ts": now - 0.2, "kind": "push", "eid": "chat:out:operator",
                     "to": RESP, "n": 1})
    SL._append(srv, {"ts": now - 0.1, "kind": "reach", "eid": RESP, "n": 1,
                     "answer": "changed", "p": 0.5, "why": "heard"})
    m = SL.status(srv)["meets"]
    assert m["other"] == RESP
    assert isinstance(m["push_age"], float) and 0.0 <= m["push_age"] < 1.0
    assert isinstance(m["heard_age"], float) and 0.0 <= m["heard_age"] < 1.0


def test_meet_ages_are_absent_before_any_exchange():
    srv = _seat()
    m = SL.status(srv)["meets"]
    assert m["pushes"] == 0 and m["other"] is None
    assert m["push_age"] is None and m["heard_age"] is None


@pytest.fixture(autouse=True)
def _rebuilt_333(monkeypatch):
    """#333: meet, turns, expect and mirror are not built until they are rebuilt
    on the nudge (part 3); the scoring and the readers tested here run on the
    tree as part 3 will build it, their passes on ladder 3 (the loop's old acts
    -- meet's push, the ball's turns, the asks at a push -- are gone)."""
    from tests._self_cap import rebuilt
    rebuilt(monkeypatch)
