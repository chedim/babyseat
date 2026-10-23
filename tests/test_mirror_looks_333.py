"""#333 part 3d: RECOGNIZE ITSELF (rung 9), rebuilt on looks at the mirror.

- The mirror toy keeps showing Blurp's moves: the feed is the world's
  physics, as a real mirror reflects, and it runs whether Blurp looks or not.
- Rows come only from Blurp's looks at the mirror: the mirror has a getter
  (the glass and the stranger), and at each look Blurp predicts both sides
  from its own last MIRROR_OWN moves (_mirror_predict) before it reads them.
- The test is #312's, unchanged: over MIRROR_LOOKS looked rows the glass
  reads as body and the stranger as other.
- What a look at the mirror can teach is the glass prediction's expected
  error (its residual over the glass's variance), floored at INFO_FLOOR, in
  place of the changed/unchanged entropy; the curiosity tilt reads the glass
  error series.
- Prerequisites: feel and nudge (first reach), replacing feel and meet.
"""
import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

DIM = 16
MIRROR = MEMBRANE_NS + "Component:toy:mirror"
OTHER = MEMBRANE_NS + "Component:toy:slow"


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    yield tmp_path / "data"


class _Drift:
    """A current that wanders: self leans into it, so its moves have news."""
    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed)
        self.c = self.rng.normal(size=DIM)
        self.c /= np.linalg.norm(self.c)

    def current(self):
        self.c = self.c + 0.05 * self.rng.normal(size=DIM)
        self.c /= np.linalg.norm(self.c)
        return self.c.copy()

    def write_events(self, evs, session=""):
        return []


def _seat(rungs=(1, 2, 13), isolate=("reach", "mirror"), toys=("mirror",), world=None, sub=""):
    d = Path(os.environ["MEMBRANE_DATA_DIR"]) / sub
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.efield = _Drift(0) if world is None else world
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in rungs:
        SL._append(srv, {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9, "ladder": SL.LADDER_V})
    rt = SL._rt(srv)
    rt["mode"] = "on"
    rt["isolate"] = None if isolate is None else set(isolate)
    for k in toys:
        assert TB.add(srv, k, locked_ok=True)["ok"]
    return srv


def _record(srv):
    return [json.loads(ln) for ln in SL.record_path(srv).read_text().splitlines() if ln.strip()]


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)


ENOUGH = int(round(SL.MIRROR_LAG / SL.PACE_S)) + SL.MIRROR_MIN_FIT + SL.MIRROR_LOOKS + 120


# ---- 1. the tree ---------------------------------------------------------------------------

def test_mirror_is_built_on_feel_and_first_reach():
    a = SL.ACHIEVEMENTS["mirror"]
    assert a["built"] is True and a["rung"] == 9 and a["name"] == "recognize itself"
    assert a["prereqs"] == ("feel", "nudge")                        # Q2: no person needed for Lv 7
    assert "meet" not in a["why_prereqs"] and "first reach" in a["why_prereqs"]
    assert "mirror" in SL.BUILT and SL.PROGRESS[9] is SL._rung9
    assert SS.BUILT_AT["mirror"]["pr"] == 477                        # rebuilt: built from this PR, not #312's (#314)
    import datetime as _dt
    assert SS.BUILT_AT["mirror"]["merged"] == _dt.datetime(2026, 10, 23, 6, 51, 58,      # the PR's opening time
                                                           tzinfo=_dt.timezone.utc).timestamp()   # until its merge's


def test_it_opens_with_feel_and_first_reach_accepted_no_meet_needed():
    srv = _seat(rungs=(1, 2, 13), isolate=None, toys=())
    assert "mirror" in SL.status(srv)["next"]
    srv = _seat(rungs=(1, 2), isolate=None, toys=(), sub="no-reach")   # first reach not earned: closed
    assert "mirror" not in SL.status(srv)["next"]


# ---- 2. the toy: a getter, and the feed is physics ---------------------------------------------

def test_the_mirror_has_a_getter_and_is_a_thing_to_look_at():
    srv = _seat()
    ent = srv.entity_store.docs[MIRROR]
    assert (ent.properties or {}).get("getter_script")
    assert MIRROR in SL._targets(srv)


def test_an_old_mirror_gets_its_getter_at_boot():
    srv = _seat()
    ent = srv.entity_store.docs[MIRROR]
    pp = dict(ent.properties)
    pp.pop("getter_script")                                          # a mirror from #312: no getter
    ent.properties = pp
    srv.entity_store.upsert(ent, embedder=None)
    assert MIRROR not in SL._targets(srv)
    TB.bind(srv)
    assert srv.entity_store.docs[MIRROR].properties.get("getter_script") == TB.STUB_GET
    assert MIRROR in SL._targets(srv)


def test_the_mirror_keeps_showing_its_moves_without_a_look():
    """The feed is the world's physics: it runs while Blurp never looks (and
    while recognize itself is closed) -- and no row is written without a look."""
    srv = _seat(rungs=(1,), isolate=(), toys=("mirror",))
    _run(srv, 10)
    st = TB.peek("toy:mirror")
    assert st["moves"] and st["glass"] is not None
    assert not [r for r in _record(srv) if r["kind"] == "mirror"]


# ---- 3. rows come only from looks --------------------------------------------------------------

def test_no_row_without_a_look():
    srv = _seat(toys=("mirror", "slow"))
    _run(srv, 60)
    recs = _record(srv)
    looks = {r["ts"] for r in recs if r["kind"] == "reach" and r["eid"] == MIRROR and r["answer"] != "none"}
    rows = [r for r in recs if r["kind"] == "mirror"]
    assert rows and looks
    assert {r["ts"] for r in rows} == looks                          # one row per look at the mirror, no other
    assert any(r["kind"] == "reach" and r["eid"] == OTHER for r in recs)   # it looked elsewhere too
    assert len(rows) < 60
    assert all(r["look"] is True and r["eid"] == MIRROR and r["toy"] == "toy:mirror" for r in rows)
    assert all(len(r["own"]) == SL.MIRROR_OWN for r in rows[SL.MIRROR_OWN:])   # its own last moves


def test_a_seat_that_never_looks_at_the_mirror_never_passes():
    srv = _seat(isolate=("mirror",))                                  # recognize itself open, no looks
    _run(srv, ENOUGH)
    recs = _record(srv)
    assert not [r for r in recs if r["kind"] == "mirror"]
    assert not [r for r in recs if r["kind"] == "achieve" and r["id"] == "mirror"]
    assert SL._rung9(recs)["crit"]["looks"] == 0.0


def test_a_seat_that_looks_passes_on_the_glass_and_reads_the_stranger_as_other():
    srv = _seat()
    _run(srv, ENOUGH)
    recs = _record(srv)
    p = SL._rung9(recs)
    assert p["passed"], p
    assert p["labels"] == {"glass": "body", "stranger": "other"}
    ach = [r for r in recs if r["kind"] == "achieve" and r["id"] == "mirror"]
    assert ach and ach[0]["ladder"] == SL.LADDER_V and ach[0]["rung"] == 9
    assert "mirror" in SL._earned(SL._read(srv))


def test_old_fed_rows_never_count():
    """#312's rows (fed every cycle, before the void) are history: the test
    and the predictions read only looked rows."""
    rng = np.random.default_rng(0)
    s = rng.normal(size=300)
    old = []
    for t in range(300):
        r = {"ts": float(t), "kind": "mirror", "n": t, "toy": "toy:mirror", "s": round(float(s[t]), 4),
             "glass": round(float(s[t - 1]), 4) if t else None,
             "stranger": round(float(s[t - 40]), 4) if t >= 40 else None}
        pb = {side: SL._mirror_predict(old, side, r["s"]) for side in ("glass", "stranger")}
        for side, v in pb.items():
            if v is not None:
                r[f"p_{side}"], r[f"b_{side}"] = round(v[0], 4), round(v[1], 4)
        old.append(r)
    assert SL._rung9(old)["crit"]["looks"] == 0.0
    assert SL._mirror_rows(old) == []


# ---- 4. the prediction at a look ---------------------------------------------------------------

def _looked(n, seed=0, glass_lag=1):
    """Looked rows: each look carries its own last MIRROR_OWN moves (`own`,
    newest first); looks are cycles apart, so a row's moves are not the rows
    before it. The glass shows own[glass_lag], the stranger a move long gone."""
    rng = np.random.default_rng(seed)
    out = []
    for t in range(n):
        own = [round(float(x), 4) for x in rng.normal(size=SL.MIRROR_OWN)]
        out.append({"ts": float(t), "kind": "mirror", "look": True, "toy": "toy:mirror", "eid": MIRROR,
                    "s": own[0], "own": own,
                    "glass": round(own[glass_lag] + 0.1 * float(rng.normal()), 4),
                    "stranger": round(float(rng.normal()), 4)})
    return out


def test_at_a_look_it_predicts_the_glass_from_its_own_last_moves():
    rows = _looked(120)
    own = [0.5, -1.2, 0.3]
    p, b = SL._mirror_predict(rows, "glass", own[0], own=own)
    assert p == pytest.approx(-1.2, abs=0.15)                         # the glass will show the move before
    assert abs(b + 1.2) > 0.3                                         # its past alone can't know it
    assert SL._mirror_predict(rows[:5], "glass", own[0], own=own) is None
    assert SL._mirror_predict(rows, "glass", own[0], own=own[:2]) is None   # too few moves known


def test_scored_looks_pass_the_unchanged_test():
    out = []
    for r in _looked(260, seed=1):
        r = dict(r)
        for side in ("glass", "stranger"):
            pb = SL._mirror_predict(out, side, r["s"], own=r["own"])
            if pb is not None:
                r[f"p_{side}"], r[f"b_{side}"] = round(pb[0], 4), round(pb[1], 4)
        out.append(r)
    p = SL._rung9(out)
    assert p["passed"], p
    assert p["labels"] == {"glass": "body", "stranger": "other"}


# ---- 5. how a look weighs the mirror -------------------------------------------------------------

def _errs_rows(errs, eid=MIRROR):
    """Looked rows whose glass prediction misses by each of errs."""
    return [{"ts": 10.0 + i, "kind": "mirror", "look": True, "toy": "toy:mirror", "eid": eid,
             "s": 0.0, "own": [0.0] * SL.MIRROR_OWN, "glass": (1.0 if i % 2 else -1.0),
             "p_glass": (1.0 if i % 2 else -1.0) - e, "b_glass": 0.0,
             "stranger": 0.0, "p_stranger": 0.0, "b_stranger": 0.0} for i, e in enumerate(errs)]


def test_what_a_look_at_the_mirror_can_teach_is_its_glass_error():
    assert SL._mirror_info([]) == 1.0                                 # nothing predicted yet: all to learn
    bad = _errs_rows([1.0] * 40)
    good = _errs_rows([0.05] * 40)
    assert SL._mirror_info(bad) == pytest.approx(1.0)                 # residual = the glass's variance
    assert SL._mirror_info(good) == pytest.approx(0.0025, abs=1e-3)
    recs = good
    # in place of the entropy: a thing that always changes teaches ~nothing by entropy
    assert SL._teach(recs, MIRROR, 0.999, mirror=MIRROR) == pytest.approx(SL._mirror_info(good))
    assert SL._teach(bad, MIRROR, 0.999, mirror=MIRROR) > 10 * SL._info(0.999)
    assert SL._teach(recs, OTHER, 0.5, mirror=MIRROR) == SL._info(0.5)   # any other thing: the entropy
    assert SL._teach(bad, MIRROR, 0.999, mirror=None) == SL._info(0.999)  # recognize itself closed


def _other_looks(n, p0, p1):
    """Ordinary looks at OTHER whose error falls a little (some progress elsewhere)."""
    return [{"ts": 1000.0 + i, "kind": "reach", "eid": OTHER, "answer": "changed",
             "p": (p0 if i < n // 2 else p1)} for i in range(n)]


def test_the_mirrors_look_weight_rises_while_its_glass_error_falls():
    W = SL.CURIO_WINDOW
    other = _other_looks(2 * W, 0.6, 0.7)                             # a little progress elsewhere
    falling = _errs_rows([0.8] * W + [0.1] * W) + other
    flat = _errs_rows([0.1] * (2 * W)) + other

    def weight(recs):
        tilt = SL._curiosity(recs, [MIRROR, OTHER])
        return max(SL.INFO_FLOOR, SL._teach(recs, MIRROR, 0.999, mirror=MIRROR)) * tilt[MIRROR]
    assert SL._curiosity(falling, [MIRROR, OTHER])[MIRROR] == pytest.approx(1.0)   # the frontier: it is learning the glass
    assert SL._curiosity(flat, [MIRROR, OTHER])[MIRROR] == pytest.approx(SL.CURIO_FLOOR)
    assert weight(falling) > weight(flat)


def test_the_tilt_reads_the_glass_errors_not_the_looks_answers():
    W = SL.CURIO_WINDOW
    looks = [{"ts": 5000.0 + i, "kind": "reach", "eid": MIRROR, "answer": "changed",
              "p": (0.1 if i < W else 0.99)} for i in range(2 * W)]   # its answers' error falls fast...
    recs = _errs_rows([0.1] * (2 * W)) + looks + _other_looks(2 * W, 0.6, 0.7)
    assert SL._curiosity(recs, [MIRROR, OTHER])[MIRROR] == pytest.approx(SL.CURIO_FLOOR)   # ...the glass's is flat
