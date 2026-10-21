"""#296: the 👶 panel's "Skill tree" tab -- self's achievements as an RPG
skill tree.

The page decides with pure helpers in membrane/ui/panel.js:
skillTree(sheet) reads the sheet's `tree` and `level` when the seat sends them
(#295) and, until then, derives a fallback tree from the ladder fields (passed
rungs, the next rung, its bar) with a fixed rung -> skill mapping; treeLayout
puts the nodes in lanes by branch (Self / World / Others), each node
below its prerequisites, with the edges; nodeCard is what a hover, focus or
tap shows; achievementsList is the earned ones, newest first; ringDash is the
open node's progress ring. renderSkillTree builds the tab with DOM calls only
(createElement + textContent): data-derived text never goes through innerHTML.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _run(body):
    """Run `body` (JS, with P = panel.js's exports) and return its `out` as JSON."""
    js = "const P=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(UI / "panel.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _tree(sheet):
    return _run("out = P.skillTree(%s)" % json.dumps(sheet))


# a sheet from today's ladder: rungs 1-3 passed, rung 4 the quest
LADDER = {
    "header": {"name": "Blurp", "level": 3, "next": 4, "bar": 0.4, "waiting_on": "12 more timed looks",
               "skill": {"rung": 4, "value": 0.21, "pass": 0.1}},
    "achievements": [{"rung": 1, "name": "feel itself", "at": "2026-10-15T10:00:00+00:00", "skill": 0.4},
                     {"rung": 2, "name": "first reach", "at": "2026-10-16T10:00:00+00:00", "skill": 0.31},
                     {"rung": 3, "name": "tell things apart", "at": "2026-10-17T10:00:00+00:00", "skill": 0.12}],
    "quest": {"rung": 4, "name": "patience", "bar": 0.4, "waiting_on": "12 more timed looks"},
    "growth": {"rows": [{"rung": n, "needs": "test %d" % n, "child_months": m, "child_milestone": "ability %d" % n}
                        for n, m in ((1, 0.1), (2, 3.0), (3, 4.0), (4, 3.5), (5, 5.0), (6, 3.0), (7, 4.0), (8, 9.0),
                                     (9, 18.0), (10, 48.0), (11, 48.0))]},
}

SEAT_TREE = {
    "header": {"name": "Pip", "level": 1},
    "level": 2,
    "tree": [
        {"id": "feel", "name": "feel itself", "branch": "self", "prereqs": [], "built": True, "state": "earned",
         "earned_at": "2026-10-15T10:00:00+00:00", "skill": 0.4, "bar": 1, "waiting_on": None,
         "child_age": "newborn", "child_ability": "tells its own touch", "why_prereqs": ""},
        {"id": "meet", "name": "<b>meet</b> an other", "branch": "others", "prereqs": ["feel"], "built": True,
         "state": "earned", "earned_at": "2026-10-18T10:00:00+00:00", "skill": 1.0, "bar": 1, "waiting_on": None,
         "child_age": "3 months", "child_ability": "tells self from social contingency",
         "why_prereqs": "it needs its own feel to tell an other's"},
        {"id": "expect", "name": "understand an other", "branch": "others", "prereqs": ["meet"], "built": True,
         "state": "open", "earned_at": None, "skill": -0.05, "bar": 1.7, "waiting_on": "reply checks",
         "child_age": "9 months", "child_ability": "predicts what others will do", "why_prereqs": "it reads meet's lines"},
        {"id": "speak", "name": "speak", "branch": "others", "prereqs": ["expect", "ghost"], "built": False,
         "state": "weird", "earned_at": None, "skill": None, "bar": None, "waiting_on": None,
         "child_age": "4 years", "child_ability": "tells its own story", "why_prereqs": ""},
    ],
}


# ---- before the core lands: the ladder read as a tree -------------------------------------

@NODE
def test_without_a_tree_the_ladder_is_read_as_a_tree():
    t = _tree(LADDER)
    assert t["source"] == "ladder" and t["level"] == 3 and t["name"] == "Blurp"
    by = {n["id"]: n for n in t["nodes"]}
    assert [n["id"] for n in t["nodes"]] == ["feel", "reach", "kinds", "patience", "shift", "meet", "turns", "expect",
                                             "mirror", "change", "speak", "invent"]
    assert by["feel"]["branch"] == "self"
    assert {k: by[k]["prereqs"] for k in ("kinds", "patience", "shift", "meet", "turns", "expect")} == \
        {"kinds": ["reach"], "patience": ["kinds"], "shift": ["reach"], "meet": ["kinds"], "turns": ["meet"], "expect": ["meet"]}
    for k in ("meet", "turns", "expect"):
        assert by[k]["branch"] == "others"
    for k in ("mirror", "change", "speak", "invent"):
        assert by[k]["state"] == "unbuilt" and by[k]["built"] is False
    assert {k: by[k]["branch"] for k in ("mirror", "change", "speak", "invent")} == \
        {"mirror": "self", "change": "self", "speak": "others", "invent": "self"}       # #329, #353
    # the passed rungs are earned, with their dates and skills
    assert [by[k]["state"] for k in ("feel", "reach", "kinds")] == ["earned"] * 3
    assert by["reach"]["earned_at"] == "2026-10-16T10:00:00+00:00" and by["reach"]["skill"] == 0.31
    # the next rung is the one open node: its bar, its skill and what it waits on
    assert by["patience"]["state"] == "open" and by["patience"]["bar"] == 0.4
    assert by["patience"]["waiting_on"] == "12 more timed looks" and by["patience"]["skill"] == 0.21
    # the strict ladder opens nothing else, even where the tree's prereqs are met
    assert [by[k]["state"] for k in ("shift", "meet", "turns", "expect")] == ["locked"] * 4
    # the child analogy and the test come from the growth rows
    assert by["expect"]["child_ability"] == "ability 8" and by["expect"]["child_age"] == "9 months"
    assert by["feel"]["child_age"] == "newborn" and by["change"]["child_age"] == "4 years"
    assert by["kinds"]["test"] == "test 3"
    assert by["kinds"]["why_prereqs"]


@NODE
def test_a_fresh_seat_has_nothing_earned_and_nothing_open_when_asleep():
    t = _tree({"header": {"name": "Blurp", "level": 0, "next": None}, "achievements": [], "quest": {"rung": None}})
    assert t["level"] == 0
    assert [n["state"] for n in t["nodes"] if n["built"]] == ["locked"] * 8


@NODE
def test_no_sheet_is_no_tree():
    assert _tree(None) is None
    assert _tree({"nope": 1}) is None


# ---- after the core lands: the seat's tree, as it says -----------------------------------

@NODE
def test_the_seats_tree_is_read_as_it_is():
    t = _tree(SEAT_TREE)
    assert t["source"] == "seat" and t["level"] == 2 and t["name"] == "Pip"
    by = {n["id"]: n for n in t["nodes"]}
    assert by["meet"]["name"] == "<b>meet</b> an other"          # text, escaped by the renderer
    assert by["expect"]["bar"] == 1                                # clamped to 0..1
    assert by["speak"]["state"] == "unbuilt"                       # an unknown state on an unbuilt node
    assert by["meet"]["why_prereqs"] == "it needs its own feel to tell an other's"


@NODE
def test_the_level_falls_back_to_the_earned_count():
    s = dict(SEAT_TREE, header={"name": "Pip"})
    s.pop("level")
    assert _tree(s)["level"] == 2


# ---- the layout ---------------------------------------------------------------------------

def _layout(sheet):
    return _run("out = P.treeLayout(P.skillTree(%s).nodes)" % json.dumps(sheet))


@NODE
def test_lanes_by_branch_and_every_node_below_its_prereqs():
    L = _layout(LADDER)
    assert [l["branch"] for l in L["lanes"]] == ["self", "world", "others"]
    assert [l["label"] for l in L["lanes"]] == ["Self", "World", "Others"]
    pos = {n["id"]: n for n in L["nodes"]}
    lane_x = {l["branch"]: l["x"] for l in L["lanes"]}
    seen = set()
    for n in L["nodes"]:
        assert n["x"] == lane_x[n["branch"]]
        assert (n["x"], n["y"]) not in seen
        seen.add((n["x"], n["y"]))
        for p in n["prereqs"]:
            assert pos[p]["y"] < n["y"]
        assert 0 < n["x"] < L["W"] and 0 < n["y"] < L["H"]
    assert len(L["edges"]) == 6
    e = next(e for e in L["edges"] if e["from"] == "reach" and e["to"] == "shift")
    assert e["d"].startswith("M") and e["earned"] is True       # drawn from an earned prereq


@NODE
def test_unknown_prereqs_and_cycles_do_not_break_the_layout():
    L = _layout(SEAT_TREE)
    assert [l["branch"] for l in L["lanes"]] == ["self", "others"]   # only the lanes it has
    assert {(e["from"], e["to"]) for e in L["edges"]} == {("feel", "meet"), ("meet", "expect"), ("expect", "speak")}
    cyc = [{"id": "a", "name": "a", "branch": "self", "prereqs": ["b"], "state": "locked"},
           {"id": "b", "name": "b", "branch": "mystery", "prereqs": ["a"], "state": "locked"}]
    L = _run("out = P.treeLayout(%s)" % json.dumps(cyc))
    assert sorted(n["id"] for n in L["nodes"]) == ["a", "b"]
    assert [l["branch"] for l in L["lanes"]] == ["self", "mystery"]


# ---- the card, the achievements, the ring --------------------------------------------------

@NODE
def test_the_card_says_the_test_skill_vs_bar_prereqs_and_the_child_as_an_analogy():
    c = _run("const t=P.skillTree(%s); out = P.nodeCard(t.nodes.find(n => n.id === 'expect'), t.nodes)" % json.dumps(SEAT_TREE))
    assert c["title"] == "understand an other"
    rows = dict(c["rows"])
    assert rows["state"].startswith("open")
    assert "waiting on reply checks" in rows["state"]
    assert rows["skill vs bar"] == "skill −0.05 · bar 100%"
    assert rows["needs"] == "<b>meet</b> an other"
    assert rows["why"] == "it reads meet's lines"
    assert rows["a child (an analogy)"] == "a child: predicts what others will do (9 months)"
    e = _run("const t=P.skillTree(%s); out = P.nodeCard(t.nodes.find(n => n.id === 'reach'), t.nodes)" % json.dumps(LADDER))
    rows = dict(e["rows"])
    assert rows["earned"] == "Oct 16 10:00 UTC"
    assert rows["test"] == "test 2"


@NODE
def test_achievements_newest_first():
    a = _run("out = P.achievementsList(P.skillTree(%s).nodes)" % json.dumps(LADDER))
    assert [x["id"] for x in a] == ["kinds", "reach", "feel"]
    assert a[0]["date"] == "Oct 17 10:00 UTC" and a[0]["skill"] == "+0.12"


@NODE
def test_the_progress_ring():
    r = _run("out = [P.ringDash(0, 10), P.ringDash(0.5, 10), P.ringDash(1, 10), P.ringDash(null, 10)]")
    c = r[1]["c"]
    assert r[0]["on"] == 0 and r[2]["on"] == c and abs(r[1]["on"] - c / 2) < 0.01
    assert r[3] is None


# ---- the DOM: no innerHTML, focusable nodes, aria labels ---------------------------------

FAKE_DOM = r"""
class El {
  constructor(tag, ns) { this.tagName = tag; this.ns = ns || null; this.children = []; this.attrs = {}; this._text = "";
                         this.listeners = {}; this.hidden = false; this.className = ""; this.parent = null; }
  set innerHTML(v) { throw new Error("innerHTML used"); }
  get innerHTML() { throw new Error("innerHTML used"); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(k, f) { (this.listeners[k] = this.listeners[k] || []).push(f); }
  focus() { doc.activeElement = this; }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  all() { return [this].concat(...this.children.map((c) => c.all())); }
  fire(k, target) { for (const f of this.listeners[k] || []) f({ target, preventDefault() {} }); }
}
const doc = { activeElement: null, createElement: (t) => new El(t), createElementNS: (ns, t) => new El(t, ns) };
const cls = (e) => (e.className || e.attrs["class"] || "");
const find = (root, c) => root.all().filter((e) => cls(e).split(/\s+/).includes(c));
"""


def _dom(body, sheet=SEAT_TREE):
    return _run(FAKE_DOM + "const root = new El('div'); const sel = {}; P.renderSkillTree(doc, root, %s, sel); %s"
                % (json.dumps(sheet), body))


@NODE
def test_the_tab_renders_with_dom_calls_only():
    out = _dom("out = {text: root.textContent, nodes: find(root, 'sknode').map((b) => ({tag: b.tagName, id: b.attrs['data-id'], "
               "aria: b.attrs['aria-label'], cls: cls(b)})), box: find(root, 'skbox').length, svg: root.all().filter((e) => e.tagName === 'svg').length,"
               "ach: find(root, 'skach').map((e) => e.textContent)}")
    assert "Lv 2" in out["text"] and "Pip" in out["text"]
    assert "<b>meet</b> an other" in out["text"]                       # text, never markup
    assert out["box"] == 1 and out["svg"] >= 1
    nodes = {n["id"]: n for n in out["nodes"]}
    assert all(n["tag"] == "button" for n in out["nodes"])             # keyboard-focusable
    assert "earned" in nodes["meet"]["cls"].split() and "open" in nodes["expect"]["cls"].split()
    assert "unbuilt" in nodes["speak"]["cls"].split()
    assert nodes["expect"]["aria"].startswith("understand an other: open")
    assert "not built yet" in nodes["speak"]["aria"]
    assert out["ach"][0].startswith("<b>meet</b> an other")            # the strip: newest first


@NODE
def test_a_node_focused_or_tapped_shows_its_card_and_keeps_it_across_a_repaint():
    out = _dom("const b = find(root, 'sknode').find((e) => e.attrs['data-id'] === 'expect');"
               "find(root, 'skbox')[0].fire('click', b);"
               "const first = find(root, 'skcard')[0].textContent;"
               "P.renderSkillTree(doc, root, %s, sel);"
               "out = {first, again: find(root, 'skcard')[0].textContent, sel: sel.id}" % json.dumps(SEAT_TREE))
    assert out["sel"] == "expect"
    assert "a child: predicts what others will do (9 months)" in out["first"]
    assert out["again"] == out["first"]


@NODE
def test_the_ladder_fallback_says_where_it_comes_from():
    out = _dom("out = root.textContent", LADDER)
    assert "Lv 3" in out and "the ladder" in out


@NODE
def test_no_sheet_says_so():
    out = _dom("out = root.textContent", None)
    assert "not reachable" in out


# ---- the wiring --------------------------------------------------------------------------

def test_home_has_the_tab_bar_and_wires_the_tree():
    home = (UI / "home.js").read_text()
    assert 'role="tablist"' in home and 'role="tab"' in home and "Skill tree" in home
    assert 'data-tab="tree"' in home and 'data-tab="sheet"' in home
    assert "aria-selected" in home and 'role="tabpanel"' in home
    assert "_skillTree(" in home
    assert "membrane.home.selftab" in home                             # the tab, remembered per browser
    assert ".sktree .skbox" in home and "overflow:auto" in home.split(".sktree .skbox", 1)[1].split("}", 1)[0]
    panel = (UI / "panel.js").read_text()
    assert "_skillTree(root, sheet, sel)" in panel
    # the renderer never writes markup
    body = panel.split("function renderSkillTree", 1)[1].split("\n}\n", 1)[0]
    assert "innerHTML" not in body


def test_the_guide_says_so():
    doc = (ROOT / "docs" / "selfgrowth.md").read_text()
    assert "Skill tree" in doc and "tab" in doc.split("Skill tree", 1)[1][:400]


@NODE
def test_the_ladder_fallback_draws_mirror_and_change_in_the_self_lane_329():
    rows = _run("out = Object.fromEntries(P.TREE_LADDER.map((r) => [r[0], r[2]]))")
    assert rows["mirror"] == "self" and rows["change"] == "self"


@NODE
def test_no_later_lane_locked_skills_sit_in_their_own_lane_353():
    L = _layout(LADDER)
    assert "later" not in [l["branch"] for l in L["lanes"]]
    by = {n["id"]: n for n in L["nodes"]}
    assert by["speak"]["branch"] == "others" and by["invent"]["branch"] == "self"
    assert by["invent"]["state"] == "unbuilt"                     # greyed out, in its lane
