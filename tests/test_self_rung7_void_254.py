"""#254: a rung 7 pass the OLD scorer recorded doesn't count.

Before the fix rung 7 graded the model's guess, not self's, so a pass it
recorded was the model's. On the fixed rung 7's first cycle (the same moment
it writes `rung7_restart`) self appends a `rung_void` line for each rung 7
pass on the record before it. The pass line itself stays (the record is
append-only); _passed() skips a rung line with a later `rung_void` for its
rung, so rung 7 re-opens under the new scoring and a later pass counts. The
character sheet lists the voided pass in the quest log.

#258 (the ladder flip): that rung 7 is rung 8 now, and rung 7 is take turns.
A rung line above 6 counts only when it carries the new ladder's tag, so the
old pass counts for neither rung even before its void; the void is still
written (and stays), the level still reads 6, and the next rung is the new
rung 7. The old scorer's test, moved up, can pass later as rung 8 once the
new rung 7 is passed. The sheet names the old pass by its old name.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_self_rung7_self_predicts_254 as P  # noqa: E402  (its synthetic others)
import test_self_sheet_249 as S  # noqa: E402  (a seat that lived through the bugs)
from membrane import self_loop as SL  # noqa: E402

OLD7 = 100.0                 # the old scorer's rung 7 pass
FIX = 200.0                  # the fixed rung 7's first cycle


def _seat_with_old_pass():
    srv = P._seat(passed=(1, 2, 3, 4, 5, 6), limit=7)  # rungs 1-6 passed at ts 1, limit 7
    SL._append(srv, {"ts": OLD7, "kind": "rung", "rung": 7, "skill": 0.2207})
    return srv


def _lines(srv, kind):
    return [r for r in SL._read(srv) if r.get("kind") == kind]


def test_the_old_scorers_pass_is_voided_not_deleted():
    srv = _seat_with_old_pass()
    assert 7 not in SL._passed(SL._read(srv))                   # #258: an untagged rung 7 line is the old ladder's
    SL._mark_rung7_restart(srv, FIX)
    voids = _lines(srv, "rung_void")
    assert len(voids) == 1 and voids[0]["rung"] == 7 and voids[0]["ts"] == FIX
    assert voids[0]["voids_ts"] == OLD7 and "#254" in voids[0]["why"]
    assert [r["ts"] for r in _lines(srv, "rung") if r["rung"] == 7] == [OLD7]   # the line stays
    assert 7 not in SL._passed(SL._read(srv)) and 8 not in SL._passed(SL._read(srv))
    st = SL.status(srv)
    assert st["rung"] == 6 and st["next"] == 7                 # working on rung 7 (take turns, #258)


def test_earlier_rungs_are_unaffected():
    srv = _seat_with_old_pass()
    SL._mark_rung7_restart(srv, FIX)
    assert {1, 2, 3, 4, 5, 6} <= SL._passed(SL._read(srv))


def test_the_void_is_written_once_and_survives_a_restart():
    srv = _seat_with_old_pass()
    SL._mark_rung7_restart(srv, FIX)
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    SL._mark_rung7_restart(srv, FIX + 50)
    assert len(_lines(srv, "rung_void")) == 1
    assert 7 not in SL._passed(SL._read(srv))


def test_the_new_scorer_can_pass_later():
    # #258: the fixed scorer is rung 8's test now; it passes once the new rung 7 is passed
    srv = _seat_with_old_pass()
    SL._mark_rung7_restart(srv, FIX)
    SL._append(srv, {"ts": FIX + 1, "kind": "rung", "rung": 7, "skill": 0.2, "ladder": SL.LADDER_V})
    SL.set_limit(srv, 8)
    for r in P._recs(P._sessions(3)):                          # an other self can learn, after the fix
        SL._append(srv, {**r, "ts": r["ts"] + FIX})
    SL._open_rungs(srv)
    new8 = [r for r in _lines(srv, "rung") if r["rung"] == 8 and r["ts"] > FIX]
    assert len(new8) == 1 and new8[0]["ladder"] == SL.LADDER_V
    assert {7, 8} <= SL._passed(SL._read(srv))
    assert SL.status(srv)["rung"] == 8


def test_no_void_without_an_old_pass():
    srv = P._seat(passed=(1, 2, 3, 4, 5, 6), limit=7)
    SL._mark_rung7_restart(srv, FIX)
    assert _lines(srv, "rung_void") == []
    assert len(_lines(srv, "rung7_restart")) == 1


def test_the_sheet_lists_the_voided_pass():
    srv = S._lived(S._seat())
    old = S.T0 + 10.5 * S.H
    S._put(srv, {"ts": old, "kind": "rung", "rung": 7, "skill": 0.2207, "eid": S.ALICE})
    S._put(srv, {"ts": S.T0 + 11 * S.H, "kind": "rung7_restart", "issue": 254})
    S._put(srv, {"ts": S.T0 + 11 * S.H, "kind": "rung_void", "rung": 7, "voids_ts": old,
                 "why": "#254: rung 7 graded the LLM's guess, not self's"})
    s = S._sheet(srv, S.T0 + 12 * S.H)
    hm = dt.datetime.fromtimestamp(old, dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    assert s["quest"]["rung"] == 7                             # take turns (#258)
    assert s["quest"]["voided"] == [f"the old rung 7 (understand an other, now rung 8) passed {hm} under the "
                                    "#254 bug: graded the LLM, not self; doesn't count"]
    assert 7 not in [a["rung"] for a in s["achievements"]]
    assert s["header"]["level"] == 6
