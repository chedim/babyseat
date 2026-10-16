"""#161 RUNG 2: first reach -- the world's tempo.

Self reaches for one entity (the read fires its getter), predicts beforehand
whether the reading will have changed since it last looked, and notices
whether it did. It learns each entity's own rate of change. The rung passes
when, for one entity, its predictions beat the seat-wide change rate AND a
blind audit holds: membrane pauses that getter (self isn't told) and no
change is recorded while the source is stopped.

What it says when it passes: "I can tell which things move on their own, and
my marks of their moving are real." The world's tempo -- not yet a body.
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

from membrane import night as N  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
COUNTER = "n = int((state.get('data') or {}).get('n', 0)) + 1\nresult = {'state': {'n': n}}"
FIXED = "result = {'state': {'pct': 40}}"


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):          # the readings' field events: not this test's concern
        return []


def _dir(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _no_lru(monkeypatch):
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    N._TICK_LAST_PERSIST.clear() if hasattr(N, "_TICK_LAST_PERSIST") else None


def _seat(getters=(("moving", COUNTER), ("still", FIXED)), d=None, limit=2):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _World(_dir(1))
    est._srv = srv
    for name, code in getters:
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                          properties={"name": name, "getter_script": code})
        est.docs[e.eid] = e
    SL.bind(srv)
    SL.set_limit(srv, 1)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0, step=None):
    step = step or SL.PACE_S
    for i in range(n):
        SL.cycle(srv, now=t0 + i * step)
    return t0 + n * step


def _with_rung1(srv):
    """Earn rung 1 first (the ladder is climbed in order)."""
    t = _run(srv, SL.RUNG1_CYCLES * 2)
    assert SL.status(srv)["rung"] == 1
    return t


# ---- the answer: the getter path says whether the reading changed -----------

def test_a_read_back_says_whether_the_reading_changed():
    srv = _seat()
    est = srv.entity_store
    mov, still = est.docs[MEMBRANE_NS + "Component:moving"], est.docs[MEMBRANE_NS + "Component:still"]
    assert WA.entity_read_back(srv, mov)["changed"] is True
    assert WA.entity_read_back(srv, mov)["changed"] is True          # a counter: every read moves it
    assert WA.entity_read_back(srv, still)["changed"] is True        # the first reading is news
    assert WA.entity_read_back(srv, still)["changed"] is False       # the same reading is not


def test_a_paused_getter_answers_unchanged_without_running():
    srv = _seat()
    mov = srv.entity_store.docs[MEMBRANE_NS + "Component:moving"]
    WA.entity_read_back(srv, mov)
    WA.pause_getter(mov.eid, until=10 ** 12)
    out = WA.entity_read_back(srv, mov)
    assert out == {"fired": True, "state": None, "changed": False, "paused": True}
    assert mov.properties["script_data"]["n"] == 1                   # it did not run


def test_a_held_getter_says_so():
    srv = _seat()
    mov = srv.entity_store.docs[MEMBRANE_NS + "Component:moving"]
    mov.properties["script_disabled"] = True
    out = WA.entity_read_back(srv, mov)
    assert out.get("held") is True and out.get("error")


# ---- the act -----------------------------------------------------------------

def test_reach_is_one_of_its_methods_and_refuses_outside_callers():
    srv = _seat()
    out = N.method_call(srv, srv.entity_store, None, None, MEMBRANE_NS + "self", "reach",
                        {"target": MEMBRANE_NS + "Component:moving"})
    assert out["rc"] == 1
    assert "own loop" in str(out.get("head") or "") + str(out.get("tail") or "")


def test_no_reaching_under_limit_one():
    srv = _seat()
    _with_rung1(srv)
    _run(srv, 10, t0=10_000.0)
    assert not [r for r in _record(srv) if r["kind"] == "reach"]
    assert SL.status(srv)["waiting_on"] == "the limit"


def test_with_limit_two_it_reaches_and_records_the_answer():
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    _run(srv, 10, t0=t)
    reaches = [r for r in _record(srv) if r["kind"] == "reach"]
    assert len(reaches) == 10
    for r in reaches:
        assert r["eid"] in (MEMBRANE_NS + "Component:moving", MEMBRANE_NS + "Component:still")
        assert r["answer"] in ("changed", "unchanged", "none")
        assert 0.0 <= r["p"] <= 1.0
    assert MEMBRANE_NS + "self" not in {r["eid"] for r in reaches}


def test_a_no_answer_is_not_counted(monkeypatch):
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    monkeypatch.setattr(WA, "entity_read_back", lambda srv, ent, force=False, by="model":
                        {"fired": False, "reason": "lru"})
    _run(srv, 5, t0=t)
    reaches = [r for r in _record(srv) if r["kind"] == "reach"]
    assert reaches and all(r["answer"] == "none" for r in reaches)
    assert SL._tempo(srv) == {} or all(v["answered"] == 0 for v in SL._tempo(srv).values())


# ---- the tempo: it learns which things move on their own ---------------------

def test_it_learns_that_one_moves_and_one_sits_still():
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    _run(srv, 80, t0=t)
    reaches = [r for r in _record(srv) if r["kind"] == "reach" and r["answer"] != "none"]
    late = reaches[-20:]
    mov = [r["p"] for r in late if r["eid"].endswith("moving")]
    still = [r["p"] for r in late if r["eid"].endswith("still")]
    assert mov and still
    assert min(mov) > 0.8 and max(still) < 0.2


def test_the_tempo_survives_a_restart():
    d = tempfile.mkdtemp()
    srv = _seat(d=d)
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    _run(srv, 40, t0=t)
    srv.entity_store.flush()
    srv2 = _seat(d=d)
    assert SL._tempo(srv2) and SL._tempo(srv2) == SL._tempo(srv)


# ---- the rung: skill, then the blind audit -----------------------------------

def test_rung_two_waits_for_the_audit_then_opens(monkeypatch):
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    t = _run(srv, 60, t0=t)
    recs = _record(srv)
    starts = [r for r in recs if r["kind"] == "audit" and r.get("phase") == "start"]
    assert starts, "a candidate should have started its audit"
    assert starts[0]["eid"].endswith("moving")          # the still one can't prove a mark could fail
    t = _run(srv, 80, t0=t)
    recs = _record(srv)
    ends = [r for r in recs if r["kind"] == "audit" and r.get("phase") == "end"]
    assert ends and ends[0]["passed"] is True and ends[0]["changes"] == 0 and ends[0]["reaches"] >= 3
    assert [r["rung"] for r in recs if r["kind"] == "rung"] == [1, 2]
    assert SL.status(srv)["rung"] == 2


def test_the_audit_is_blind_and_the_model_does_not_learn_inside_it():
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    def _audit(phase):
        return [r for r in _record(srv) if r["kind"] == "audit" and r.get("phase") == phase]
    i = 0
    while not _audit("start") and i < 200:            # cycle by cycle, until an audit starts
        SL.cycle(srv, now=t + i * SL.PACE_S); i += 1
    start = _audit("start")[0]
    before = dict(SL._tempo(srv)[start["eid"]])
    while not _audit("end") and i < 400:              # ... and until it ends
        SL.cycle(srv, now=t + i * SL.PACE_S); i += 1
    inside = [r for r in _record(srv) if r["kind"] == "reach" and r.get("audit")]
    assert inside and all(r["answer"] in ("unchanged", "none") for r in inside)
    assert SL._tempo(srv)[start["eid"]] == before      # nothing learned while the source was stopped


def test_a_tautological_recorder_fails_the_audit(monkeypatch):
    # a mark written whenever the act runs can't fail -- the audit must catch it
    srv = _seat()
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    real = WA.entity_read_back

    def marks_regardless(srv, ent, force=False, by="model"):
        out = real(srv, ent, force, by)
        return {**out, "changed": True} if out.get("paused") else out   # it marks a change with the source stopped
    monkeypatch.setattr(WA, "entity_read_back", marks_regardless)
    _run(srv, 160, t0=t)
    recs = _record(srv)
    ends = [r for r in recs if r["kind"] == "audit" and r.get("phase") == "end"]
    assert ends and ends[0]["passed"] is False
    assert 2 not in [r["rung"] for r in recs if r["kind"] == "rung"]


def test_a_world_where_everything_changes_teaches_no_tempo():
    srv = _seat(getters=(("a", COUNTER), ("b", COUNTER)))
    t = _with_rung1(srv)
    SL.set_limit(srv, 2)
    _run(srv, 80, t0=t)
    assert SL.status(srv)["rung"] == 1                    # nothing to tell apart: no skill over the base rate
