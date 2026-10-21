"""#239 (part of #161): the toybox -- self's toys as tested, built-in kit.

Every toy is an entity whose getter (and tick, for the toys with a clock of
their own) is a two-line stub into membrane/toybox.py: no model-written code.
Its state lives in <data dir>/toybox/<name>.json (atomic writes, survives a
restart), its randomness is seeded (a replay reproduces it), and every change
of its state carries who caused it: `by: self` (self's own look or reach) or
`by: world` (its own clock or randomness) -- the ground truth rung 6's
body/other split can be checked against.
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from tests._self_cap import cap  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


def _unit(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(d=None):
    d = Path(d or os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _World(_unit(1))
    est._srv = srv
    SL.bind(srv)
    TB.bind(srv)
    return srv


def _eid(name):
    return MEMBRANE_NS + "Component:" + name


def _log(name):
    return TB.state(name)["log"]


def _drive(name, t0, t1, step, reads=()):
    """Tick the toy from t0 to t1; read it (by the given reader) at the given times."""
    reads = sorted(reads)
    t = t0
    while t <= t1:
        while reads and reads[0][0] <= t:
            ts, by = reads.pop(0)
            TB.read(name, by=by, now=ts)
        TB.tick(name, now=t)
        t += step


# ---- the kit -----------------------------------------------------------------

def test_the_kit():
    assert set(TB.KINDS) == {"fast", "slow", "burst", "drift", "die", "body", "tower", "ball",   # ball: #264
                             "mirror"}                                                          # mirror: #312
    for kind, spec in TB.KINDS.items():
        assert isinstance(spec["params"], dict) and spec["about"]


def test_a_toy_is_an_entity_with_a_stub_getter_not_model_code():
    srv = _seat()
    out = TB.add(srv, "die", params={"p": 0.5})
    assert out["ok"] and out["name"] == "toy:die"
    e = srv.entity_store.docs[_eid("toy:die")]
    p = e.properties
    assert p["toybox"]["kind"] == "die"
    assert "toybox" in p["getter_script"] and len(p["getter_script"]) < 120
    assert "toybox" in p["tick_script"] and len(p["tick_script"]) < 120
    assert SL._eid() not in SL._targets(srv) and _eid("toy:die") in SL._targets(srv)


def test_bad_kind_and_bad_params_are_refused():
    srv = _seat()
    assert not TB.add(srv, "kite")["ok"]
    assert not TB.add(srv, "die", params={"p": 2})["ok"]
    assert not TB.add(srv, "die", params={"q": 1})["ok"]          # a param the kind does not take
    assert not TB.add(srv, "tower", params={"n": 0})["ok"]
    TB.add(srv, "body")
    assert not TB.add(srv, "body")["ok"]                          # the name is taken
    assert TB.add(srv, "body", name="toy:body2")["ok"]


def test_the_state_lives_in_the_data_dir(_data):
    srv = _seat()
    TB.add(srv, "drift")
    p = _data / "toybox" / "toy_drift.json"
    assert p.exists() and json.loads(p.read_text())["kind"] == "drift"
    assert not list((_data / "toybox").glob("*.tmp"))


# ---- determinism: a replay reproduces --------------------------------------------

@pytest.mark.parametrize("kind,params,span,step", [
    ("fast", {}, 60, 1.0),
    ("slow", {"period": 600}, 4000, 30.0),
    ("burst", {"window": 120, "every": 10}, 3000, 5.0),
    ("drift", {"min_s": 20, "max_s": 90}, 3000, 5.0),
    ("die", {"p": 0.05}, 1000, 1.0),
])
def test_each_world_toy_replays_under_its_seed(kind, params, span, step, tmp_path, monkeypatch):
    runs = []
    for i in range(2):
        monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / f"run{i}"))
        TB.create(kind, "toy:x", dict(params, seed=7), now=0.0)
        _drive("toy:x", 0.0, span, step, reads=[(t, "self") for t in range(5, span, 37)])
        runs.append(_log("toy:x"))
    assert runs[0] == runs[1] and len(runs[0]) > 1
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "other"))
    TB.create(kind, "toy:x", dict(params, seed=8), now=0.0)
    _drive("toy:x", 0.0, span, step)
    if kind not in ("slow",):                                     # slow's schedule is its period, not chance
        assert [e.get("value") for e in _log("toy:x")] != [e.get("value") for e in runs[0]]


def test_the_default_seed_is_derived_from_the_name():
    a = TB.create("die", "toy:a", {}, now=0.0)
    b = TB.create("die", "toy:b", {}, now=0.0)
    assert a["seed"] == TB.create("die", "toy:a2", {"seed": a["seed"]}, now=0.0)["seed"]
    assert a["seed"] != b["seed"]


# ---- dynamics -----------------------------------------------------------------

def test_fast_changes_faster_than_any_look_and_never_because_of_one():
    TB.create("fast", "toy:fast", {"seed": 1}, now=0.0)
    seen = []
    for t in range(0, 50, 10):                                    # self's looks, its pace
        TB.tick("toy:fast", now=float(t))
        seen.append(TB.read("toy:fast", by="self", now=t + 0.5))
    assert all(a != b for a, b in zip(seen, seen[1:]))            # every look finds it changed
    assert {e["by"] for e in _log("toy:fast")} == {"world"}       # ...and none of it was self


def test_slow_changes_once_a_period():
    TB.create("slow", "toy:slow", {"seed": 1, "period": 3600}, now=0.0)
    _drive("toy:slow", 0.0, 2 * 3600 + 10, 60.0, reads=[(t, "self") for t in range(0, 7200, 300)])
    ch = [e for e in _log("toy:slow") if e["what"] == "change"]
    assert len(ch) == 2 and {e["by"] for e in ch} == {"world"}
    assert [round(e["ts"]) for e in ch] == [3600, 7200]


def test_burst_changes_only_inside_its_active_windows():
    TB.create("burst", "toy:burst", {"seed": 3, "window": 100, "active": 0.4, "every": 10}, now=0.0)
    _drive("toy:burst", 0.0, 19999, 2.0)                          # 200 whole windows
    ch = [e for e in _log("toy:burst") if e["what"] == "change"]
    wins = {}
    for e in ch:
        wins.setdefault(int(e["ts"] // 100), []).append(e)
    assert 0.2 < len(wins) / 200 < 0.6                             # about `active` of the windows move
    assert all(len(v) >= 5 for v in wins.values())                # ...and those move a lot
    assert {e["by"] for e in ch} == {"world"}


def test_drift_is_a_bounded_random_walk_on_its_own_schedule():
    TB.create("drift", "toy:drift", {"seed": 2, "min_s": 30, "max_s": 60, "step": 5}, now=0.0)
    _drive("toy:drift", 0.0, 6000, 1.0)
    ch = [e for e in _log("toy:drift") if e["what"] == "change"]
    gaps = [b["ts"] - a["ts"] for a, b in zip(ch, ch[1:])]
    assert ch and all(29 <= g <= 62 for g in gaps)
    vals = [e["value"] for e in ch]
    assert all(0 <= v <= 100 for v in vals)
    assert all(abs(b - a) == 5 or b in (0, 100) or a in (0, 100) for a, b in zip(vals, vals[1:]))
    assert {e["by"] for e in ch} == {"world"}


def test_die_rolls_at_rate_p_and_never_on_a_read():
    TB.create("die", "toy:die", {"seed": 4, "p": 0.1}, now=0.0)
    before = TB.state("toy:die")
    for i in range(200):
        TB.read("toy:die", by="self", now=float(i))
    assert TB.state("toy:die") == before                          # reads never roll it
    _drive("toy:die", 0.0, 2000, 1.0)
    ch = [e for e in _log("toy:die") if e["what"] == "change"]
    assert 120 < len(ch) < 290                                    # ~p per second
    assert {e["by"] for e in ch} == {"world"}
    assert all(1 <= e["value"] <= 6 for e in ch)
    assert all(a["value"] != b["value"] for a, b in zip(ch, ch[1:]))


def test_body_changes_only_on_selfs_read():
    TB.create("body", "toy:body", {"seed": 5}, now=0.0)
    r0 = TB.read("toy:body", by="model", now=1.0)
    for by in ("model", "field", "world", ""):
        assert TB.read("toy:body", by=by, now=2.0) == r0
    for t in range(200):
        TB.tick("toy:body", now=float(t))                         # no clock of its own
    assert TB.read("toy:body", by="field", now=3.0) == r0
    r1 = TB.read("toy:body", by="self", now=4.0)
    assert r1 != r0
    ch = [e for e in _log("toy:body") if e["what"] == "change"]
    assert len(ch) == 1 and ch[0]["by"] == "self" and ch[0]["ts"] == 4.0


def test_the_readings_are_minimal():
    for kind in TB.KINDS:
        name = "toy:" + kind
        TB.create(kind, name, {"seed": 1}, now=0.0)
        r = TB.read(name, by="model", now=1.0, block=2 if kind == "tower" else None)
        assert isinstance(r, dict) and 0 < len(r) <= 2, (kind, r)
        assert "by" not in json.dumps(r) and "log" not in r and "seed" not in r


# ---- the tower ------------------------------------------------------------------

def test_tower_topples_from_the_nudged_block_up_and_only_self_does_it():
    TB.create("tower", "toy:tower", {"n": 5}, now=0.0)
    for by in ("model", "field", "world", "self"):                  # #333: self's look changes nothing either
        assert TB.read("toy:tower", by=by, now=1.0, block=3) == {"standing": True}
    assert all(b["standing"] for b in TB.state("toy:tower")["blocks"])
    assert TB.nudge("toy:tower", block=3, now=2.0)["reading"] == {"standing": False}   # #333: its nudge does
    bl = TB.state("toy:tower")["blocks"]
    assert [b["standing"] for b in bl] == [True, True, False, False, False]
    assert all(b["by"] == "self" and b["at"] == 2.0 for b in bl[2:])
    assert TB.nudge("toy:tower", block=4, now=3.0)["reading"] == {"standing": True}   # a fallen block: lifted
    assert [b["standing"] for b in TB.state("toy:tower")["blocks"]] == [True, True, False, True, False]
    assert TB.read("toy:tower", by="model", now=4.0) == {"standing": 3}              # the whole: a count
    assert {e["by"] for e in _log("toy:tower")} == {"self"}


# ---- persistence -------------------------------------------------------------------

def test_a_restart_continues_the_same_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "a"))
    TB.create("drift", "toy:d", {"seed": 9, "min_s": 10, "max_s": 30}, now=0.0)
    _drive("toy:d", 0.0, 2000, 1.0)
    whole = _log("toy:d")
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "b"))
    TB.create("drift", "toy:d", {"seed": 9, "min_s": 10, "max_s": 30}, now=0.0)
    _drive("toy:d", 0.0, 1000, 1.0)
    TB._forget()                                                  # the process dies: nothing kept in memory
    _drive("toy:d", 1001.0, 2000, 1.0)
    assert _log("toy:d") == whole


def test_a_seat_restart_keeps_the_toys_and_their_state(_data):
    srv = _seat()
    TB.add(srv, "tower", params={"n": 3})
    TB.nudge("toy:tower", block=2, now=5.0)                     # #333: self topples it by a nudge
    srv.entity_store.flush()
    TB._forget()
    srv2 = _seat()
    assert _eid("toy:tower:b3") in srv2.entity_store.docs
    assert [b["standing"] for b in TB.state("toy:tower")["blocks"]] == [True, False, False]


# ---- end to end: self's reach through the real self loop --------------------------

def test_tower_through_selfs_real_nudge_path(monkeypatch):
    srv = _seat()
    TB.add(srv, "tower", params={"n": 5})        # #267: on the bench before birth (it unlocks at rung 3)
    cap(srv, 1)
    t = 1000.0
    for i in range(SL.RUNG1_CYCLES * 2):                          # rung 1 first: the ladder is climbed in order
        SL.cycle(srv, now=t)
        t += SL.PACE_S
    assert SL.status(srv)["rung"] == 1
    cap(srv, 2)
    b = {k: _eid(f"toy:tower:b{k}") for k in range(1, 6)}
    monkeypatch.setattr(SL, "_targets", lambda srv: [b[3]])
    SL.cycle(srv, now=t)                                          # #333: a look, and nothing moves
    t += SL.PACE_S
    assert all(x["standing"] for x in TB.state("toy:tower")["blocks"])
    SL._nudge_once(srv, 0, t - 1.0, b[3], None)                   # its nudge, through self::nudge
    SL.cycle(srv, now=t)
    t += SL.PACE_S
    reach = [r for r in SL._read(srv) if r.get("kind") == "reach"][-1]
    assert reach["eid"] == b[3] and reach["answer"] == "changed"
    bl = TB.state("toy:tower")["blocks"]
    assert [x["standing"] for x in bl] == [True, True, False, False, False]
    assert all(x["by"] == "self" for x in bl[2:])
    # the model's own read of a block changes nothing
    WA.entity_read_back(srv, srv.entity_store.docs[b[1]], by="model")
    assert TB.state("toy:tower")["blocks"][0]["standing"] is True
    monkeypatch.setattr(SL, "_targets", lambda srv: [b[4]])
    SL.cycle(srv, now=t)
    t += SL.PACE_S
    SL._nudge_once(srv, 0, t - 1.0, b[4], None)
    SL.cycle(srv, now=t)
    reach = [r for r in SL._read(srv) if r.get("kind") == "reach"][-1]
    assert reach["eid"] == b[4] and reach["answer"] == "changed"
    assert [x["standing"] for x in TB.state("toy:tower")["blocks"]] == [True, True, False, True, False]
    lifts = [e for e in _log("toy:tower") if e["what"] == "lift"]
    assert lifts and lifts[-1]["by"] == "self" and lifts[-1]["block"] == 4


def test_the_getter_is_told_who_reads():
    """The marker: entity_read_back hands its `by` to the getter (state.extra.by)."""
    srv = _seat()
    e = QuantumEntity(eid=_eid("probe"), cls="Component",
                      properties={"name": "probe", "getter_script":
                                  "result = {'state': {'by': state['extra'].get('by')}}"})
    srv.entity_store.docs[e.eid] = e
    assert WA.entity_read_back(srv, e, by="self")["state"] == {"by": "self"}
    assert WA.entity_read_back(srv, e, by="model")["state"] == {"by": "model"}


# ---- status: the 👶 panel's toys -----------------------------------------------------

def test_status_lists_toybox_toys_with_their_kind():
    srv = _seat()
    TB.add(srv, "body", name="pet")                               # no toy: prefix: still a toy
    TB.add(srv, "die")
    SL._rt(srv).pop("toys", None)
    toys = {t["eid"]: t for t in SL.status(srv)["toys"]}
    assert toys[_eid("pet")]["kind"] == "body"
    assert toys[_eid("toy:die")]["kind"] == "die"
    assert toys[_eid("toy:die")]["health"] == "unread"


# ---- the surface: the toybox object (cliffhanger) and the CLI ------------------------

def _call(srv, method, args=None):
    return N.method_call(srv, srv.entity_store, None, None, "toybox", method, args or {})


def test_the_toybox_object_answers_list_add_remove_reset():
    srv = _seat()
    out = _call(srv, "add", {"spec": "die p=0.5 seed=3"})
    assert out["rc"] == 0, out
    assert _eid("toy:die") in srv.entity_store.docs
    assert TB.state("toy:die")["params"]["p"] == 0.5 and TB.state("toy:die")["seed"] == 3
    out = _call(srv, "add", {"spec": "tower n=3 name=toy:t3"})
    assert out["rc"] == 0 and _eid("toy:t3:b3") in srv.entity_store.docs
    out = _call(srv, "list")
    assert out["rc"] == 0 and "toy:die" in out["head"] and "tower" in out["head"]
    TB.read("toy:t3", by="self", now=1.0, block=1)
    assert _call(srv, "reset", {"name": "toy:t3"})["rc"] == 0
    assert all(b["standing"] for b in TB.state("toy:t3")["blocks"])
    assert _call(srv, "remove", {"name": "toy:t3"})["rc"] == 0
    assert not any(k.startswith(_eid("toy:t3")) for k in srv.entity_store.docs)
    assert _call(srv, "add", {"spec": "kite"})["rc"] != 0


def test_cli_add_list_reset_remove(_data, capsys):
    _seat().entity_store.flush()
    assert TB.cli_main(["add", "tower", "--name", "toy:tw", "--param", "n=3", "--param", "seed=4"]) == 0
    est = EntityStore(path=str(_data / "entities.json"), shared=False)
    assert _eid("toy:tw:b3") in est.docs
    assert TB.cli_main(["list"]) == 0
    assert "toy:tw" in capsys.readouterr().out
    TB.read("toy:tw", by="self", now=1.0, block=1)
    assert TB.cli_main(["reset", "toy:tw"]) == 0
    assert all(b["standing"] for b in TB.state("toy:tw")["blocks"])
    assert TB.cli_main(["remove", "toy:tw"]) == 0
    est = EntityStore(path=str(_data / "entities.json"), shared=False)
    assert not any(k.startswith(_eid("toy:tw")) for k in est.docs)
    assert list((_data / "toybox" / "archive").glob("toy_tw*.json"))   # the history is kept, not deleted


def test_cli_refuses_to_edit_the_store_under_a_live_seat(monkeypatch, capsys):
    monkeypatch.setattr(TB, "_seat_alive", lambda: True)
    assert TB.cli_main(["add", "die"]) != 0
    assert "toybox.add" in capsys.readouterr().err


def test_membrane_toybox_is_a_command(monkeypatch):
    from membrane import cli
    seen = {}
    monkeypatch.setattr(TB, "cli_main", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["membrane", "toybox", "list"])
    with pytest.raises(SystemExit) as ex:
        cli.main()
    assert ex.value.code == 0 and seen["argv"] == ["list"]


# ---- migration -------------------------------------------------------------------------

def _old_toys(srv):
    est = srv.entity_store
    def put(name, **p):
        e = QuantumEntity(eid=_eid(name), cls="Component", properties={"name": name, **p})
        est.docs[e.eid] = e
    put("w:fast", getter_script="import time\nresult={'state':{'t':time.time()}}")
    put("w:body", getter_script="result={'state':{}}")
    put("toy:die", tick_script="result={}")
    put("toy:tower", getter_script="result={}")
    for k in (1, 2, 3):
        put(f"toy:tower:b{k}", getter_script="result={}")
    put("toy:tower:reach", tick_script="result={}")
    put("w:prime", getter_script="result={}")
    est.flush()


def test_migrate_dry_run_lists_and_changes_nothing(_data, capsys):
    srv = _seat()
    _old_toys(srv)
    before = (_data / "entities.json").read_bytes()
    assert TB.cli_main(["migrate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "w:fast" in out and "fast" in out
    assert "toy:tower" in out and "tower" in out and "n=3" in out
    assert "toy:tower:reach" in out and "retire" in out
    assert "w:prime" in out and "no built-in" in out
    assert (_data / "entities.json").read_bytes() == before
    assert not (_data / "toybox").exists() or not list((_data / "toybox").glob("*.json"))


def test_migrate_without_the_confirmation_changes_nothing(_data):
    srv = _seat()
    _old_toys(srv)
    before = (_data / "entities.json").read_bytes()
    assert TB.cli_main(["migrate"]) != 0
    assert (_data / "entities.json").read_bytes() == before


def test_migrate_with_yes_replaces_and_backs_up(_data):
    srv = _seat()
    _old_toys(srv)
    assert TB.cli_main(["migrate", "--yes"]) == 0
    est = EntityStore(path=str(_data / "entities.json"), shared=False)
    assert est.docs[_eid("w:fast")].properties["toybox"]["kind"] == "fast"
    assert est.docs[_eid("toy:tower:b3")].properties["toybox"]["kind"] == "tower"
    assert _eid("toy:tower:reach") not in est.docs
    assert "import time" not in est.docs[_eid("w:fast")].properties["getter_script"]
    assert _eid("w:prime") in est.docs and "toybox" not in est.docs[_eid("w:prime")].properties
    backup = list((_data / "toybox" / "migrated").glob("*.json"))
    names = {json.loads(p.read_text())["eid"] for p in backup}
    assert {_eid("w:fast"), _eid("toy:tower:reach"), _eid("toy:tower:b1")} <= names
