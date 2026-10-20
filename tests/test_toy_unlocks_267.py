"""#267 (part of #161): toys unlock as the operator's rewards when self passes rungs.

Each toybox kind has an unlock rung (membrane/toy_unlocks.py): body, fast and
slow at birth; tower at rung 3; burst at 4; die and drift at 5; ball (#264)
at 6; and three new kinds that land as LOCKED PLACEHOLDERS (a name, an icon,
the rung and one line of what each will teach; no mechanics yet): mirror at
8, blocks at 9, rattle at 10. "Passes rung N" is the ladder's own rule
(self_loop._passed: void-aware, and a pass above rung 6 only with
"ladder": 2).

On a pass that unlocks something the record gets one line {"kind": "unlock",
"rung": N, "toys": [...]}, and the unlocked built-in toys are provisioned
(once: a kind the seat already has is left alone). No toy ever disappears:
a toy whose kind is still locked stays (grandfathered); adding a locked kind
is refused. An unlock never feeds a rung's test.
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

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
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
    srv.efield = None
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    TB.bind(srv)
    return srv


def _born(srv, ts=1000.0):
    SL._append(srv, {"ts": ts, "kind": "birth", "by": "operator"})


def _pass(srv, *rungs, ts=2000.0, ladder=True):
    for n in rungs:
        line = {"ts": ts, "kind": "rung", "rung": n, "skill": 0.9}
        if ladder:
            line["ladder"] = SL.LADDER_V
        SL._append(srv, line)


def _unlocks(srv):
    return [r for r in SL._read(srv) if r.get("kind") == "unlock"]


def _kinds():
    return sorted({t["kind"] for t in TB.list_toys()})


# ---- the table --------------------------------------------------------------------

def test_the_table():
    assert TU.UNLOCKS == {"body": 0, "fast": 0, "slow": 0, "tower": 3, "burst": 4, "die": 5, "drift": 5,
                          "ball": 6, "mirror": 8, "blocks": 9, "rattle": 10}


def test_every_built_kind_has_an_unlock_rung():
    assert set(TB.KINDS) <= set(TU.UNLOCKS)


def test_the_placeholders_have_a_name_an_icon_a_rung_and_a_lesson():
    assert set(TU.PLACEHOLDERS) == {"mirror", "blocks", "rattle"}
    assert {k: TU.ICONS[k] for k in TU.PLACEHOLDERS} == {"mirror": "🪞", "blocks": "🧱", "rattle": "🔊"}
    for k in TU.PLACEHOLDERS:
        assert TU.TEACHES[k] and "\n" not in TU.TEACHES[k]
        assert k not in TB.KINDS                       # no mechanics yet: never provisioned


def test_every_kind_in_the_table_has_an_icon_and_a_lesson():
    for k in TU.UNLOCKS:
        assert TU.ICONS.get(k) and TU.TEACHES.get(k), k


def test_unlocked_by_the_passed_rungs():
    assert TU.unlocked({0}) == ["body", "fast", "slow"]
    assert TU.unlocked({0, 1, 2, 3}) == ["body", "fast", "slow", "tower"]
    assert TU.unlocked({0, 1, 2, 3, 4, 5}) == ["body", "fast", "slow", "tower", "burst", "die", "drift"]
    assert "mirror" in TU.unlocked(set(range(9))) and "blocks" not in TU.unlocked(set(range(9)))
    assert TU.unlocked(5) == TU.unlocked({0, 1, 2, 3, 4, 5})     # a level: every rung up to it


def test_unlocked_is_void_aware():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, ts=2000.0)
    SL._append(srv, {"ts": 2100.0, "kind": "rung_void", "rung": 3, "voids_ts": 2000.0})
    assert "tower" not in TU.unlocked(TU.passed(srv))
    _pass(srv, 3, ts=2200.0)                                     # a later pass counts
    assert "tower" in TU.unlocked(TU.passed(srv))


def test_unlocked_honours_the_ladder_flip():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, 4, 5, 6, 7)
    _pass(srv, 8, ladder=False)                                   # an old numbering's line: not rung 8
    assert "mirror" not in TU.unlocked(TU.passed(srv))
    _pass(srv, 8)
    assert "mirror" in TU.unlocked(TU.passed(srv))


# ---- the unlock line and provisioning ---------------------------------------------------

def test_birth_kinds_are_unlocked_from_the_start_without_a_line_or_a_toy():
    """Birth is not a pass: body, fast and slow are simply unlocked (the
    operator adds them, as before) -- no unlock line, nothing switched on."""
    srv = _seat()
    _born(srv)
    TU.sync(srv, now=1001.0)
    assert _unlocks(srv) == []
    assert _kinds() == []
    assert TU.unlocked(TU.passed(srv)) == ["body", "fast", "slow"]
    assert TB.add(srv, "body")["ok"]


def test_the_unlock_line_is_written_once_and_provisioning_is_idempotent():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, ts=2000.0)
    TU.sync(srv, now=2001.0)
    TU.sync(srv, now=2002.0)
    TU.sync(srv, now=2003.0)
    rows = _unlocks(srv)
    assert [r["rung"] for r in rows] == [3]
    assert rows[0]["toys"] == ["tower"] and rows[0]["kind"] == "unlock" and rows[0]["added"] == ["toy:tower"]
    assert _kinds() == ["tower"]
    assert [t["name"] for t in TB.list_toys()].count("toy:tower") == 1


def test_a_kind_the_seat_already_has_is_not_provisioned_again():
    srv = _seat()
    assert TB.add(srv, "tower", name="toy:my-tower")["ok"]        # unborn: the bench is open
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    assert [t["name"] for t in TB.list_toys() if t["kind"] == "tower"] == ["toy:my-tower"]


def test_a_removed_toy_is_not_brought_back():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    assert TB.remove(srv, "toy:tower")["ok"]
    TU.sync(srv, now=2002.0)
    assert "tower" not in _kinds()


def test_a_placeholder_unlock_is_announced_but_provisions_nothing():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, 4, 5, 6, 7, 8)
    TU.sync(srv, now=2001.0)
    row = next(r for r in _unlocks(srv) if r["rung"] == 8)
    assert row["toys"] == ["mirror"]
    assert "mirror" not in _kinds()


def test_ball_is_tolerated_when_its_kind_is_not_built():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, 4, 5, 6)
    TU.sync(srv, now=2001.0)                                     # no error, whatever the kit holds
    row = next(r for r in _unlocks(srv) if r["rung"] == 6)
    assert row["toys"] == ["ball"]
    assert ("ball" in _kinds()) == ("ball" in TB.KINDS)


def test_a_late_unlock_is_marked_so_the_page_does_not_celebrate_it():
    srv = _seat()
    _born(srv, ts=1000.0)
    _pass(srv, 1, 2, 3, ts=2000.0)
    TU.sync(srv, now=2000.0 + 3600)                              # an upgraded seat: the pass is old news
    assert all(r.get("late") for r in _unlocks(srv))
    srv2 = _seat(Path(os.environ["MEMBRANE_DATA_DIR"]) / "two")
    _born(srv2, ts=5000.0)
    _pass(srv2, 1, 2, 3, ts=5000.0)
    TU.sync(srv2, now=5001.0)
    assert not _unlocks(srv2)[0].get("late")


def test_no_provisioning_into_another_data_dirs_toybox(tmp_path):
    srv = _seat(tmp_path / "elsewhere")                          # its record is not the toybox's data dir
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    assert [r["rung"] for r in _unlocks(srv)] == [3]             # the line, yes
    assert TB.list_toys() == []                                  # ...the toys, no


def test_nothing_before_birth():
    srv = _seat()
    assert TU.sync(srv, now=1.0) == []
    assert _unlocks(srv) == []


def test_the_cycle_runs_the_sync_right_after_the_rungs_open():
    import inspect
    src = inspect.getsource(SL.cycle)
    assert "_open_rungs(srv)\n    _unlock_toys(srv)" in src
    assert "toy_unlocks" in inspect.getsource(SL._unlock_toys)


# ---- no toy ever disappears; a locked kind is refused ----------------------------------

def test_grandfathered_toys_stay_and_are_marked():
    srv = _seat()
    assert TB.add(srv, "die")["ok"]                               # before birth: a die (unlocks at 5)
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    assert "die" in _kinds()
    d = {t["name"]: t for t in SH.dock(srv, fresh=True)["toys"]}
    assert d["toy:die"]["grandfathered"] is True and d["toy:die"]["unlock_rung"] == 5
    assert d["toy:tower"]["grandfathered"] is False


def test_adding_a_locked_kind_is_refused_with_a_clear_message():
    srv = _seat()
    _born(srv)
    out = TB.add(srv, "tower")
    assert not out["ok"]
    assert "locked" in out["error"] and "'tell things apart'" in out["error"]   # #295: the achievement (rung 3's test)
    assert TB.add(srv, "fast", name="toy:f2")["ok"]               # an unlocked kind: fine


def test_a_locked_placeholder_is_refused_and_an_unlocked_one_is_not_built_yet():
    srv = _seat()
    _born(srv)
    out = TB.add(srv, "mirror")
    assert not out["ok"] and "locked" in out["error"] and "'expect a reply'" in out["error"]   # #295, #300
    _pass(srv, 1, 2, 3, 4, 5, 6, 7, 8)
    out = TB.add(srv, "mirror")
    assert not out["ok"] and "not built yet" in out["error"]


def test_the_toybox_object_refuses_a_locked_kind():
    srv = _seat()
    _born(srv)
    out = TB.from_command("add tower")
    assert out["rc"] == 2 and "locked" in out["text"]
    assert TB.from_command("add mirror")["rc"] == 2


def test_the_cli_refuses_a_locked_kind(capsys):
    srv = _seat()
    _born(srv)
    SL._RT.pop(id(srv), None)
    assert TB.cli_main(["add", "tower"]) == 2
    assert "'tell things apart'" in capsys.readouterr().err   # #295: the achievement that unlocks it
    assert TB.cli_main(["add", "mirror"]) == 2
    assert "locked" in capsys.readouterr().err
    assert TB.cli_main(["add", "slow"]) == 0


def test_the_cli_adds_an_unlocked_kind_after_its_rung():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3)
    SL._RT.pop(id(srv), None)
    assert TB.cli_main(["add", "tower", "--name", "toy:t2"]) == 0


def test_placeholders_are_unpokeable():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, 4, 5, 6, 7, 8)
    TU.sync(srv, now=2001.0)
    for name in ("toy:mirror", "locked:mirror", "mirror", "locked:blocks"):
        out = TB.poke(name, "poke", by="alice")
        assert not out["ok"], name
    assert "not built yet" in TB.poke("toy:mirror", "look")["error"]


def test_migrate_keeps_its_toys_whatever_the_rung():
    srv = _seat()
    _born(srv)
    out = TB.add(srv, "tower", locked_ok=True)                     # what migrate replaces with
    assert out["ok"]


# ---- an unlock never affects grading ---------------------------------------------------

def test_no_rung_test_reads_unlocks():
    """Every rung's PROGRESS gives the same answer with the unlock lines
    taken out of the record."""
    srv = _seat()
    _born(srv)
    rng = np.random.default_rng(3)
    t = 1001.0
    for i in range(400):
        t += 10
        SL._append(srv, {"ts": t, "kind": "cycle", "n": i, "act": "move", "step": 0.1, "push": 0.2,
                         "gap": 0.05, "naive": 0.2, "change": "world"})
        if i % 3 == 0:
            SL._append(srv, {"ts": t + 1, "kind": "reach", "eid": "membrane::Component:toy:fast",
                             "p": float(rng.random()), "answer": "changed" if rng.random() < 0.5 else "unchanged",
                             "steer": True, "timed": i % 2 == 0})
    _pass(srv, 1, 2, 3, 4, 5, 6, ts=t)
    TU.sync(srv, now=t + 1)
    recs = list(SL._read(srv))
    assert any(r.get("kind") == "unlock" for r in recs)
    bare = [r for r in recs if r.get("kind") != "unlock"]
    for n, fn in SL.PROGRESS.items():
        assert fn(recs, {}) == fn(bare, {}), n


def test_no_rung_test_names_unlocks_in_its_source():
    import inspect
    for n, fn in SL.PROGRESS.items():
        assert "unlock" not in inspect.getsource(fn), n


# ---- the sheet and the status --------------------------------------------------------

def test_the_sheet_has_a_toys_section_with_locked_and_unlocked():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    s = SH.compute(srv)
    rows = {r["kind"]: r for r in s["toys"]}
    assert set(rows) == set(TU.UNLOCKS)
    assert rows["tower"]["state"] == "unlocked" and rows["tower"]["have"] is True
    assert rows["tower"]["at"]                                    # when it unlocked
    assert rows["mirror"]["state"] == "locked" and rows["mirror"]["rung"] == 8 and rows["mirror"]["icon"] == "🪞"
    assert rows["mirror"]["teaches"]


def test_the_achievements_carry_the_toys_each_pass_unlocked():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3)
    TU.sync(srv, now=2001.0)
    a = {x["rung"]: x for x in SH.compute(srv)["achievements"]}
    assert a[3]["toys"] == ["tower"]
    assert a[1].get("toys") in (None, [])


def test_status_carries_the_latest_unlock():
    srv = _seat()
    _born(srv)
    _pass(srv, 1, 2, 3, ts=2000.0)
    TU.sync(srv, now=2001.0)
    u = SL.status(srv)["unlock"]
    assert u["rung"] == 3 and u["toys"] == [{"kind": "tower", "icon": TU.ICONS["tower"]}]


def test_the_dock_lists_the_next_locked_toys_capped():
    srv = _seat()
    _born(srv)
    assert TB.add(srv, "fast")["ok"]
    d = SH.dock(srv, fresh=True)
    assert [t["name"] for t in d["toys"]] == ["toy:fast"]
    lk = d["locked"]
    assert len(lk) == TU.NEXT_LOCKED == 2
    assert [x["kind"] for x in lk] == ["tower", "burst"]
    assert lk[0] == {"name": "locked:tower", "kind": "tower", "locked": True, "icon": TU.ICONS["tower"],
                     "unlock_rung": 3, "unlock_id": "kinds", "rung_name": SL.RUNG_NAMES[3], "teaches": TU.TEACHES["tower"]}


def test_a_kept_toy_is_not_shown_again_as_locked():
    srv = _seat()
    assert TB.add(srv, "tower")["ok"]                              # before birth: kept
    _born(srv)
    TU.sync(srv, now=1001.0)
    d = SH.dock(srv, fresh=True)
    assert [x["kind"] for x in d["locked"]] == ["burst", "die"]


def test_an_unborn_seat_has_nothing_locked():
    srv = _seat()
    assert SH.dock(srv, fresh=True)["locked"] == []
    assert TB.add(srv, "tower")["ok"]


# ---- the page (node) ------------------------------------------------------------------

def _js(expr, f="panel.js", name="L"):
    js = ('const %s=require(%r); process.stdout.write(JSON.stringify(%s))' % (name, str(UI / f), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


LOCKED = {"name": "locked:mirror", "kind": "mirror", "locked": True, "icon": "🪞", "unlock_rung": 8,
          "rung_name": "understand an other", "teaches": "shows self its own trace beside another's"}


@NODE
def test_the_dock_renders_a_locked_toy_greyed_with_a_lock():
    h = _js("L.toyDockHtml(%s)" % json.dumps([LOCKED]))
    assert 'class="dk locked"' in h and "🔒" in h and "🪞" in h
    it = _js("L.toyDockItem(%s)" % json.dumps(LOCKED))
    assert it["label"] == "mirror, locked: unlocks when Blurp passes rung 8: shows self its own trace beside another's"
    assert it["badge"] is None


@NODE
def test_a_locked_toy_offers_no_play():
    assert _js("L.toyActions(%s)" % json.dumps(dict(LOCKED, toy="locked:mirror"))) == []
    pop = _js("L.toyPopoverHtml(%s)" % json.dumps(LOCKED))
    assert "unlocks when Blurp passes rung 8" in pop and "data-poke-toy" not in pop


@NODE
def test_a_grandfathered_toy_says_so():
    t = {"name": "toy:die", "kind": "die", "reading": {"value": 3}, "health": "ok", "grandfathered": True,
         "unlock_rung": 5}
    assert "kept from before" in _js("L.toyPopoverHtml(%s)" % json.dumps(t))


@NODE
def test_the_level_up_caption_mentions_the_new_toy():
    st = {"self": {"mode": "on", "rung": 8, "rung_name": "understand an other", "bar": 0, "next": None, "limit": 8,
                   "unlock": {"rung": 8, "toys": [{"kind": "mirror", "icon": "🪞"}]}}}
    x = _js("L.selfExp(%s)" % json.dumps(st))
    assert x["newToy"] == "new toy: 🪞 mirror"
    assert _js("L.levelUpText(L.selfExp(%s))" % json.dumps(st)) == "⬆ level up: Lv 8 · understand an other · new toy: 🪞 mirror"
    st["self"]["unlock"]["rung"] = 3                               # an older unlock is no news at rung 8
    assert _js("L.selfExp(%s).newToy" % json.dumps(st)) == ""


@NODE
def test_the_feed_and_the_brain_caption_say_new_toy():
    r = {"kind": "unlock", "rung": 8, "toys": ["mirror"]}
    assert _js("L.selfLine(%s)" % json.dumps(r)) == "new toy: 🪞 mirror (rung 8 passed)"
    assert "new toy: 🪞 mirror" in _js("H.brainCaption(%s, '')" % json.dumps(r), "home.js", "H")
    assert _js("H.unlockText(%s)" % json.dumps(r), "home.js", "H") == "new toy: 🪞 mirror"
    assert _js("H.unlockText(%s)" % json.dumps({"kind": "unlock", "rung": 3, "toys": ["tower", "ball"]}),
               "home.js", "H") == "new toys: 🗼 tower, ⚽ ball"


def test_the_home_brain_rings_gold_for_a_fresh_unlock():
    home = (UI / "home.js").read_text()
    assert 'r.kind === "unlock" && !r.late' in home and "v.selfLevelUp(r.rung, unlockText(r))" in home
    f3 = (UI / "field3d.js").read_text()
    assert "selfLevelUp(rung, extra)" in f3


def test_the_dock_reads_the_locked_rows_too():
    panel = (UI / "panel.js").read_text()
    assert "r.data.toys.concat(Array.isArray(r.data.locked) ? r.data.locked : [])" in panel


def test_the_locked_dock_css_respects_the_themes_and_motion():
    home = (UI / "home.js").read_text()
    assert ".toydock .dk.locked .dkb" in home and "grayscale" in home
