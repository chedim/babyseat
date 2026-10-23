"""#295: Blurp's skill tree -- achievements with real prerequisites, earned
out of order, replace the strict ladder of rungs.

Each achievement has a stable id, a name, a branch and prerequisites (ids);
an edge is kept only where the test reads data or machinery another
achievement's pass switches on (each edge says why). A test runs whenever its
prerequisites are earned; a pass appends {"kind": "achieve", "id": ...}.
The record is never rewritten: old `rung` lines count as their achievements
(rung_void and the #258 ladder filter as before). Level = achievements
earned. #299: the growth limit gave way to acceptance (tests/test_accept_299.py);
the deprecated limit -- an achievement (it + its prerequisite closure), off,
all, or a number N (the closure of rungs 1..N) -- now bulk-accepts what is
earned in that closure.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import toy_unlocks as TU  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from tests._self_cap import rebuilt  # noqa: E402

OTHER = MEMBRANE_NS + "Component:chat:in:someone"
BUILT = ["feel", "reach", "kinds", "patience", "shift", "meet", "turns", "expect", "mirror", "change",
         "speak"]   # #311, #312, #320 -- the tree the mechanics below walk (_whole_tree)
LATER = ["mirror", "change", "speak", "invent"]
ORDER = ["feel", "reach", "kinds", "patience", "shift", "nudge", "meet", "turns", "expect"] + LATER   # the tree's order
# #333: first reach, the nudge skill, before its children (3e: meet; 3d: mirror)


@pytest.fixture(autouse=True)
def _whole_tree(request, monkeypatch):
    """#333: meet, turns, expect and mirror are not built until they are rebuilt
    on the nudge (part 3); the tree's mechanics are tested on the whole tree,
    built as part 3 will build it, their passes on ladder 3. The tree's own
    contract (the first test) reads the real one."""
    if request.node.name != "test_the_tree_has_stable_ids_branches_and_real_edges":
        rebuilt(monkeypatch)
CONTRACT = {"id", "name", "branch", "prereqs", "built", "state", "earned_at", "skill", "bar",
            "waiting_on", "child_age", "child_ability", "why_prereqs"}


def _seat(rungs=(), limit=None, lines=()):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    SL.bind(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in rungs:
        line = {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9}
        if n >= 6:
            line["ladder"] = SL.LADDER_V        # #333: rungs 6-9 count on ladder 3
        SL._append(srv, line)
    for ln in lines:
        SL._append(srv, ln)
    rt = SL._rt(srv)
    rt["mode"] = "on"
    if limit is not None:
        rt["limit"] = limit
    return srv


def _expect_passes_turns_not(t0=1000.0, n=110):
    """A small synthetic record: pushes to the other by the timer (no learned
    timing, so turns has nothing to score) and a reply check after each, whose
    outcome self's recorded prediction tracks far better than the running rate."""
    out = []
    for i in range(n):
        t = t0 + 300.0 * i
        o = (i % 2 == 0)
        out.append({"ts": t, "kind": "push", "n": i, "eid": "chat:out", "to": OTHER})
        out.append({"ts": t + 130.0, "kind": "reach", "n": i, "eid": OTHER, "p": 0.5,
                    "answer": "changed" if o else "unchanged", "reply": True, "replied": o,
                    "p_self": 0.9 if o else 0.1})
    return out


# ---- 1. the tree -----------------------------------------------------------------------

def test_the_tree_has_stable_ids_branches_and_real_edges():
    A = SL.ACHIEVEMENTS
    assert list(A) == ORDER
    assert [a for a in A if A[a]["built"]] == [a for a in ORDER if a in BUILT + ["nudge"]
                                               and a not in ("turns", "expect")]   # #333 (3d: mirror; 3e: meet)
    assert A["nudge"]["prereqs"] == ("feel",) and A["nudge"]["built"]    # #333 part 3c: first reach, its test built
    br = {a: A[a]["branch"] for a in A}
    assert br["feel"] == "self"
    assert {br[a] for a in ("reach", "kinds", "patience", "shift")} == {"world"}
    assert {br[a] for a in ("meet", "turns", "expect")} == {"others"}
    assert {br[a] for a in ("mirror", "change")} == {"self"}          # #329: about self, beside feel
    assert br["speak"] == "others" and br["invent"] == "self"     # #353: no Later lane
    pre = {a: set(A[a]["prereqs"]) for a in A}
    assert pre["feel"] == set() and pre["reach"] == set()          # two roots: reaching never read feel's data
    assert pre["kinds"] == {"reach"}
    assert pre["patience"] == {"kinds"}
    assert pre["shift"] == {"reach"}                               # its noticing reads the tempo, not patience's rates
    assert pre["meet"] == {"reach", "nudge"}                       # #333 part 3e: the look after first reach's check
    assert pre["turns"] == {"meet"} and pre["expect"] == {"meet"}  # siblings: either can come first
    assert pre["change"] == {"mirror", "patience"}                 # #320: its dream replays patience's timed looks
    for a in A:
        for p in A[a]["prereqs"]:
            assert p in A and list(A).index(p) < list(A).index(a)   # acyclic, prerequisites listed first
            assert p in A[a]["why_prereqs"] or A[a]["why_prereqs"]  # every edge says why
        assert A[a]["child_age"] and A[a]["child_ability"]
    # the rung numbers the record has always used map onto the ids
    assert [SL.RUNG_ID[n] for n in range(1, 9)] == ["feel", "reach", "kinds", "patience", "shift", "meet",
                                                     "turns", "expect"]
    assert SL.RUNG_ID[13] == "nudge"
    assert A["expect"]["name"] == "expect a reply" and A["turns"]["name"] == "time its turn"   # #300


def test_child_ages_follow_what_each_test_measures():
    age = {a: SL.ACHIEVEMENTS[a]["child_age"] for a in SL.ACHIEVEMENTS}
    assert age["feel"] == "newborn" and age["mirror"] == "~18 mo" and age["invent"] == "~5 y"
    assert age["expect"] == "~2-3 mo"                              # expecting a response (still-face)
    assert "Tronick" in SL.ACHIEVEMENTS["expect"]["child_ability"]


# ---- 2. the record: old rung lines count, voids hold ---------------------------------------

def test_old_rung_lines_count_as_their_achievements_and_the_void_holds():
    srv = _seat(rungs=(1, 2, 3, 4, 5, 6), lines=[
        {"ts": 50.0, "kind": "rung", "rung": 7, "skill": 0.3},                     # the old rung 7 (pre-flip)
        {"ts": 60.0, "kind": "rung_void", "rung": 7, "voids_ts": 50.0, "why": "#254"}])
    recs = SL._read(srv)
    assert SL._earned(recs) == {"feel", "reach", "kinds", "patience", "shift", "meet"}
    st = SL.status(srv)
    assert st["level"] == 6 and st["rung"] == 6


def test_a_post_flip_rung_line_counts_and_an_achieve_line_counts():
    srv = _seat(rungs=(1, 2, 3, 4, 5, 6, 7))
    assert "turns" in SL._earned(SL._read(srv))
    SL._append(srv, {"ts": 99.0, "kind": "achieve", "id": "expect", "skill": 0.3, "ladder": 3})
    assert SL._earned(SL._read(srv)) == set(BUILT) - {"speak", "mirror", "change"}
    assert 8 in SL._passed(SL._read(srv))                          # the toys and the bug windows read rung numbers


# ---- 3. out of order: expect before turns -----------------------------------------------

def test_expect_is_earned_while_turns_stays_open():
    srv = _seat(rungs=(1, 2, 3, 4, 5, 6), limit="all", lines=_expect_passes_turns_not())
    recs = SL._read(srv)
    assert SL._rung8(recs)["passed"] and not SL._rung7(recs)["passed"]
    SL._open_rungs(srv)
    got = [r for r in SL._read(srv) if r.get("kind") == "achieve"]
    assert [r["id"] for r in got] == ["expect"]
    assert got[0]["skill"] > SL.RUNG8_MARGIN and isinstance(got[0]["ts"], float)
    st = SL.status(srv)
    assert st["level"] == 7
    assert st["rung_name"] == "expect a reply"                     # #300
    assert st["next"] == ["nudge", "turns"]                        # #333: first reach with feel (3d: mirror with feel + first reach; 3e: tree order)
    assert [e["id"] for e in st["earned"]][-1] == "expect"
    tree = {t["id"]: t for t in SS.compute(srv)["tree"]}
    assert tree["expect"]["state"] == "earned" and tree["turns"]["state"] == "open"
    assert tree["change"]["state"] == "locked" and tree["invent"]["state"] == "unbuilt"   # #320: change is built


def test_a_root_needs_nothing_and_a_child_waits_for_its_parent():
    srv = _seat(limit="all")
    assert SL._available(srv, 2)                                   # reach: a root, practised from birth
    assert not SL._available(srv, 3)                               # kinds waits for reach
    srv = _seat(rungs=(2,), limit="all")
    assert SL._available(srv, 3) and SL._available(srv, 5)
    assert not SL._available(srv, 6)                               # #333 part 3e: meet waits for first reach too
    assert not SL._available(srv, 4)                               # patience waits for kinds


def test_the_reply_checks_run_for_either_sibling():
    srv = _seat(rungs=(2, 6))                                       # meet earned and accepted: both siblings open
    assert SL._available(srv, 8) and SL._available(srv, 7)
    assert SL._replies(srv)
    srv = _seat(rungs=(2,), lines=[{"ts": 2.5, "kind": "accept_migrated", "ids": ["reach"]},
                                   {"ts": 9.0, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0,
                                    "ladder": 3}])
    assert not SL._replies(srv)                                    # #299: meet earned, not accepted: no reply checks
    SL.accept(srv, "meet")
    assert SL._replies(srv)


# ---- 4. the growth limit, deprecated (#299): a bulk accept of its closure -------------------

def _accepts(srv):
    return [r["id"] for r in SL._read(srv) if r.get("kind") == "accept"]


def test_the_limit_accepts_an_achievement_and_its_closure():
    srv = _seat(rungs=(1, 2, 6), lines=[{"ts": 9.5, "kind": "accept_migrated", "ids": []}])
    assert SL.status(srv)["awaiting"] == ["feel", "reach", "meet"]
    assert SL.set_limit(srv, "expect")                             # its closure: reach, feel, nudge, meet, expect
    assert _accepts(srv) == ["feel", "reach", "meet"]              # the earned ones in it, in tree order
    assert SL.set_limit(srv, "all") and _accepts(srv) == ["feel", "reach", "meet"]   # #333 3e: nothing left
    assert not SL.set_limit(srv, "nonsense") and not SL.set_limit(srv, "invent")   # unbuilt: nothing to accept
    assert SL.set_limit(srv, 5) and SL.set_limit(srv, 8)           # nothing left to accept: no new lines
    assert len(_accepts(srv)) == 3
    assert SL.set_limit(srv, "off") and SL.mode(srv) == "off" and SL._allowed(srv) == set()


def test_a_saved_limit_is_ignored_and_the_mode_kept():
    srv = _seat()
    d = SL._dir(srv)
    (d / "mode.json").write_text(json.dumps({"mode": "on", "limit": 3, "allow": ["feel", "reach", "kinds"]}))
    assert SL._load_mode(srv) == "on"
    SL.set_mode(srv, "watch")
    assert json.loads((d / "mode.json").read_text())["mode"] == "watch"


def test_a_test_under_an_unaccepted_parent_does_not_earn():
    srv = _seat(rungs=(1, 2, 3, 4, 5), lines=[{"ts": 9.5, "kind": "accept_migrated",
                                               "ids": ["feel", "reach", "kinds", "patience", "shift"]},
                                              {"ts": 9.6, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0,
                                               "ladder": SL.LADDER_V}]
                + _expect_passes_turns_not())
    SL._open_rungs(srv)
    assert not [r for r in SL._read(srv) if r.get("kind") == "achieve" and r.get("id") != "meet"]
    tree = {t["id"]: t for t in SS.compute(srv)["tree"]}
    assert tree["expect"]["state"] == "locked" and tree["expect"]["waiting_on"] == "your accept of meet an other"
    assert tree["meet"]["state"] == "earned" and tree["meet"]["accepted"] is False


# ---- 5. the data contract ----------------------------------------------------------------------

def test_the_sheet_and_the_dock_carry_the_tree_and_the_level():
    srv = _seat(rungs=(1, 2, 3), limit="all")
    sh = SS.compute(srv)
    assert sh["level"] == 3 and sh["header"]["level"] == 3
    rows = sh["tree"]
    assert [r["id"] for r in rows] == ORDER
    for r in rows:
        assert set(r) >= CONTRACT, set(CONTRACT) - set(r)
        assert r["state"] in ("locked", "open", "earned", "unbuilt")
        assert r["branch"] in ("self", "world", "others")
        assert 0.0 <= r["bar"] <= 1.0
    by = {r["id"]: r for r in rows}
    assert by["kinds"]["state"] == "earned" and by["kinds"]["earned_at"].startswith("1970")
    assert by["kinds"]["skill"] == 0.9 and by["kinds"]["bar"] == 1.0
    assert by["patience"]["state"] == "open" and by["shift"]["state"] == "open" and by["nudge"]["state"] == "open"
    assert by["meet"]["state"] == "locked" and by["meet"]["waiting_on"] == "first reach"   # #333 part 3e
    assert by["turns"]["state"] == "locked" and by["turns"]["skill"] is None
    assert "meet an other" in by["turns"]["waiting_on"]
    assert by["patience"]["why_prereqs"]
    dock = SS.dock(srv, fresh=True)
    assert dock["level"] == 3 and [r["id"] for r in dock["tree"]] == ORDER


def test_the_card_counts_achievements():
    srv = _seat(rungs=(1, 2, 3, 4, 5, 6), limit="all", lines=_expect_passes_turns_not())
    SL._open_rungs(srv)
    c = SS.card(srv)
    assert c["level"] == 7 and c["rung_name"] == "expect a reply" and c["next"] == ["nudge", "turns"]   # #300, #333


# ---- 6. the toys unlock by achievement -------------------------------------------------------

def test_toys_unlock_by_achievement():
    assert TU.UNLOCK_IDS["ball"] == "nudge" and TU.UNLOCK_IDS["tower"] == "feel"   # #333 part 3e: #337's order
    assert TU.UNLOCK_IDS["mirror"] == "nudge"                      # #312: with its last prerequisite
    srv = _seat(rungs=(2,))
    why = TU.refusal(srv, "ball")
    assert why and "first reach" in why


# ---- 7. home's celebration: once per newly earned achievement ----------------------------------

NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr):
    js = 'const H=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(ROOT / "membrane" / "ui" / "home.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _cel(earned, last, old=None):
    return _js("H.achievementsToCelebrate(%s, %s, %s)" % (json.dumps(earned), json.dumps(last), json.dumps(old)))


@NODE
def test_a_newly_earned_achievement_celebrates_once():
    six = ["feel", "reach", "kinds", "patience", "shift", "meet"]
    out = _cel(six + ["expect"], json.dumps(six))
    assert out == {"ids": ["expect"], "last": six + ["expect"]}
    assert _cel(six + ["expect"], json.dumps(out["last"])) == {"ids": [], "last": six + ["expect"]}   # a reload


@NODE
def test_the_first_visit_after_the_upgrade_never_re_celebrates_the_rungs():
    six = ["feel", "reach", "kinds", "patience", "shift", "meet"]
    assert _cel(six, None, "6") == {"ids": [], "last": six}         # the old per-rung memory: rungs 1-6 seen
    assert _cel(six + ["expect"], None, "6") == {"ids": ["expect"], "last": six + ["expect"]}
    assert _cel(six, None, None) == {"ids": [], "last": six}        # a first visit ever: no backlog


@NODE
def test_a_voided_achievement_is_dropped_quietly_and_re_earning_is_news():
    out = _cel(["feel"], json.dumps(["feel", "turns"]))
    assert out == {"ids": [], "last": ["feel"]}
    assert _cel(["feel", "turns"], json.dumps(out["last"]))["ids"] == ["turns"]
    assert _cel(None, json.dumps(["feel"])) == {"ids": [], "last": ["feel"]}


@NODE
def test_the_caption_and_key_name_the_achievement():
    assert _js("H.achieveText('Blurp', 'understand an other')") == "Blurp earned understand an other"
    k = _js("H.achieveKey('Argus', 'Blurp')")
    assert k.startswith("membrane.home.achieve") and k != _js("H.achieveKey('Argus', 'Zed')")


def test_the_page_celebrates_from_the_earned_list():
    home = (ROOT / "membrane" / "ui" / "home.js").read_text()
    assert "achievementsToCelebrate(" in home and "s.earned" in home
    assert 'k === "achieve"' in home                                # the brain caption knows the line
