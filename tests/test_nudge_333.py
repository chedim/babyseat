"""#333, part 1: Blurp acts by NUDGING things on the field.

One act replaces the per-thing verbs (meet's chat push, roll's ball): a nudge
at a thing in Blurp's world, which the THING answers -- the ball rolls to the
other side, a tower block topples or lifts, a die / drift / burst changes; a
thing with no nudge does nothing. Reach is infinite (a nudge is mental), but
only things in Blurp's world (#337) can be nudged: one outside is refused.
Using a thing wrong costs energy (energy.error, 0.01): a nudge refused, the
ball nudged while it is on the person's side, a nudge outside its world.

Each nudge writes a `nudge` line: n, ts, eid, dist, rc, aim (and force).
Once feel itself is accepted (the nudge skill's prerequisite), Blurp's aims
are drawn among the field spots of the things in its world, at random, and
it nudges the thing it aims at with its own move (the direction and the
force are its stride) on each cycle its distance to it fell (decision 1;
tests/test_nudge_closing_333.py).

THE LADDER (LADDER_V = 3): meet, turns, expect and mirror were the
developer's wiring (the audit); their passes count only when a line carries
`ladder: 3`, so every Blurp drops to Lv 5 (feel, look, kinds, patience,
shift). Rung 2's pass is kept as "look"; "first reach" is the nudge skill
(its test comes with the rebuilt rungs). The record is never rewritten: a
migration writes one `rung_void` line per voided pass, once."""
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

from membrane import energy as EN  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_world as SW  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
T0 = 1_700_000_000.0           # in the past: the migration's accept line (now) comes after the passes


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


def _field():
    fld = EntityField(MemBackend(), _Emb(), "nudge@test")
    fld.current = lambda now=None: np.array(_unit(1), dtype=float)
    return fld


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(record=None, toys=()):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _field()
    est._srv = srv
    if record:
        p = SL.record_path(srv)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in record))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    for k in toys:
        out = TB.add(srv, k, locked_ok=True)
        assert out["ok"], out
    return srv


def _lines(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _cid(name):
    return MEMBRANE_NS + "Component:" + name


OLD = [{"ts": T0, "kind": "birth", "by": "operator"},
       {"ts": T0 + 10, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9},
       {"ts": T0 + 20, "kind": "achieve", "id": "reach", "rung": 2, "skill": 0.3},
       {"ts": T0 + 30, "kind": "achieve", "id": "kinds", "rung": 3, "skill": 0.3},
       {"ts": T0 + 40, "kind": "achieve", "id": "shift", "rung": 5, "skill": 1.0},
       {"ts": T0 + 50, "kind": "achieve", "id": "patience", "rung": 4, "skill": 0.1},
       {"ts": T0 + 60, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0},
       {"ts": T0 + 70, "kind": "achieve", "id": "expect", "rung": 8, "skill": 0.2},
       {"ts": T0 + 80, "kind": "achieve", "id": "mirror", "rung": 9, "skill": 0.95},
       {"ts": T0 + 90, "kind": "accept_migrated", "ids": ["feel", "reach", "kinds", "shift", "patience",
                                                          "meet", "expect", "mirror"]}]


# ---- the ladder: the old wiring's passes are voided ---------------------------------

def test_the_ladder_version_is_3_and_the_rebuilt_rungs_are_not_built_yet():
    assert SL.LADDER_V == 3
    for i in ("turns", "expect"):
        assert SL.ACHIEVEMENTS[i]["built"] is False, i
    assert SL.ACHIEVEMENTS["mirror"]["built"] is True             # part 3d: rebuilt on looks
    assert SL.ACHIEVEMENTS["meet"]["built"] is True               # part 3e: rebuilt on delayed contingency
    assert SL.ACHIEVEMENTS["reach"]["name"] == "look"           # rung 2's pass, kept as the look
    assert SL.ACHIEVEMENTS["reach"]["built"] is True
    nd = SL.ACHIEVEMENTS["nudge"]
    assert nd["name"] == "first reach" and nd["prereqs"] == ("feel",) and nd["built"] is True   # part 3c


def test_an_old_blurp_drops_to_level_5_feel_look_kinds_patience_shift():
    srv = _seat(record=OLD)
    st = SL.status(srv)
    assert st["level"] == 5
    assert {e["id"] for e in st["earned"]} == {"feel", "reach", "kinds", "patience", "shift"}
    assert [o["id"] for o in st["old_wiring"]] == ["meet", "expect", "mirror"]


def test_the_voids_are_written_once_and_no_line_is_lost():
    srv = _seat(record=OLD)
    got = _lines(srv)
    for r in OLD:
        assert r in got                                          # never rewritten, never dropped
    voids = [r for r in got if r.get("kind") == "rung_void"]
    assert sorted(r["rung"] for r in voids) == [6, 8, 9]
    assert all(r.get("issue") == 333 and r.get("voids_ts") for r in voids)
    n = len(got)
    SL._RT.pop(id(srv), None)
    SL.bind(srv)                                                 # a restart: nothing more
    assert len(_lines(srv)) == n


def test_a_blurp_with_nothing_to_void_gets_no_void_lines():
    srv = _seat(record=OLD[:3])
    assert not [r for r in _lines(srv) if r.get("kind") == "rung_void"]


def test_a_pass_on_ladder_3_counts():
    srv = _seat(record=OLD)
    SL._append(srv, {"ts": T0 + 10_000, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0, "ladder": 3})
    assert "meet" in SL._earned(SL._read(srv))


def test_an_old_ladder_2_rung_line_above_6_counts_no_more():
    recs = [{"ts": T0, "kind": "rung", "rung": 7, "ladder": 2}, {"ts": T0, "kind": "rung", "rung": 8, "ladder": 2},
            {"ts": T0, "kind": "rung", "rung": 3}]
    assert SL._passed(recs) == {0, 3}


def test_a_new_pass_carries_the_ladder():
    srv = _seat(record=OLD[:2])
    SL.set_mode(srv, "on")
    import membrane.self_loop as M
    old = M.PROGRESS[2]
    M.PROGRESS[2] = lambda recs, tempo=None: {"crit": {}, "skill": 1.0, "passed": True}
    try:
        SL._open_rungs(srv)
    finally:
        M.PROGRESS[2] = old
    line = next(r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") == "reach")
    assert line["ladder"] == 3


# ---- meet and roll are gone ------------------------------------------------------------

def test_meet_roll_and_the_chat_push_are_gone():
    assert "meet" not in SL.METHODS and "roll" not in SL.METHODS
    assert SL.METHODS["nudge"] == "self::nudge {{target}}"
    for name in ("_meet", "_roll", "_push_chat", "_ball_turn", "_toy_turns"):
        assert not hasattr(SL, name), name


# ---- the things answer a nudge (toybox) -------------------------------------------------

def test_the_ball_rolls_to_the_other_side_and_refuses_when_it_is_there():
    srv = _seat(toys=("ball",))
    out = TB.nudge("toy:ball", now=T0)
    assert out["ok"] and out["changed"] and not out["refused"]
    st = TB.peek("toy:ball")
    assert st["side"] == "other" and st["by"] == "self"
    again = TB.nudge("toy:ball", now=T0 + 1)
    assert again["refused"] and not again["changed"]
    assert TB.peek("toy:ball")["side"] == "other"


def test_a_tower_block_topples_and_lifts_by_a_nudge_and_a_look_moves_nothing():
    srv = _seat(toys=("tower",))
    assert TB.read("toy:tower", by="self", now=T0, block=3) == {"standing": True}
    assert TB.read("toy:tower", by="self", now=T0 + 1, block=3) == {"standing": True}   # a look is a look
    out = TB.nudge("toy:tower", block=3, now=T0 + 2)
    assert out["changed"] and not out["refused"]
    bl = TB.peek("toy:tower")["blocks"]
    assert [b["standing"] for b in bl] == [True, True, False, False, False]
    assert bl[2]["by"] == "self"
    TB.nudge("toy:tower", block=4, now=T0 + 3)
    assert TB.peek("toy:tower")["blocks"][3]["standing"] is True
    whole = TB.nudge("toy:tower", now=T0 + 4)                    # the tower as a whole has no nudge
    assert whole["ok"] and not whole["changed"] and not whole["refused"]


@pytest.mark.parametrize("kind", ["die", "drift", "burst"])
def test_the_self_moving_toys_change_by_a_nudge(kind):
    srv = _seat(toys=(kind,))
    name = f"toy:{kind}"
    before = TB.peek(name)["value"]
    out = TB.nudge(name, now=T0)
    assert out["changed"] and not out["refused"]
    assert TB.peek(name)["value"] != before
    assert TB.state(name)["log"][-1]["by"] == "self"


@pytest.mark.parametrize("kind", ["fast", "slow", "body", "mirror"])
def test_a_toy_with_no_nudge_does_nothing(kind):
    srv = _seat(toys=(kind,))
    name = f"toy:{kind}"
    n = TB.log_len(name)
    out = TB.nudge(name, now=T0)
    assert out["ok"] and not out["changed"] and not out["refused"]
    assert TB.log_len(name) == n


# ---- the act: self::nudge, its world gate, its line and its fee -------------------------

def test_a_nudge_outside_its_world_is_refused_and_costs_the_error_fee():
    srv = _seat(toys=("ball",))
    eid = _cid("toy:ball")
    SW._doc(srv)["members"].pop(field_id(eid))                  # the ball is not in this Blurp's world
    e0 = EN.level(srv)
    SL._nudge_once(srv, 7, T0, eid, np.full(DIM, 0.01))
    assert TB.peek("toy:ball")["side"] == "self"                 # untouched
    line = [r for r in _lines(srv) if r.get("kind") == "nudge"][-1]
    assert line["eid"] == eid and line["rc"] == 2 and line["n"] == 7
    err = [r for r in _lines(srv) if r.get("kind") == "error"][-1]
    assert err["cost"] == EN.ERROR_COST
    assert EN.level(srv) == pytest.approx(e0 - 0.01)


def test_nudging_the_ball_rolls_it_then_on_their_side_costs_the_fee():
    srv = _seat(toys=("ball",))
    eid = _cid("toy:ball")
    e0 = EN.level(srv)
    SL._nudge_once(srv, 1, T0, eid, np.full(DIM, 0.01))
    assert TB.peek("toy:ball")["side"] == "other"
    assert EN.level(srv) == pytest.approx(e0)
    SL._nudge_once(srv, 2, T0 + 10, eid, np.full(DIM, 0.01))
    ns = [r for r in _lines(srv) if r.get("kind") == "nudge"]
    assert [r["rc"] for r in ns] == [0, 2]
    for k in ("n", "ts", "eid", "dist", "rc", "aim", "force"):
        assert k in ns[0], k
    assert ns[0]["force"] == pytest.approx(float(np.linalg.norm(np.full(DIM, 0.01))), abs=1e-4)
    assert EN.level(srv) == pytest.approx(e0 - 0.01)


def test_nudging_a_thing_with_no_nudge_is_nothing_and_free():
    srv = _seat(toys=("fast",))
    e0 = EN.level(srv)
    SL._nudge_once(srv, 1, T0, _cid("toy:fast"), np.full(DIM, 0.01))
    line = [r for r in _lines(srv) if r.get("kind") == "nudge"][-1]
    assert line["rc"] == 1
    assert EN.level(srv) == pytest.approx(e0)
    assert not [r for r in _lines(srv) if r.get("kind") == "error"]


def test_a_thing_with_a_nudge_method_of_its_own_answers_it():
    srv = _seat()
    est = srv.entity_store
    eid = _cid("lamp")
    est.upsert(QuantumEntity(eid=eid, cls="Component", properties={"name": "lamp"}), embedder=None)
    SW.join(srv, [eid], "test")
    from membrane.night import method_define
    method_define(srv, est, None, None, on=eid, method="nudge", command="true")
    SL._nudge_once(srv, 1, T0, eid, np.full(DIM, 0.01))
    assert [r for r in _lines(srv) if r.get("kind") == "nudge"][-1]["rc"] == 0


def test_self_nudge_runs_only_in_its_own_loop():
    assert SL.from_command("nudge x")["rc"] == 1


# ---- the cycle: aims among the things of its world, one nudge a cycle ------------------

def _felt(toys=("ball", "die")):
    rec = [{"ts": T0, "kind": "birth", "by": "operator"},
           {"ts": T0 + 1, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9}]
    srv = _seat(record=rec, toys=toys)
    SL.set_mode(srv, "on")
    SL._rt(srv)["isolate"] = {"feel", "nudge"}                   # no looks: the nudges alone
    return srv


def test_once_feel_is_accepted_it_nudges_the_thing_it_aims_at_while_closing_in(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * 11.0)                # many aims in a short test: 2 cycles 11 s apart (#343: s)
    srv = _felt()
    outside = _cid("toy:elsewhere")                              # a spot on the field, outside its world
    srv.entity_store.upsert(QuantumEntity(eid=outside, cls="Component", properties={"name": "elsewhere"}),
                            embedder=None)
    TB.place(srv, outside)
    mine = {field_id(_cid("toy:ball")), field_id(_cid("toy:die"))}
    for k in range(80):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    ns = [r for r in _lines(srv) if r.get("kind") == "nudge"]
    cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
    assert len(cyc) == 80
    assert 0 < len(ns) < len(cyc)                                # at most one a cycle: only while closing in
    assert len({r["n"] for r in ns}) == len(ns)
    got = {field_id(r["eid"]) for r in ns}
    assert mine <= got and field_id(outside) not in got          # both things, never the outside one
    assert got <= mine | {f for f in got if ":toy:tower" in f}   # #333 part 3e: feel's tower joined its world
    by_n = {c["n"]: c for c in cyc}
    for r in ns:
        c = by_n[r["n"]]
        assert r["aim"] == c["aim_n"] and field_id(c["aim_eid"]) == field_id(r["eid"])
        assert c["n"] != c["aim_n"]                              # never an aim's first cycle


def test_no_nudge_before_feel_is_accepted():
    srv = _seat(record=[{"ts": T0, "kind": "birth", "by": "operator"}], toys=("ball",))
    SL.set_mode(srv, "on")
    for k in range(5):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    assert not [r for r in _lines(srv) if r.get("kind") == "nudge"]


def test_with_no_thing_on_the_field_it_aims_into_space_and_nudges_nothing():
    srv = _felt(toys=())
    for k in range(5):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    assert not [r for r in _lines(srv) if r.get("kind") == "nudge"]
    cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
    assert cyc and all("aim_n" in c and "aim_eid" not in c for c in cyc)
