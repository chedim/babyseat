"""#333 part 3c: FIRST REACH (rung 13), the nudge skill's test.

Does its nudge change what it next sees, beyond the thing's tempo? Over the
last RUNG13_LOOKS (30) nudged looks at one thing (answered, outside every
audit):
1. looks: at least RUNG13_LOOKS of them;
2. skill: the Brier skill of p_n (its nudge-aware prediction) over the tempo
   prediction p on those same looks, 1 - B(p_n)/B(p), at least RUNG13_SKILL
   (0.2);
3. audit (ground truth, never fed to Blurp): among those looks that found a
   change and carry `by`, at least RUNG13_AUDIT_SHARE (0.9) include `self`,
   with at least RUNG13_AUDIT_MIN (5) such looks -- the change was its nudge,
   not the person.
The best thing counts, as in rung 2. `nudge` is built, and its pass line
carries `ladder: 3`."""
import json
import os
import random
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

T0 = 1_700_000_000.0
TOWER = MEMBRANE_NS + "Component:toy:tower:b2"
DRIFT = MEMBRANE_NS + "Component:toy:drift"


def _looks(eid, n, change, p, by, k=1, t0=T0):
    """n nudged looks at eid (k own nudges before each), each with the tempo
    prediction p and a prequential p_n (its k bin's Jeffreys share over the
    earlier looks, 0.5 before any) -- the way the seat records them.
    change(i) -> changed or not; by(i) -> the toy log's changers (or None)."""
    out, c, m = [], 0, 0
    for i in range(n):
        p_n = (c + 0.5) / (m + 1.0)
        x = bool(change(i))
        r = {"ts": t0 + i * 60.0, "kind": "reach", "eid": eid, "p": p, "nudged": k,
             "p_n": round(p_n, 4), "answer": "changed" if x else "unchanged"}
        b = by(i) if x else None
        if b is not None:
            r["by"] = b
        out.append(r)
        c, m = c + x, m + 1
    return out


def _tower(n=30, **kw):
    """A tower block that changes only when nudged: every nudged look finds the change, Blurp's own."""
    return _looks(TOWER, n, lambda i: True, 0.05, lambda i: ["self"], **kw)


def test_the_approved_values():
    assert SL.RUNG13_LOOKS == 30
    assert SL.RUNG13_SKILL == 0.2
    assert SL.RUNG13_AUDIT_SHARE == 0.9
    assert SL.RUNG13_AUDIT_MIN == 5


def test_nudge_is_built_and_its_test_is_rung_13():
    a = SL.ACHIEVEMENTS["nudge"]
    assert a["built"] is True and a["rung"] == 13 and a["prereqs"] == ("feel",)
    assert "nudge" in SL.BUILT
    assert SL.PROGRESS[13] is SL._rung13
    assert SL.SKILL_PASS[13] == SL.RUNG13_SKILL
    assert 13 in SL.LADDER
    assert "nudge" in SS.BUILT_AT


def test_a_tower_that_changes_only_when_nudged_passes():
    p = SL._rung13(_tower())
    assert p["passed"], p
    assert p["eid"] == TOWER
    assert p["skill"] >= SL.RUNG13_SKILL
    assert p["crit"] == {"looks": 1.0, "skill": 1.0, "audit": 1.0}


def test_a_thing_on_its_own_tempo_ignoring_nudges_does_not_pass_even_with_many_nudges():
    rng = random.Random(7)
    flips = [rng.random() < 0.4 for _ in range(400)]
    recs = (_looks(DRIFT, 200, lambda i: flips[i], 0.4, lambda i: ["world"], k=1)
            + _looks(DRIFT, 200, lambda i: flips[200 + i], 0.4, lambda i: ["world"], k=2, t0=T0 + 50_000))
    p = SL._rung13(recs)
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0
    assert p["crit"]["skill"] < 1.0                       # p_n knows nothing p does not
    assert p["skill"] < SL.RUNG13_SKILL


def test_a_thing_changed_by_the_person_right_after_each_nudge_fails_the_audit():
    """The nudge moves nothing; the person changes it right after: p_n
    predicts the change far better than the tempo, but the change is not Blurp's."""
    recs = _looks(TOWER, 30, lambda i: True, 0.05, lambda i: ["operator"])
    p = SL._rung13(recs)
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0 and p["crit"]["skill"] == 1.0
    assert p["crit"]["audit"] < 1.0


def test_the_audit_needs_at_least_5_changes_carrying_by():
    recs = _looks(TOWER, 30, lambda i: True, 0.05, lambda i: ["self"] if i >= 26 else None)
    p = SL._rung13(recs)                                  # only 4 of the last 30 carry `by`
    assert not p["passed"] and p["crit"]["audit"] < 1.0
    recs = _looks(TOWER, 30, lambda i: True, 0.05, lambda i: ["self"] if i >= 25 else None)
    assert SL._rung13(recs)["passed"]                     # 5 do


def test_a_little_slack_for_a_person_playing_at_the_same_moment():
    """`self` among the changers counts even when the person played too; 1 in
    10 changes without self is still within the 0.9 share, 2 in 10 is not."""
    ok = _looks(TOWER, 30, lambda i: True, 0.05,
                lambda i: ["self"] if i % 10 else (["operator"] if i == 0 else ["operator", "self"]))
    assert SL._rung13(ok)["passed"]
    bad = _looks(TOWER, 30, lambda i: True, 0.05, lambda i: ["operator"] if i % 5 == 0 else ["self"])
    assert not SL._rung13(bad)["passed"]


def test_29_looks_do_not_pass():
    p = SL._rung13(_tower(29))
    assert not p["passed"], p
    assert p["crit"]["looks"] < 1.0


def test_only_answered_nudged_looks_outside_audits_count():
    recs = _tower(29)
    recs += [{"ts": T0 + 9_000, "kind": "reach", "eid": TOWER, "p": 0.05, "nudged": 0, "p_n": 0.05,
              "answer": "changed", "by": ["self"]},                                     # not nudged
             {"ts": T0 + 9_001, "kind": "reach", "eid": TOWER, "p": 0.05, "nudged": 1, "p_n": 0.9,
              "answer": "none"},                                                        # no answer
             {"ts": T0 + 9_002, "kind": "reach", "eid": TOWER, "p": 0.05, "nudged": 1, "p_n": 0.9,
              "answer": "changed", "by": ["self"], "audit": True},                      # inside an audit
             {"ts": T0 + 9_003, "kind": "reach", "eid": TOWER, "p": 0.05, "nudged": 1, "p_n": 0.9,
              "answer": "changed", "by": ["self"], "audit5": "pause"}]
    assert not SL._rung13(recs)["passed"]


def test_the_window_is_the_last_30_looks():
    """30 good looks after many bad ones pass: only the last RUNG13_LOOKS count."""
    rng = random.Random(3)
    flips = [rng.random() < 0.4 for _ in range(100)]
    old = _looks(TOWER, 100, lambda i: flips[i], 0.4, lambda i: ["world"])
    new = _looks(TOWER, 30, lambda i: True, 0.05, lambda i: ["self"], k=2, t0=T0 + 50_000)
    assert SL._rung13(old + new)["passed"]


def test_the_best_thing_counts():
    rng = random.Random(5)
    flips = [rng.random() < 0.4 for _ in range(60)]
    recs = _looks(DRIFT, 60, lambda i: flips[i], 0.4, lambda i: ["world"]) + _tower(t0=T0 + 50_000)
    p = SL._rung13(recs)
    assert p["passed"] and p["eid"] == TOWER


def test_no_looks_no_pass():
    p = SL._rung13([])
    assert not p["passed"] and p["skill"] == 0.0
    assert set(p["crit"]) == {"looks", "skill", "audit"}


# ---- the pass line on a seat ---------------------------------------------------------------

@pytest.fixture
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    yield tmp_path / "data"


def _seat(record):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = None
    est._srv = srv
    p = SL.record_path(srv)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in record))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _lines(srv):
    return [json.loads(ln) for ln in SL.record_path(srv).read_text().splitlines() if ln.strip()]


def test_the_pass_line_carries_ladder_3(_data):
    record = [{"ts": T0 - 100, "kind": "birth", "by": "operator"},
              {"ts": T0 - 90, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9},
              {"ts": T0 - 80, "kind": "accept_migrated", "ids": ["feel"]}] + _tower()
    srv = _seat(record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    line = next(r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") == "nudge")
    assert line["ladder"] == 3
    assert line["rung"] == 13 and line["eid"] == TOWER
    assert line["skill"] >= SL.RUNG13_SKILL
    assert "nudge" in SL._earned(SL._read(srv))


def test_no_pass_before_feel_is_accepted(_data):
    record = [{"ts": T0 - 100, "kind": "birth", "by": "operator"},
              {"ts": T0 - 90, "kind": "accept_migrated", "ids": []}] + _tower()
    srv = _seat(record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    assert not [r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") == "nudge"]


def test_nudge_is_built_at_the_merge_of_its_test():
    """#475 merged 2026-10-23 04:43:02 UTC: first reach is built from then."""
    import datetime as _dt
    from membrane import self_sheet as SS
    assert SS.BUILT_AT["nudge"]["pr"] == 475
    assert SS.BUILT_AT["nudge"]["merged"] == _dt.datetime(2026, 10, 23, 4, 43, 2,
                                                          tzinfo=_dt.timezone.utc).timestamp()
