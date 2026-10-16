"""#161: STEER -- self uses what it learned to land where it means to.

Rung 1 taught self to predict the current's push; it never used that. Once rung
1 has passed, self holds an AIM for a while and leans into the current by the
push it expects (step = aim - here - expected push, a bigger stride than its
babble). The record keeps how close it lands to its aim against where the same
stride would have landed without leaning (the naive landing) -- measured, never
claimed: its model is what gets it there, or nothing does.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import fieldpulse as FP  # noqa: E402
from membrane import field3d as F3  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

DIM = 16


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)


class _Hub:
    def __init__(self):
        self.frames = []

    def publish(self, f):
        self.frames.append(f)


def _dir(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


def _seat(passed=(1,)):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = _World(_dir(1))
    SL.bind(srv)
    SL.set_limit(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _cycles(srv):
    return [r for r in _record(srv) if r["kind"] == "cycle"]


def test_it_does_not_steer_before_rung_one():
    srv = _seat(passed=())
    _run(srv, 10)
    assert not any("aim_err" in r for r in _cycles(srv))


def test_once_rung_one_is_earned_it_lands_near_its_aim_because_it_leans_into_the_current():
    srv = _seat()
    _run(srv, 120)
    late = [r for r in _cycles(srv) if "aim_err" in r][-30:]
    err = sum(r["aim_err"] for r in late) / len(late)
    naive = sum(r["aim_naive"] for r in late) / len(late)
    assert err < 0.5 * naive                       # its model, not luck, gets it there
    st = SL.status(srv)["steer"]                   # the panel reads the last 20
    assert st["skill"] > 0.5 and st["err"] < 0.5 * st["naive"]


def test_without_a_model_of_the_current_it_cannot_lean():
    srv = _seat()
    _run(srv, 1)
    first = _cycles(srv)[0]
    assert first["aim_err"] == pytest.approx(first["aim_naive"])   # nothing learned yet: the same landing


def test_it_holds_an_aim_for_a_while():
    srv = _seat()
    _run(srv, SL.AIM_HOLD + 5)
    aims = [r["aim_n"] for r in _cycles(srv) if "aim_n" in r]
    assert aims.count(aims[0]) == SL.AIM_HOLD and len(set(aims)) == 2


def test_the_viewer_gets_the_aim_and_labels_its_markers():
    srv = _seat()
    hub = _Hub()
    srv._field_pulse = FP.FieldPulse(srv, hub=hub)
    rng = np.random.default_rng(0)
    rows = [{"eid": f"f:e{i}", "kind": "f", "surface": f"e{i}", "accr": i,
             "state": rng.normal(size=DIM).tolist()} for i in range(60)]
    proj = F3.build(lambda: iter(rows), lambda s: s, "")
    srv._field3d = {proj.pid: proj}
    _run(srv, 3)
    srv._field_pulse.beat()
    f = [x for x in hub.frames if x.get("type") == "self.cycle"][-1]
    assert len(f["points"]["aim"]) == 3
    js = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "field3d.js").read_text()
    for label in ("wants", "pushes to", "expects", "pull"):
        assert f'"{label}"' in js
