"""#333 part 3b: LEARNED AIMS (the curiosity tilt on what nudges teach).

An aim is drawn among the things of Blurp's world with a field spot. In
AIM_EXPLORE (0.1) of draws it is uniform among all of them, whether Blurp can
look at them or not (exploration). The rest go to the things it can look at
(a getter), weighted by the curiosity tilt on each thing's NUDGE CHECKS --
its nudged looks (k >= 1), scored (o - p_n)^2 -- over NUDGE_CURIO_WINDOW
(10) checks a window: the fastest-improving thing gets 1, a flat or worsening
one CURIO_FLOOR, a thing too young to have progress 1; with no progress
anywhere (under CURIO_MIN) the draw is uniform among the lookable things. A
thing it cannot look at can teach it nothing it sees, so only exploration
reaches it. The look curiosity (_curiosity over ordinary looks) is unchanged:
nudged looks stay out of it."""
import collections
import json
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

from membrane import self_loop as SL  # noqa: E402
from membrane import self_world as SW  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
T0 = 1_700_000_000.0
NS = MEMBRANE_NS + "Component:"


def test_the_approved_values():
    assert SL.AIM_EXPLORE == 0.1
    assert SL.NUDGE_CURIO_WINDOW == 10


# ---- the nudge checks and their progress ------------------------------------------------

def _check(eid, k, p_n, answer, **kw):
    return {"ts": T0, "kind": "reach", "eid": eid, "p": 0.5, "answer": answer, "nudged": k, "p_n": p_n, **kw}


def _learning(eid, n=20):
    """Checks whose p_n gets better: wrong early, right late."""
    out = []
    for i in range(n):
        p = 0.5 if i < n // 2 else 0.95
        out.append(_check(eid, 1, p, "changed"))
    return out


def _flat(eid, n=20):
    return [_check(eid, 1, 0.5, "changed" if i % 2 else "unchanged") for i in range(n)]


def test_check_errors_read_only_answered_nudged_looks_outside_audits():
    recs = [_check("a", 1, 0.25, "changed"),
            _check("a", 2, 0.5, "unchanged"),
            _check("a", 0, 0.5, "changed"),                 # an ordinary look: no check
            _check("a", 1, 0.5, "none"),                    # no answer
            _check("a", 1, 0.5, "changed", audit=True),     # inside an audit
            _check("a", 1, 0.5, "changed", audit5="pause"),
            {"ts": T0, "kind": "reach", "eid": "a", "p": 0.5, "answer": "changed", "nudged": 1}]   # no p_n
    assert SL._check_errors(recs) == {"a": [0.5625, 0.25]}


def test_the_ordinary_look_errors_still_leave_the_checks_out():
    assert SL._ordinary_errors(_learning("a")) == {}


def test_nudge_progress_needs_two_windows_of_checks():
    W = SL.NUDGE_CURIO_WINDOW
    assert SL._nudge_progress(_learning("a", 2 * W - 1)) == {}
    lp = SL._nudge_progress(_learning("a", 2 * W) + _flat("b", 2 * W))
    assert lp["a"] == pytest.approx(0.25 - 0.0025)
    assert lp["b"] == pytest.approx(0.0)


def test_the_aim_tilt_is_curiositys_formula_on_the_checks():
    W = SL.NUDGE_CURIO_WINDOW
    recs = _learning("a", 2 * W) + _flat("b", 2 * W)
    t = SL._aim_tilt(recs, ["a", "b", "c"])
    assert t["a"] == 1.0                                     # the fastest-improving
    assert t["b"] == SL.CURIO_FLOOR                          # flat
    assert t["c"] == 1.0                                     # too young to have progress
    assert SL._aim_tilt(_flat("a", 2 * W) + _flat("b", 2 * W), ["a", "b"]) == {"a": 1.0, "b": 1.0}


# ---- the draw ---------------------------------------------------------------------------

def _draws(recs, cands, lookable, n=6000):
    return collections.Counter(SL._aim_pick(recs, cands, lookable, f"aim:{i}") for i in range(n))


def test_aims_fall_on_lookable_things_in_about_1_minus_explore_of_draws():
    cands = [f"w{i}" for i in range(18)] + ["a", "b"]
    got = _draws([], cands, ["a", "b"])
    n = sum(got.values())
    on = (got["a"] + got["b"]) / n
    want = (1 - SL.AIM_EXPLORE) + SL.AIM_EXPLORE * 2 / 20
    assert abs(on - want) < 0.02
    off = 1 - on                                             # a thing with no getter: only by exploration
    assert abs(off - SL.AIM_EXPLORE * 18 / 20) < 0.02
    assert set(got) == set(cands)                            # exploration reaches every one


def test_with_no_progress_anywhere_the_draw_is_uniform_among_lookable_things():
    got = _draws([], ["a", "b", "c", "w"], ["a", "b", "c"], n=9000)
    shares = [got[e] / 9000 for e in ("a", "b", "c")]
    for s in shares:
        assert abs(s - ((1 - SL.AIM_EXPLORE) / 3 + SL.AIM_EXPLORE / 4)) < 0.02


def test_a_thing_whose_checks_get_better_draws_more_aims_than_a_flat_one():
    W = SL.NUDGE_CURIO_WINDOW
    recs = _learning("a", 2 * W) + _flat("b", 2 * W)
    got = _draws(recs, ["a", "b"], ["a", "b"])
    assert got["a"] > 4 * got["b"]
    # the share: 0.9 x (1 / 1.1) + 0.05 for a; 0.9 x (0.1 / 1.1) + 0.05 for b
    assert abs(got["a"] / 6000 - (0.9 / 1.1 + 0.05)) < 0.02


def test_with_nothing_lookable_every_aim_explores():
    got = _draws([], ["w1", "w2"], [], n=2000)
    assert abs(got["w1"] / 2000 - 0.5) < 0.05


def test_the_draw_is_deterministic_by_its_seed():
    assert [SL._aim_pick([], ["a", "b", "w"], ["a", "b"], f"aim:{i}") for i in range(50)] == \
           [SL._aim_pick([], ["a", "b", "w"], ["a", "b"], f"aim:{i}") for i in range(50)]


# ---- in the cycle -----------------------------------------------------------------------

class _Emb:
    model = "fake"

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.random.default_rng(sum(map(ord, t)) * 7919 % 2**32).normal(size=DIM)
            out.append(list(v / np.linalg.norm(v)))
        return out


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(words=12):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    fld = EntityField(MemBackend(), _Emb(), "aims@test")
    v = np.random.default_rng(1).normal(size=DIM)
    fld.current = lambda now=None: v / np.linalg.norm(v)
    srv.efield = fld
    est._srv = srv
    rec = [{"ts": T0, "kind": "birth", "by": "operator"},
           {"ts": T0 + 1, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9}]
    p = SL.record_path(srv)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rec))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    for k in ("ball", "die"):
        assert TB.add(srv, k, locked_ok=True)["ok"]
    heard = []
    for i in range(words):                                   # heard words: a field spot, no getter
        eid = NS + f"word{i}"
        est.upsert(QuantumEntity(eid=eid, cls="Component", properties={"name": f"word{i}"}), embedder=None)
        TB.place(srv, eid)
        heard.append(eid)
    SW.join(srv, heard, "test")
    SL.set_mode(srv, "on")
    SL._rt(srv)["isolate"] = {"feel", "nudge"}               # no looks: the aims alone
    return srv, heard


def test_in_the_cycle_aims_land_mostly_on_things_it_can_look_at(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 11.0)                # a new aim every cycle
    srv, heard = _seat()
    look = set(SL._targets(srv))
    assert look and not look & set(heard)
    aims = []
    for k in range(200):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    rt_lines = [json.loads(ln) for ln in SL.record_path(srv).read_text().splitlines() if ln.strip()]
    aims = {c["aim_n"]: c["aim_eid"] for c in rt_lines if c.get("kind") == "cycle" and c.get("aim_eid")}
    assert len(aims) > 150
    on = sum(1 for e in aims.values() if e in look) / len(aims)
    assert on > 0.8                                          # ~0.9 + 0.1 x 2/14; random was 2/14
    assert any(e in heard for e in aims.values())            # exploration still reaches the words
