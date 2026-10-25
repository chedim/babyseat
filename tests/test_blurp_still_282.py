"""#282: Blurp freezes after a restart while nobody speaks.

The field's current (efield.current()) is RAM only and None until a session
speaks, and self_loop.cycle() returned None while it was None. Since #272 the
chat is quiet from rung 7 on, so after a seat restart nothing spoke and self
sat still: no looks, no ball turns, until a person chatted. Two fixes:

  1. PLAYING COUNTS AS SPEAKING: a person's poke on a toy pulls the field's
     current toward that toy (its spot on the field), under the person's own
     session key (toy:<person>), with the weight one chat event's activity
     gives (efield.pull_current, the same path _apply uses). Self's own rolls,
     the world's changes and the model's pokes never pull it.
  2. A STILL WORLD STILL CYCLES: with no current self cycles anyway with a
     zero push (the current is where it stands). The cycle line says so
     (`still: true`, `change: "still"`), it learns nothing about the current
     from it, and the rung stats that read push / gap / naive skip it -- a
     run of still cycles neither passes nor fails a rung by itself.
"""
import json
import os
import shutil
import subprocess
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
from tests._self_cap import cap  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import CURRENT_TAU_S, EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
BALL = MEMBRANE_NS + "Component:" + TB.BALL


def _unit(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


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
    monkeypatch.setattr(SL, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model at rung 7")))
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(limit=7, placed=True):
    """A seat just restarted: a real field nobody has spoken to (no current),
    self where it stood before the restart, rungs 1-6 passed, the ball out."""
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    fld = EntityField(MemBackend(), _Emb(), "still@test")
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=fld)
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
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    cap(srv, limit)
    SL.set_mode(srv, "on")
    TU.sync(srv)                                  # the toys before it
    if TB.peek(TB.BALL) is None:                  # #333: meet's pass no longer counts -- the ball, out as before
        TB.add(srv, "ball", locked_ok=True)
    if placed:
        SL._entity(srv).position = [float(x) for x in _unit(3)]
    assert fld.current() is None                  # nobody has spoken since the boot
    return srv


def _cycles(srv, n, t0=10_000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _kind(srv, k):
    return [r for r in SL._read(srv) if r.get("kind") == k]


# ---- 2. a still world still cycles ------------------------------------------------------

def test_with_no_current_self_still_cycles_and_looks():
    srv = _seat()
    pos0 = list(SL._entity(srv).position)
    out = SL.cycle(srv, now=10_000.0)
    assert out is not None                                  # it did not freeze
    cyc = _kind(srv, "cycle")
    assert len(cyc) == 1
    c = cyc[-1]
    assert c["still"] is True and c["change"] == "still"
    assert c["push"] <= SL.CALM                            # the world did not push: it is still
    assert len(SL._entity(srv).position) == len(pos0)
    _cycles(srv, 10, t0=10_000.0 + SL.PACE_S)
    assert len(_kind(srv, "cycle")) == 11
    assert _kind(srv, "reach"), "self keeps looking at things in a still world"


def test_a_still_cycle_never_writes_aim_lines_or_learns_the_current():
    srv = _seat()
    SL._rt(srv)["model"] = m0 = {"mp": list(_unit(4)), "mw": list(0.01 * _unit(5)), "cov": -0.001, "var": 0.01}
    _cycles(srv, 5)
    assert len(_kind(srv, "cycle")) == 5
    assert SL._rt(srv)["model"] == m0                       # a still world teaches nothing about the current
    for c in _kind(srv, "cycle"):
        assert c["still"] is True
        assert "aim_err" not in c and "aim_naive" not in c   # the steer stat never reads a still cycle


def test_without_a_place_to_stand_and_no_current_nothing_runs():
    srv = _seat(placed=False)
    SL._entity(srv).position = None
    assert SL.cycle(srv, now=10_000.0) is None              # never born into a still world: no dimension
    assert _kind(srv, "cycle") == []


def test_the_nudges_happen_with_no_current(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * SL.PACE_S)      # #333: a fresh aim (a thing) every other cycle (#343: s)
    # (a nudge needs a held aim: it nudges only once its distance to it fell, decision 1)
    srv = _seat()
    SL._rt(srv)["isolate"] = set(SL._rt(srv)["isolate"]) | {"nudge"}
    _cycles(srv, 40)
    nudges = _kind(srv, "nudge")
    assert nudges, "self nudges the things of its world though nobody spoke"
    assert any(r["eid"] == BALL and r["rc"] == SL.NUDGE_MOVED for r in nudges)
    assert TB.state(TB.BALL)["side"] == "other"             # the ball rolled to the person by a nudge
    assert srv.efield.current() is None                     # self's own nudge never pulls the current


# ---- the rung stats skip still cycles ----------------------------------------------------

def _still(n, t0=1.0):
    return [{"ts": t0 + i, "kind": "cycle", "n": i + 1, "act": "move", "step": SL.STEP, "push": 0.0,
             "gap": 0.0, "naive": 0.0, "change": "still", "still": True} for i in range(n)]


def _world(n, gap, naive, t0=1000.0):
    return [{"ts": t0 + i, "kind": "cycle", "n": 1000 + i, "act": "move", "step": SL.STEP, "push": 0.2,
             "gap": gap, "naive": naive, "change": "world", "aim_n": 1, "aim_err": gap, "aim_naive": naive}
            for i in range(n)]


def test_still_cycles_alone_neither_pass_nor_fail_a_rung():
    recs = _still(200)
    r1 = SL._rung1(recs)
    assert r1["passed"] is False and r1["crit"]["pushes"] == 0.0     # nothing felt: no evidence either way
    assert SL._rung2_stats(recs, {}) == {}
    assert SL._rung2(recs)["passed"] is False
    assert SL._steer_stats(recs) is None


def test_still_cycles_do_not_move_rung_one_or_the_steer_stat():
    good = _world(SL.RUNG1_CYCLES, gap=0.01, naive=0.2)
    bad = _world(SL.RUNG1_CYCLES, gap=0.19, naive=0.2)
    for base in (good, bad):
        mixed = base + _still(100, t0=5000.0)
        assert SL._rung1(mixed) == SL._rung1(base)                  # a pass stays a pass, a fail a fail
        assert SL._steer_stats(mixed) == SL._steer_stats(base)
    assert SL._rung1(good)["passed"] is True and SL._rung1(bad)["passed"] is False


def test_status_and_the_sheet_read_still_cycles():
    srv = _seat()
    _cycles(srv, 6)
    st = SL.status(srv)
    assert st["cycles"] == 6
    sheet = SS.compute(srv, now=10_000.0 + 6 * SL.PACE_S)
    assert isinstance(sheet, dict)


# ---- 1. playing counts as speaking --------------------------------------------------------

def test_a_persons_poke_pulls_the_current_toward_the_toy():
    srv = _seat()
    fld = srv.efield
    TB.roll_self(TB.BALL)                                   # self serves: the ball is on the person's side
    assert fld.current() is None
    out = TB.poke(TB.BALL, "roll", by="alice")
    assert out["ok"]
    cur = fld.current()
    assert cur is not None
    spot = fld.get(field_id(BALL)).state
    assert float(np.asarray(cur) @ (np.asarray(spot) / np.linalg.norm(spot))) > 0.99
    assert "toy:alice" in fld._thing_ctx                    # under the person's own key (#333 part 3g: the things current)
    assert fld._thing_w["toy:alice"] == pytest.approx(1.0)  # one event's activity, as a chat line gives
    assert "toy:alice" not in fld._now                      # a poke is no news


def test_the_pull_fades_like_a_chat_lines():
    srv = _seat()
    fld = srv.efield
    TB.roll_self(TB.BALL)
    TB.poke(TB.BALL, "roll", by="alice")
    t = fld._thing_ts["toy:alice"]
    assert fld.current(now=t + 0.1 * CURRENT_TAU_S) is not None
    assert fld.current(now=t + 10 * CURRENT_TAU_S) is None     # gone, as a quiet stream is


def test_self_the_world_and_the_model_never_pull_the_current():
    srv = _seat()
    fld = srv.efield
    TB.roll_self(TB.BALL)                                   # self's own roll
    assert TB.poke(TB.BALL, "roll", by="model")["ok"]       # the model's poke
    TB.roll_self(TB.BALL)
    TB.add(srv, "die", locked_ok=True)                      # unlocked at rung 5: already out
    assert TB.state("toy:die")["kind"] == "die"
    assert TB.poke("toy:die", "roll", by="model")["ok"]
    st = TB.state("toy:die")
    assert TB.tick("toy:die", now=float(st["next_at"]) + 1.0)   # the world's change
    assert fld.current() is None
    assert not any(k.startswith("toy:") for k in fld._now)


def test_after_a_poke_self_feels_a_real_push_again():
    srv = _seat()
    _cycles(srv, 2)
    TB.roll_self(TB.BALL)
    TB.poke(TB.BALL, "roll", by="alice")
    _cycles(srv, 1, t0=20_000.0)
    c = _kind(srv, "cycle")[-1]
    assert not c.get("still") and c["change"] == "world" and c["push"] > SL.CALM


# ---- the page names a still cycle --------------------------------------------------------

def _js(mod, expr):
    js = "const M=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / mod), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_the_activity_lines_say_the_world_was_still():
    rec = {"kind": "cycle", "act": "move", "gap": 0.0, "naive": 0.0, "push": 0.0, "change": "still", "still": True}
    assert _js("home.js", "M.brainCaption(%s, 'dmitrii')" % json.dumps(rec)) == "Blurp moved — the world was still"
    assert _js("panel.js", "M.selfLine(%s)" % json.dumps(rec)) == "move (still world)"
    moved = {"kind": "cycle", "act": "move", "gap": 0.0312}
    assert _js("home.js", "M.brainCaption(%s, 'dmitrii')" % json.dumps(moved)) == \
        "Blurp moved — it landed 0.031 from where it expected"
