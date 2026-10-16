"""#161: THE SELF LOOP ON THE VIEWER -- an amoeba in the field's space.

Each cycle of self (self_loop.py) is drawn in the 3D viewer: where it started,
where it meant to go (its own step), where it expected to end up (its step +
the push it learned), where it ended up (the world pushed it), and where it
has learned the current pulls. The seat places the points into every cached
projection (field3d.place) and sends one small frame -- cube coordinates and
numbers, never vectors and never text.
"""
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import field3d as F3  # noqa: E402
from membrane import fieldpulse as FP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

DIM = 8


class _Hub:
    def __init__(self):
        self.frames = []

    def publish(self, f):
        self.frames.append(f)


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)


def _proj(n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = [{"eid": f"f:e{i}", "kind": "f", "surface": f"e{i}", "accr": i,
             "state": rng.normal(size=DIM).tolist()} for i in range(n)]
    return F3.build(lambda: iter(rows), lambda s: s, "")


def _vec(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


def _seat(with_projection=True):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = _World(_vec(1))
    hub = _Hub()
    srv._field_pulse = FP.FieldPulse(srv, hub=hub)
    if with_projection:
        proj = _proj()
        srv._field3d = {proj.pid: proj}
    SL.bind(srv)
    SL.set_mode(srv, "on")
    return srv, hub


def _self_frames(hub):
    return [f for f in hub.frames if f.get("type") == "self.cycle"]


def test_a_cycle_reaches_the_viewer_as_placed_points():
    srv, hub = _seat()
    for i in range(3):
        SL.cycle(srv, now=1000.0 + i * SL.PACE_S)
    srv._field_pulse.beat()
    frames = _self_frames(hub)
    assert len(frames) == 1                         # coalesced: the latest cycle per beat
    f = frames[0]
    assert f["pid"] in srv._field3d
    for k in ("from", "intent", "expected", "at"):
        p = f["points"][k]
        assert len(p) == 3 and all(0.0 <= x <= 1.0 for x in p)
    assert "pull" in f["points"]                    # after a few cycles it has a model of the current
    rec = [r for r in SL._read(srv) if r["kind"] == "cycle"][-1]
    assert f["n"] == rec["n"] and abs(f["gap"] - rec["gap"]) < 1e-9
    for k in ("naive", "push", "rung", "bar", "skill", "mode"):
        assert k in f


def test_the_frame_carries_no_vectors_and_no_text():
    srv, hub = _seat()
    SL.cycle(srv, now=1000.0)
    srv._field_pulse.beat()
    f = _self_frames(hub)[0]

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            assert len(v) <= 3, "a vector rode the frame"
            for x in v:
                walk(x)
        elif isinstance(v, str):
            assert len(v) <= 24, "text rode the frame"
    walk(f)


def test_no_projection_open_the_numbers_still_go_out():
    # the switches' bar updates live from these, viewer open or not
    srv, hub = _seat(with_projection=False)
    SL.cycle(srv, now=1000.0)
    srv._field_pulse.beat()
    frames = _self_frames(hub)
    assert len(frames) == 1
    assert frames[0]["pid"] is None and "points" not in frames[0]
    for k in ("rung", "bar", "skill", "mode", "waiting_on", "next", "cycles", "held"):
        assert k in frames[0]


def test_the_frame_tells_the_switch_what_the_bar_waits_on():
    srv, hub = _seat()
    SL.cycle(srv, now=1000.0)
    srv._field_pulse.beat()
    f = _self_frames(hub)[0]
    st = SL.status(srv)
    assert f["waiting_on"] == st["waiting_on"] and f["cycles"] == st["cycles"]


def test_the_home_bar_has_the_self_switch_where_the_star_was():
    ui = Path(__file__).resolve().parent.parent / "membrane" / "ui"
    home = (ui / "home.js").read_text()
    assert '<button class="selfbtn"' in home
    assert 'class="start"' not in home                 # the star (HA's default-dashboard pin) is gone
    assert "_toggleSelf()" in home and "_selfView()" in home
    panel = (ui / "panel.js").read_text()
    assert "_selfView() {" in panel
    assert 'ev.type === "self.cycle"' in panel          # the bar moves with each cycle


def test_the_frame_drains():
    srv, hub = _seat()
    SL.cycle(srv, now=1000.0)
    srv._field_pulse.beat()
    srv._field_pulse.beat()
    assert len(_self_frames(hub)) == 1


def test_a_cycle_without_a_pulse_still_runs():
    srv, _ = _seat()
    srv._field_pulse = None
    assert SL.cycle(srv, now=1000.0) is not None


def test_the_viewer_draws_the_amoeba():
    js = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "field3d.js").read_text()
    assert 't === "self.cycle"' in js
    assert "_selfCycle(" in js
    for words in ("meant to go", "expected", "ended up", "the current pulls"):
        assert words in js
