"""#333 part 3g: Blurp's position is T + N.

The field keeps two running pulls with ONE decay (CURRENT_TAU_S): the THINGS current, pulled by
each event's NNLS fit (#335) and by a person's poke toward the toy's spot, and the NEWS current,
pulled by the residual. Blurp's position is unit(T + N): T follows the things current with
PUSH_GAIN, N follows the news current with PUSH_GAIN, its own stride moves T only. The cycle line
logs |T|, |N| and the 3 nearest world things; a restart keeps T and N.
"""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.efield import CURRENT_TAU_S, EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from test_self_loop import DIM, _cycles, _dir, _record, _seat  # noqa: E402


class _Emb:
    model = "fake"

    def embed(self, texts):
        return [list(_dir(sum(map(ord, t)))) for t in texts]


def _field():
    return EntityField(MemBackend(), _Emb(), "tn@test")


class _Parts:
    """A steerable world: a things current and a news current (None: absent)."""
    def __init__(self, t=None, n=None):
        self.t, self.n = t, n

    def current_parts(self, now=None):
        return (None if self.t is None else np.array(self.t, dtype=float),
                None if self.n is None else np.array(self.n, dtype=float))

    def current(self, now=None):
        parts = [p for p in self.current_parts() if p is not None]
        if not parts:
            return None
        s = np.sum(parts, axis=0)
        return s / np.linalg.norm(s)


def _on(world, d=None):
    srv = _seat(d)
    srv.efield = world
    SL.set_mode(srv, "on")
    SL._entity(srv).position = [float(x) for x in _dir(9)]
    return srv


# ---- the field's two pulls -----------------------------------------------------------------

def test_a_fit_pulls_the_things_current_and_a_residual_pulls_the_news_current():
    fld = _field()
    assert fld.current_parts() == (None, None)
    fld.pull_things("s", _dir(1))
    t, n = fld.current_parts()
    assert n is None and float(t @ _dir(1)) == pytest.approx(1.0)
    fld.pull_current("s", _dir(2))
    t, n = fld.current_parts()
    assert float(t @ _dir(1)) == pytest.approx(1.0) and float(n @ _dir(2)) == pytest.approx(1.0)


def test_both_pulls_halve_on_one_tau():
    fld = _field()
    fld.pull_things("s", _dir(1))
    fld.pull_current("s", _dir(2))
    half = CURRENT_TAU_S * float(np.log(2.0))
    assert fld._thing_w["s"] == pytest.approx(1.0) and fld._now_w["s"] == pytest.approx(1.0)
    t, n = fld.current_parts(now=fld._now_ts["s"] + half)
    w_t = fld._thing_w["s"] * float(np.exp(-half / CURRENT_TAU_S))
    assert w_t == pytest.approx(0.5)
    fld.pull_things("s", _dir(1), weight=0.0, now=fld._thing_ts["s"] + half)
    fld.pull_current("s", _dir(2), now=fld._now_ts["s"] + half)   # a pull a half-life later
    assert fld._thing_w["s"] == pytest.approx(0.5, abs=1e-6)       # 1 halved, plus a pull of 0
    assert fld._now_w["s"] == pytest.approx(1.5, abs=1e-6)         # 1 halved, plus 1


def test_the_whole_current_is_both_pulls_together():
    fld = _field()
    fld.pull_things("s", _dir(1))
    fld.pull_current("s", _dir(2))
    c = fld.current()
    want = _dir(1) + _dir(2)
    assert float(c @ (want / np.linalg.norm(want))) == pytest.approx(1.0, abs=1e-6)


def test_a_persons_poke_pulls_the_things_current_toward_the_toy(monkeypatch):
    fld = _field()
    spot = _dir(5)
    st = types.SimpleNamespace(state=spot)
    monkeypatch.setitem(TB._SEAT, "srv", types.SimpleNamespace(efield=fld))
    monkeypatch.setattr(fld, "get", lambda eid: st)
    TB._pull_field("ball", None, "alice")
    t, n = fld.current_parts()
    assert n is None and float(t @ spot) == pytest.approx(1.0)
    assert fld._thing_w["toy:alice"] == pytest.approx(TB.POKE_PULL) and TB.POKE_PULL == 1.0


# ---- the position --------------------------------------------------------------------------

def _parts(srv):
    return SL._parts(srv, np.asarray(SL._entity(srv).position, dtype=float))


def test_a_fresh_blurp_is_all_t_and_no_n():
    srv = _on(_Parts())
    pos = np.asarray(SL._entity(srv).position, dtype=float)
    t, n = _parts(srv)
    assert np.allclose(t, pos) and not n.any()


def test_t_follows_the_things_current_and_n_the_news_current_each_at_push_gain():
    srv = _on(_Parts(t=_dir(1), n=_dir(2)))
    pos0 = np.asarray(SL._entity(srv).position, dtype=float)
    SL.cycle(srv, now=1000.0)
    t, n = _parts(srv)
    mine = SL.STEP * SL._seed_dir("1", DIM)                  # no aim yet: a babble stride
    assert np.allclose(t, pos0 + mine + SL.PUSH_GAIN * (_dir(1) - pos0))
    assert np.allclose(n, SL.PUSH_GAIN * _dir(2))
    pos = np.asarray(SL._entity(srv).position, dtype=float)
    assert np.allclose(pos, (t + n) / np.linalg.norm(t + n))   # its position is unit(T + N)


def test_its_stride_moves_t_only(monkeypatch):
    a = _on(_Parts(n=_dir(2)))
    SL.cycle(a, now=1000.0)
    monkeypatch.setattr(SL, "STEP", 0.25)                    # a longer babble stride
    b = _on(_Parts(n=_dir(2)))
    SL.cycle(b, now=1000.0)
    (ta, na), (tb, nb) = _parts(a), _parts(b)
    assert np.allclose(na, nb) and np.allclose(na, SL.PUSH_GAIN * _dir(2))   # N never felt the stride
    assert not np.allclose(ta, tb)                                           # T did


def test_a_still_world_moves_nothing_but_the_stride():
    srv = _on(_Parts())
    t0 = np.asarray(SL._entity(srv).position, dtype=float)
    SL.cycle(srv, now=1000.0)
    t, n = _parts(srv)
    assert not n.any()
    assert np.allclose(t, t0 + SL.STEP * SL._seed_dir("1", DIM))
    rec = [r for r in _record(srv) if r["kind"] == "cycle"][-1]
    assert rec["still"] is True


def test_n_fades_when_the_news_stops_but_things_still_pull():
    srv = _on(_Parts(t=_dir(1), n=_dir(2)))
    SL.cycle(srv, now=1000.0)
    n1 = _parts(srv)[1]
    srv.efield.n = None
    SL.cycle(srv, now=1010.0)
    n2 = _parts(srv)[1]
    assert np.allclose(n2, (1 - SL.PUSH_GAIN) * n1)


def test_the_cycle_line_logs_t_n_and_the_nearest_world_things():
    srv = _on(_Parts(t=_dir(1), n=_dir(2)))
    SL.cycle(srv, now=1000.0)
    rec = [r for r in _record(srv) if r["kind"] == "cycle"][-1]
    t, n = _parts(srv)
    assert rec["norm_t"] == pytest.approx(float(np.linalg.norm(t)), abs=1e-3)
    assert rec["norm_n"] == pytest.approx(float(np.linalg.norm(n)), abs=1e-3)
    assert rec["n"] == 1                                       # the cycle number is still the cycle number
    assert isinstance(rec["near"], list) and len(rec["near"]) <= SL.NEAR_LOG and SL.NEAR_LOG == 3


def test_near_lists_world_things_only_nearest_first(monkeypatch):
    from membrane.entities import MEMBRANE_NS, QuantumEntity
    srv = _on(_Parts(t=_dir(1)))
    pos = np.asarray(SL._entity(srv).position, dtype=float)
    spots = {"a": pos, "b": -pos, "c": SL._unit(pos + 0.5 * _dir(3)), "d": SL._unit(pos + _dir(4)),
             "outside": pos}
    world = {MEMBRANE_NS + k for k in ("a", "b", "c", "d")}
    monkeypatch.setattr(SL, "_in_world", lambda srv, eid: eid in world)
    monkeypatch.setattr(SL, "_spot", lambda srv, eid: spots.get(eid.replace(MEMBRANE_NS, "")))
    for k in spots:
        srv.entity_store.docs[MEMBRANE_NS + k] = QuantumEntity(
            eid=MEMBRANE_NS + k, cls="Component", properties={"name": k})
    got = SL._near(srv, pos)
    ids = [e.replace(MEMBRANE_NS, "") for e, _ in got]
    assert "outside" not in ids and len(got) == SL.NEAR_LOG
    cos = [c for _, c in got]
    assert cos == sorted(cos, reverse=True) and ids[0] == "a"


def test_a_restart_keeps_t_and_n(tmp_path):
    srv = _on(_Parts(t=_dir(1), n=_dir(2)), tmp_path)
    _cycles(srv, 3)
    t, n = _parts(srv)
    assert n.any()
    srv.entity_store.flush()
    srv2 = _seat(tmp_path)                                   # a new process: RAM gone, files kept
    srv2.efield = _Parts()
    SL._rt(srv2).pop("parts", None)
    SL.load_state(srv2)
    t2, n2 = SL._parts(srv2, np.asarray(SL._entity(srv2).position, dtype=float))
    assert np.allclose(t2, t) and np.allclose(n2, n)


def test_without_a_saved_split_t_is_the_position_and_n_is_zero(tmp_path):
    srv = _on(_Parts(), tmp_path)
    SL._rt(srv).pop("parts", None)
    SL.load_state(srv)
    pos = np.asarray(SL._entity(srv).position, dtype=float)
    t, n = SL._parts(srv, pos)
    assert np.allclose(t, pos) and not n.any()
