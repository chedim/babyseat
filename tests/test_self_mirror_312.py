"""#312 (part of #161): the mirror skill -- "recognize itself" (rung 9; prerequisites
feel itself and meet an other).

Self's body is a point moving in the field. The 🪞 mirror toy has two sides:
the GLASS shows self's own move from one feed back, with seeded jitter; the
STRANGER shows self's own move from MIRROR_LAG feeds back, with the same
jitter -- the same statistics, and nothing to do with what self does now.
Each cycle self predicts both sides with the same learner -- its own moves
plus the side's past (p_*), against the side's past alone (b_*, the no-self
baseline) -- then feeds its move and reads both. The test passes when its own
moves predict the glass far beyond the baseline (skill >= BODY_SKILL, the
line meet's self/other split uses, and a sign-test z >= MIRROR_Z) -- the glass
reads as body -- and do not predict the stranger -- the stranger reads as other.
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
from membrane import self_sheet as SH  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

DIM = 16
MIRROR = MEMBRANE_NS + "Component:toy:mirror"


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
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


def _seat(rungs=(1, 2, 6), world=None, mirror=True, sub=""):
    d = Path(os.environ["MEMBRANE_DATA_DIR"]) / sub          # a sub dir: a seat of its own (no provisioning)
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.efield = world
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in rungs:
        line = {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9}
        if n >= 6:                      # #333: rungs 6-9 on ladder 3
            line["ladder"] = SL.LADDER_V
        SL._append(srv, line)
    rt = SL._rt(srv)
    rt["mode"] = "on"
    rt["isolate"] = {"mirror"}                      # the mirror's machinery alone
    if mirror:
        assert TB.add(srv, "mirror")["ok"]
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)


# ---- 1. the tree ---------------------------------------------------------------------------

def test_mirror_is_built_with_its_test():
    a = SL.ACHIEVEMENTS["mirror"]
    assert a["built"] is True and a["rung"] == 9 and a["name"] == "recognize itself"
    assert set(a["prereqs"]) == {"feel", "meet"} and a["branch"] == "self"
    assert "not built" not in a["why_prereqs"]
    assert 9 in SL.LADDER and SL.RUNG_ID[9] == "mirror" and "mirror" in SL.BUILT
    assert SL.PROGRESS[9] is SL._rung9
    assert SL.SKILL_PASS[9] == SL.BODY_SKILL                     # meet's own self/other line
    assert "Amsterdam" in a["child_ability"]                     # an analogy, kept


def test_it_opens_once_feel_and_meet_are_earned_and_accepted():
    srv = _seat(rungs=(1, 2, 6), mirror=False)
    assert "mirror" in SL.status(srv)["next"]
    srv = _seat(rungs=(2, 6), mirror=False, sub="two")            # feel not earned: closed
    assert "mirror" not in SL.status(srv)["next"]


# ---- 2. the toy -----------------------------------------------------------------------------

def test_the_mirror_is_a_built_in_kind_unlocked_with_meet():
    assert "mirror" in TB.KINDS and TB.KINDS["mirror"]["clock"] is False
    assert "mirror" not in TU.PLACEHOLDERS
    assert TB.MIRROR_LAG == SL.MIRROR_LAG and TB.KINDS["mirror"]["params"]["lag"] == TB.MIRROR_LAG
    assert TB.MIRROR_LAG % SL.AIM_HOLD                            # never in step with its aims (they'd leak timing)
    assert TU.UNLOCKS["mirror"] == 6 and TU.UNLOCK_IDS["mirror"] == "meet"   # its last prerequisite


def test_the_glass_shows_the_move_one_feed_back_and_the_stranger_one_from_long_ago():
    TB.create("mirror", "toy:mirror", now=1.0)
    lag, j = TB.MIRROR_LAG, TB.KINDS["mirror"]["params"]["jitter"]
    moves = [float(np.sin(0.7 * k) + 0.3 * k % 1.0) for k in range(lag + 5)]
    outs = [TB.mirror_feed("toy:mirror", m, now=2.0 + k) for k, m in enumerate(moves)]
    assert outs[0]["ok"] and outs[0]["glass"] is None              # nothing one feed back yet
    for k in range(1, len(moves)):
        assert abs(outs[k]["glass"] - moves[k - 1]) <= 5 * j       # the move before, a little blurred
    for k in range(lag + 1):
        assert outs[k]["stranger"] is None                         # nothing that old yet
    for k in range(lag + 1, len(moves)):
        assert abs(outs[k]["stranger"] - moves[k - 1 - lag]) <= 5 * j
    errs = [outs[k]["glass"] - moves[k - 1] for k in range(1, len(moves))]
    assert 0.3 * j < float(np.std(errs)) < 2 * j                   # jittered, by about its SD


def test_the_mirror_replays_and_keeps_its_moves_over_a_restart():
    TB.create("mirror", "toy:mirror", now=1.0)
    TB.create("mirror", "toy:m2", params={"seed": TB.peek("toy:mirror")["seed"]}, now=1.0)
    a = [TB.mirror_feed("toy:mirror", 0.1 * k, now=2.0 + k)["glass"] for k in range(20)]
    TB._forget()                                                   # a restart: the file is the toy
    b = [TB.mirror_feed("toy:m2", 0.1 * k, now=2.0 + k)["glass"] for k in range(20)]
    assert a == b                                                  # seeded: a run replays
    assert TB.mirror_feed("toy:mirror", 9.0, now=50.0)["glass"] == pytest.approx(1.9, abs=0.6)


def test_who_changed_each_side_is_on_the_log():
    TB.create("mirror", "toy:mirror", now=1.0)
    for k in range(TB.MIRROR_LAG + 3):
        TB.mirror_feed("toy:mirror", 0.5, now=2.0 + k)
    log = TB.state("toy:mirror")["log"]
    assert {e["by"] for e in log if e.get("side") == "glass"} == {"self"}
    assert {e["by"] for e in log if e.get("side") == "stranger"} == {"world"}
    assert TB.read("toy:mirror", by="self") == TB._reading(TB.peek("toy:mirror"))   # a look changes nothing


def test_the_mirror_takes_no_pokes_and_has_no_getter():
    srv = _seat(mirror=True)
    out = TB.poke("toy:mirror", "roll", by="alice")
    assert not out["ok"] and "mirror" in out["error"]
    ent = srv.entity_store.docs[MIRROR]
    assert not (ent.properties or {}).get("getter_script")        # self's ordinary looks never read it
    assert MIRROR not in SL._targets(srv)


def test_a_seat_whose_rung_6_unlocked_before_gets_the_mirror_once():
    srv = _seat(rungs=(1, 2, 6), mirror=False)
    SL._append(srv, {"ts": 5.0, "kind": "unlock", "rung": 6, "toys": ["ball"]})   # before the mirror was built
    TU.sync(srv, now=100.0)
    new = [r for r in _record(srv) if r["kind"] == "unlock" and "mirror" in r["toys"]]
    assert len(new) == 1 and new[0]["rung"] == 6 and new[0]["toys"] == ["mirror"]
    assert "mirror" in {t["kind"] for t in TB.list_toys()}
    TB.remove(srv, "toy:mirror")
    TU.sync(srv, now=200.0)                                        # removed stays removed
    assert "mirror" not in {t["kind"] for t in TB.list_toys()}


def test_a_seat_whose_rung_8_line_named_the_mirror_gets_it_built_late():
    srv = _seat(rungs=(1, 2, 6, 7, 8), mirror=False)
    SL._append(srv, {"ts": 5.0, "kind": "unlock", "rung": 6, "toys": ["ball"]})
    SL._append(srv, {"ts": 6.0, "kind": "unlock", "rung": 8, "id": "expect", "toys": ["mirror"]})
    TU.sync(srv, now=100.0)
    recs = _record(srv)
    assert [r for r in recs if r["kind"] == "unlock_built" and r["toys"] == ["mirror"]]
    assert not [r for r in recs if r["kind"] == "unlock" and r["ts"] == 100.0]   # no second unlock
    assert "mirror" in {t["kind"] for t in TB.list_toys()}


# ---- 3. the move and the predictions ----------------------------------------------------------

def test_the_move_is_its_direction_on_a_fixed_axis():
    v = np.zeros(DIM)
    assert SL._mirror_move(v) == 0.0                               # no move: nothing to show
    u = SL._mirror_axis(DIM)
    assert SL._mirror_move(0.01 * u) == pytest.approx(np.sqrt(DIM), rel=1e-3)
    assert SL._mirror_move(3.0 * u) == pytest.approx(np.sqrt(DIM), rel=1e-3)   # the direction, not the size
    assert SL._mirror_move(-u) == pytest.approx(-np.sqrt(DIM), rel=1e-3)


def _rows(n, glass, stranger, seed=0):
    """Mirror lines: own moves s, and each side as a function of the moves."""
    rng = np.random.default_rng(seed)
    s = rng.normal(size=n)
    out = []
    for t in range(n):
        out.append({"ts": float(t), "kind": "mirror", "n": t, "s": round(float(s[t]), 4),
                    "glass": glass(s, t, rng), "stranger": stranger(s, t, rng)})
    return out


def _echo(s, t, rng):
    return None if t < 1 else round(float(s[t - 1] + 0.1 * rng.normal()), 4)


def _old(s, t, rng):
    return None if t < 40 else round(float(s[t - 40] + 0.1 * rng.normal()), 4)


def test_self_learns_the_glass_from_its_own_moves_and_the_baseline_cannot():
    rows = _rows(200, _echo, _old)
    s_now = 0.7
    p, b = SL._mirror_predict(rows, "glass", s_now)
    assert p == pytest.approx(rows[-1]["s"], abs=0.1)             # the glass will show its last move
    assert abs(b - rows[-1]["s"]) > 0.0                            # the baseline sees only the glass's past
    assert SL._mirror_predict(rows[:5], "glass", s_now) is None   # too few rows: no prediction yet


def test_the_prediction_is_prequential():
    rows = _rows(120, _echo, _old)
    a = SL._mirror_predict(rows[:100], "glass", 0.3)
    rows2 = rows[:100] + [dict(r, glass=99.0) for r in rows[100:]]
    assert SL._mirror_predict(rows2[:100], "glass", 0.3) == a     # only rows before it count


def _scored(rows):
    """Run the predictions as the loop does: each row predicted from the rows before."""
    out = []
    for t, r in enumerate(rows):
        r = dict(r)
        for side in ("glass", "stranger"):
            pb = SL._mirror_predict(out, side, r["s"])
            if pb is not None:
                r[f"p_{side}"], r[f"b_{side}"] = round(pb[0], 4), round(pb[1], 4)
        out.append(r)
    return out


def test_the_test_passes_when_the_glass_is_its_own_and_the_stranger_is_not():
    recs = _scored(_rows(260, _echo, _old))
    p = SL._rung9(recs)
    assert p["passed"] is True, p
    assert p["skill"] >= SL.BODY_SKILL and p["z"] >= SL.MIRROR_Z
    assert p["labels"] == {"glass": "body", "stranger": "other"}
    assert list(p["crit"]) == ["looks", "self", "other"]


def test_a_glass_that_does_not_follow_it_is_not_itself():
    recs = _scored(_rows(260, _old, _old, seed=1))
    p = SL._rung9(recs)
    assert p["passed"] is False and p["crit"]["self"] < 1.0
    assert p["labels"]["glass"] == "other"
    assert SL._staged(p["crit"])[1] == "self"


def test_a_stranger_that_follows_it_too_is_no_stranger():
    recs = _scored(_rows(260, _echo, _echo, seed=2))
    p = SL._rung9(recs)
    assert p["crit"]["self"] >= 1.0 and p["crit"]["other"] == 0.0 and p["passed"] is False
    assert p["labels"]["stranger"] == "body"


def test_too_few_looks_hold_it_at_the_first_stage():
    recs = _scored(_rows(80, _echo, _old))
    p = SL._rung9(recs)
    assert p["passed"] is False and SL._staged(p["crit"])[1] == "looks"
    assert SL._rung9([])["crit"] == {"looks": 0.0, "self": 0.0, "other": 0.0}


def test_a_still_self_cannot_recognize_itself():
    still = lambda s, t, rng: None if t < 1 else round(float(0.1 * rng.normal()), 4)   # noqa: E731
    rows = [dict(r, s=0.0) for r in _rows(260, still, still, seed=3)]
    p = SL._rung9(_scored(rows))
    assert p["passed"] is False and p["labels"]["glass"] == "other"


# ---- 4. the practice, through the loop ---------------------------------------------------------

def test_the_loop_feeds_the_mirror_and_earns_recognize_itself():
    srv = _seat(world=_Drift(0))
    _run(srv, SL.MIRROR_LAG + SL.MIRROR_MIN_FIT + SL.MIRROR_LOOKS + 120)
    recs = _record(srv)
    lines = [r for r in recs if r["kind"] == "mirror"]
    assert lines and all(r["toy"] == "toy:mirror" for r in lines)
    assert lines[0]["glass"] is None and lines[-1]["stranger"] is not None
    assert any("p_glass" in r and "b_glass" in r for r in lines)
    ach = [r for r in recs if r["kind"] == "achieve" and r["id"] == "mirror"]
    assert ach, SL._rung9(recs)
    st = SL.status(srv)
    assert "mirror" in [e["id"] for e in st["earned"]] and "mirror" in st["awaiting"]   # #299: waits for the accept


def test_no_mirror_lines_without_the_toy():
    srv = _seat(world=_Drift(1), mirror=False)
    SL._append(srv, {"ts": 5.0, "kind": "unlock", "rung": 6, "toys": ["ball", "mirror"],
                     "added": ["toy:ball", "toy:mirror"]})                       # provisioned, then removed
    _run(srv, 30)
    assert not [r for r in _record(srv) if r["kind"] == "mirror"]


def test_no_mirror_lines_while_it_is_closed():
    srv = _seat(rungs=(2, 6), world=_Drift(1), mirror=True)       # feel not earned: the toy, but no skill
    SL._rt(srv)["isolate"] = None
    _run(srv, 30)
    assert not [r for r in _record(srv) if r["kind"] == "mirror"]


# ---- 5. the sheet ----------------------------------------------------------------------------

def test_the_sheet_says_the_mirrors_criteria_in_words():
    recs = _scored(_rows(260, _echo, _old))
    progs = {9: SL._rung9(recs)}
    crits = {c["key"]: c for c in SH._criteria(9, recs, progs)}
    assert set(crits) == {"looks", "self", "other"}
    assert crits["looks"]["text"].startswith(f"{SL.MIRROR_LOOKS}/{SL.MIRROR_LOOKS}")
    assert "mirror" in crits["self"]["text"] and f"{SL.BODY_SKILL:.2f}" in crits["self"]["text"]
    assert "stranger" in crits["other"]["text"]
    assert all(c["met"] for c in crits.values())


def test_the_sheet_knows_when_it_was_built_and_how_fast_it_can_pass():
    assert SH.BUILT_AT["mirror"]["pr"] > 0 and SH.BUILT_AT["mirror"]["merged"] > 0
    assert SH.fastest_cycles()[9] == SL.MIRROR_LAG + 3 + SL.MIRROR_MIN_FIT + SL.MIRROR_LOOKS
    assert "mirror" in SH._needs()[9]
    assert SH.SKILL_LABEL[9]


# ---- 6. the dock ------------------------------------------------------------------------------

def test_the_dock_shows_both_sides_and_how_self_reads_them():
    srv = _seat(world=_Drift(2))
    _run(srv, 5)
    from membrane import self_sheet as SS
    t = next(t for t in SS.dock(srv, fresh=True)["toys"] if t["kind"] == "mirror")
    assert set(t["reading"]) == {"glass", "stranger"} and t["reading"]["glass"] is not None
    assert t["health"] == "ok"                                     # fed, though it has no getter
    assert t["seen_as"] == {"glass": None, "stranger": None}       # not enough looks to say yet


NODE = pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def _js(expr, f="panel.js"):
    import subprocess
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _mirror_toy(**kw):
    t = {"name": "toy:mirror", "kind": "mirror", "reading": {"glass": 0.42, "stranger": -1.3}, "health": "ok",
         "looks": 0, "changes": {"self": 9, "world": 3}, "last": {"by": "self", "what": "show", "age_s": 1},
         "seen_as": {"glass": "body", "stranger": "other"}}
    t.update(kw)
    return t


@NODE
def test_the_glyph_is_a_mirror_with_both_sides():
    g = _js("L.toyGlyph(%s)" % json.dumps(_mirror_toy()))
    assert "🪞" in g["html"]
    assert "mirror" in g["label"] and "stranger" in g["label"] and "Blurp" in g["label"]
    assert "reads the mirror as itself" in g["label"]


@NODE
def test_the_popover_offers_no_pokes():
    h = _js("L.toyPopoverHtml(%s, null)" % json.dumps(_mirror_toy()))
    assert "data-poke-action" not in h
    assert "its own moves" in h


@pytest.fixture(autouse=True)
def _rebuilt_333(monkeypatch):
    """#333: meet, turns, expect and mirror are not built until they are rebuilt
    on the nudge (part 3); the scoring and the readers tested here run on the
    tree as part 3 will build it, their passes on ladder 3 (the loop's old acts
    -- meet's push, the ball's turns, the asks at a push -- are gone)."""
    from tests._self_cap import rebuilt
    rebuilt(monkeypatch)
