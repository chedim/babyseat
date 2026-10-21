"""#361: the Skill tree tab takes the film's look.

The film's skill-tree scene: earned skills filled translucent gold with a gold
border and a 🏅, locked ones dim with a 🔒, edges solid gold once their
prerequisite is earned and dashed otherwise, and a short light-up when a skill
is newly earned. The states and their meanings stay (earned, awaiting 🎁, open
with its ring, locked, unbuilt); the light-up plays only when a skill turns
earned while the page is open -- the previous render's states against this
one's -- never on a load, and not at all under prefers-reduced-motion.

Pure helpers in membrane/ui/panel.js decide: treeStates (a render's states),
newlyEarned (what turned earned since), edgeClass and nodeClass (the classes
the renderer draws with).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _run(body):
    js = "const P=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(UI / "panel.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _node(id_, state, prereqs=(), branch="self", built=True, accepted=True):
    return {"id": id_, "name": id_, "branch": branch, "prereqs": list(prereqs), "built": built, "state": state,
            "accepted": accepted, "earned_at": "2026-10-18T10:00:00+00:00" if state == "earned" else None,
            "skill": None, "bar": 0.5 if state == "open" else None, "waiting_on": None,
            "child_age": "", "child_ability": "", "why_prereqs": ""}


def _sheet(*nodes):
    return {"header": {"name": "Pip"}, "tree": list(nodes)}


BEFORE = _sheet(_node("feel", "earned"), _node("reach", "open", ["feel"], "world"),
                _node("kinds", "locked", ["reach"], "world"), _node("meet", "locked", ["kinds"], "others"),
                _node("speak", "weird", ["meet"], "others", built=False))


# ---- the newly-earned light-up: a pure comparison of two renders -------------------------

@NODE
def test_tree_states_maps_each_node_to_its_state():
    s = _run("out = P.treeStates(P.skillTree(%s).nodes)" % json.dumps(BEFORE))
    assert s == {"feel": "earned", "reach": "open", "kinds": "locked", "meet": "locked", "speak": "unbuilt"}


@NODE
def test_newly_earned_is_what_turned_earned_since_the_last_render():
    prev = {"feel": "earned", "reach": "open", "kinds": "locked", "meet": "locked", "gift": "open", "speak": "unbuilt"}
    now = [_node("feel", "earned"), _node("reach", "earned"), _node("kinds", "open"), _node("meet", "locked"),
           _node("gift", "awaiting"), _node("speak", "unbuilt"), _node("fresh", "earned")]
    out = _run("out = P.newlyEarned(%s, %s)" % (json.dumps(prev), json.dumps(now)))
    # reach turned earned, gift turned earned-awaiting-accept; feel was earned already, kinds only opened,
    # and a node the last render never saw is no change it saw
    assert out == ["reach", "gift"]


@NODE
def test_no_previous_render_is_no_light_up():
    now = [_node("feel", "earned"), _node("reach", "earned")]
    assert _run("out = P.newlyEarned(null, %s)" % json.dumps(now)) == []
    assert _run("out = P.newlyEarned(undefined, %s)" % json.dumps(now)) == []


@NODE
def test_accepting_an_earned_skill_is_not_a_new_light_up():
    out = _run("out = P.newlyEarned({feel: 'awaiting'}, %s)" % json.dumps([_node("feel", "earned")]))
    assert out == []


# ---- the edges: solid gold once the prerequisite is earned, dashed otherwise --------------

@NODE
def test_edge_class_per_prerequisite_state():
    L = _run("out = P.treeLayout(P.skillTree(%s).nodes)" % json.dumps(BEFORE))
    by = {(e["from"], e["to"]): e for e in L["edges"]}
    assert by[("feel", "reach")]["fromState"] == "earned"
    assert by[("reach", "kinds")]["fromState"] == "open"
    cls = _run("const L = P.treeLayout(P.skillTree(%s).nodes); out = L.edges.map((e) => [e.from, P.edgeClass(e)])"
               % json.dumps(BEFORE))
    cls = dict(cls)
    assert cls["feel"] == "skedge earned"          # solid gold
    assert cls["reach"] == "skedge"                # dashed
    assert cls["kinds"] == "skedge" and cls["meet"] == "skedge"
    # an earned prerequisite still awaiting your accept: gold, but dashed (nothing it leads to runs yet)
    assert _run("out = P.edgeClass({from: 'a', to: 'b', earned: false, fromState: 'awaiting'})") == "skedge awaiting"
    # the edge out of a newly earned skill lights up with it
    assert _run("out = P.edgeClass({from: 'a', to: 'b', earned: true, fromState: 'earned'}, ['a'])") == "skedge earned lit"


# ---- the nodes: one class per state, plus the chosen and the newly earned ----------------

@NODE
def test_node_class_per_state():
    out = _run("out = ['earned','awaiting','open','locked','unbuilt'].map((s) => P.nodeClass({id: 'x', state: s}))")
    assert out == ["sknode earned", "sknode awaiting", "sknode open", "sknode locked", "sknode unbuilt"]
    assert _run("out = P.nodeClass({id: 'x', state: 'earned'}, {sel: 'x', lit: ['x']})") == "sknode earned sel lit"
    assert _run("out = P.nodeClass({id: 'x', state: 'locked'}, {sel: 'y', lit: ['y']})") == "sknode locked"


# ---- the DOM: icons per state, the light-up only on a change seen while open ---------------

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
const lit = (root) => find(root, 'lit').map((e) => e.attrs['data-id'] || ('edge:' + e.attrs['d']));
const nodes = (root) => Object.fromEntries(find(root, 'sknode').map((b) => [b.attrs['data-id'],
  {cls: cls(b), icon: find(b, 'skic')[0].textContent, ring: find(b, 'skring').length}]));
"""

AFTER = _sheet(_node("feel", "earned"), _node("reach", "earned", ["feel"], "world"),
               _node("kinds", "open", ["reach"], "world"), _node("meet", "locked", ["kinds"], "others"),
               _node("speak", "weird", ["meet"], "others", built=False))


@NODE
def test_each_state_draws_its_look():
    out = _run(FAKE_DOM + "const root = new El('div'); P.renderSkillTree(doc, root, %s, {}); out = nodes(root)"
               % json.dumps(_sheet(*BEFORE["tree"], _node("gift", "earned", accepted=False))))
    assert out["feel"]["icon"] == "🏅" and out["feel"]["cls"].split()[:2] == ["sknode", "earned"]
    assert out["gift"]["icon"] == "🎁" and "awaiting" in out["gift"]["cls"].split()
    assert out["reach"]["ring"] == 1 and "open" in out["reach"]["cls"].split()
    assert out["kinds"]["icon"] == "🔒" and "locked" in out["kinds"]["cls"].split()
    assert "unbuilt" in out["speak"]["cls"].split()


@NODE
def test_the_light_up_plays_only_on_a_change_seen_while_open():
    out = _run(FAKE_DOM + "const root = new El('div'); const sel = {};"
               "P.renderSkillTree(doc, root, %s, sel); const first = lit(root);"
               "P.renderSkillTree(doc, root, %s, sel); const second = lit(root);"
               "const edges = find(root, 'skedge').filter((e) => cls(e).split(/\\s+/).includes('lit')).length;"
               "P.renderSkillTree(doc, root, %s, sel); const third = lit(root);"
               "out = {first, second, edges, third}" % (json.dumps(BEFORE), json.dumps(AFTER), json.dumps(AFTER)))
    assert out["first"] == []                         # a load lights nothing
    assert "reach" in out["second"]                   # reach turned earned while open
    assert [x for x in out["second"] if not x.startswith("edge:")] == ["reach"]
    assert out["edges"] == 1                          # reach -> kinds lights with it
    assert out["third"] == []                         # the next render: still earned, no new light-up


@NODE
def test_picking_a_node_keeps_its_light_up_class():
    out = _run(FAKE_DOM + "const root = new El('div'); const sel = {};"
               "P.renderSkillTree(doc, root, %s, sel); P.renderSkillTree(doc, root, %s, sel);"
               "const b = find(root, 'sknode').find((e) => e.attrs['data-id'] === 'kinds');"
               "find(root, 'skbox')[0].fire('click', b);"
               "out = nodes(root)" % (json.dumps(BEFORE), json.dumps(AFTER)))
    assert "lit" in out["reach"]["cls"].split()
    assert "sel" in out["kinds"]["cls"].split()


# ---- the styles: the film's gold from the page's theme, no motion when reduced -------------

def _css():
    return (UI / "home.js").read_text()


def test_the_gold_follows_the_theme():
    home = _css()
    decl = re.search(r"\.sktree \{ --sk-earned:([^;]+);", home)
    assert decl, "the tree's gold is one variable"
    assert "var(--primary-text-color" in decl.group(1)        # leans dark on a light theme, bright on a dark one


def test_the_light_up_is_motion_only_and_reduced_motion_has_none():
    home = _css()
    assert ".sktree .sknode.lit" in home and "@keyframes skearn" in home
    blocks = home.split("@media (prefers-reduced-motion: no-preference)")
    inside = "".join(b.split("\n  }\n", 1)[0] for b in blocks[1:])
    assert "skearn" in inside
    outside = blocks[0] + "".join(b.split("\n  }\n", 1)[1] if "\n  }\n" in b else "" for b in blocks[1:])
    assert "animation:skearn" not in outside.replace(" ", "")


def test_locked_is_dim_and_earned_is_translucent_gold():
    home = _css()
    earned = home.split(".sktree .sknode.earned {", 1)[1].split("}", 1)[0]
    assert "var(--sk-earned)" in earned and "color-mix" in earned
    locked = home.split(".sktree .sknode.locked {", 1)[1].split("}", 1)[0]
    assert "opacity" in locked


def test_the_guide_says_so():
    doc = (ROOT / "docs" / "selfgrowth.md").read_text()
    sec = doc.split("### The Skill tree tab", 1)[1].split("\n### ", 1)[0]
    assert "#361" in sec and "lights up" in sec and "reduced motion" in sec
