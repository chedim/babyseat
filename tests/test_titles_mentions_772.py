"""#772: titles everywhere (title beats name beats id) and clickable entity mentions in the field view."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def test_title_property_beats_name_in_nodes_and_waiting_on():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    G, T1, T2 = (MEMBRANE_NS + x for x in ("g", "task:t1", "task:t2"))
    est.upsert(QuantumEntity(eid=G, cls="Goal", properties={"name": "raw goal name", "title": "Goal title", "status": "open"}))
    est.upsert(QuantumEntity(eid=T1, cls="Task", properties={"name": "raw1", "title": "Task one", "status": "stuck", "goal": G, "blocks": [T2]}))
    est.upsert(QuantumEntity(eid=T2, cls="Task", properties={"name": "raw2", "title": "Task two", "status": "open", "goal": G, "blocks": []}))
    g = DM.work_graph(est)
    by = {n["id"]: n for n in g["nodes"]}
    assert by[G]["title"] == "Goal title" and by[T1]["title"] == "Task one"
    assert [w["title"] for w in by[G]["waiting_on"]] == ["Task one"]
    assert [w["title"] for w in by[T2]["waiting_on"]] == ["Task one"]


def _js(body):
    js = 'const F=require(%r);\n%s' % (str(ROOT / "membrane" / "ui" / "fieldview.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


GRAPH = ('{nodes:[{id:"membrane::task:a:k1",kind:"task",title:"Find the issue",status:"done",waiting_on:[]},'
         '{id:"membrane::goal:g",kind:"goal",title:"Ship it",status:"open",waiting_on:[]}],edges:[]}')


@NODE
def test_mentions_become_clickable_titles():
    out = _js('const h=F.mentionHtml(%s,"see membrane::task:a:k1 and membrane::task:zz <x>");console.log(JSON.stringify(h))' % GRAPH)
    assert 'data-eid="membrane::task:a:k1"' in out and ">Find the issue</a>" in out
    assert "membrane::task:a:k1</a>" not in out
    assert "membrane::task:zz" in out and "&lt;x&gt;" in out      # unknown ids stay as text, escaped


@NODE
def test_window_text_chat_and_logs_use_mentions():
    out = _js('const w=F.windowInfo(%s,"membrane::goal:g",{properties:{summary:"blocked by membrane::task:a:k1"},changes:[{seq:1,stream:"s",text:"ran membrane::task:a:k1"}]},{messages:[{who:"me",text:"ping membrane::task:a:k1"}]});console.log(JSON.stringify([w.textHtml,w.chatHtml,w.logHtml]))' % GRAPH)
    s = json.dumps(out)
    assert s.count("Find the issue") >= 3 and 'data-eid' in s


@NODE
def test_card_info_titles_never_fall_back_to_id_when_known():
    out = _js('const g=%s;g.edges=[{from:"membrane::goal:g",to:"membrane::task:a:k1",rel:"blocks"}];console.log(JSON.stringify(F.cardInfo(g,"membrane::goal:g")))' % GRAPH)
    assert out["blocks"] == ["Find the issue"]


def test_3d_label_prefers_title_over_surface_and_eid():
    import numpy as np
    from membrane import field3d as F
    rng = np.random.default_rng(0)
    rows = [{"eid": f"membrane::task:x{i}", "kind": "s", "surface": "" if i == 0 else f"s{i}", "accr": 1,
             "conflicts": [], "recent": [], "state": rng.normal(size=8).tolist()} for i in range(6)]
    titles = {"membrane::task:x0": "Audit the labels", "membrane::task:x1": "Second title"}
    p = F.build(lambda: iter(rows), lambda s: s, "", ref_vec=lambda e: None, titles=titles.get)
    lab = {p.ids[i]: p.labels[i] for i in range(len(p.ids))}
    assert lab["membrane::task:x0"] == "Audit the labels" and lab["membrane::task:x1"] == "Second title"
    assert lab["membrane::task:x2"] == "s2"


REFG = ('{nodes:[{id:"membrane::task:a",kind:"task",title:"Task <A>",status:"open",waiting_on:[{id:"membrane::task:b",title:"Task B & co",reason:"stuck"},{id:"membrane::task:a",title:"Task <A>",reason:"self"}]},'
        '{id:"membrane::task:b",kind:"task",title:"Task B & co",status:"stuck",waiting_on:[]},'
        '{id:"membrane::task:c",kind:"task",title:"Task C",status:"open",waiting_on:[]}],'
        'edges:[{from:"membrane::task:a",to:"membrane::task:b",rel:"blocks"},{from:"membrane::task:a",to:"membrane::task:a",rel:"blocks"},{from:"membrane::task:c",to:"membrane::task:a",rel:"blocks"}]}')


@NODE
def test_blocks_blocked_by_waiting_on_render_as_links():
    out = _js('console.log(JSON.stringify(F.relHtml(%s,"membrane::task:a")))' % REFG)
    for k in ("blocks", "blockedBy", "waitingOn"):
        assert 'class="ment"' in out[k]
    assert 'data-eid="membrane::task:b"' in out["blocks"] and ">Task B &amp; co</a>" in out["blocks"]
    assert 'data-eid="membrane::task:c"' in out["blockedBy"] and ">Task C</a>" in out["blockedBy"]
    assert 'data-eid="membrane::task:b"' in out["waitingOn"] and "stuck" in out["waitingOn"]
    assert "membrane::task:b</a>" not in out["blocks"]


@NODE
def test_selected_entity_is_plain_title_not_a_link():
    out = _js('console.log(JSON.stringify(F.relHtml(%s,"membrane::task:a")))' % REFG)
    assert 'data-eid="membrane::task:a"' not in json.dumps(out)
    assert "Task &lt;A&gt;" in out["blocks"] and "Task &lt;A&gt;" in out["waitingOn"]


@NODE
def test_rel_titles_are_escaped():
    out = _js('console.log(JSON.stringify(F.relHtml(%s,"membrane::task:a")))' % REFG)
    s = json.dumps(out)
    assert "<A>" not in s and "B & co" not in s
    empty = _js('console.log(JSON.stringify(F.relHtml(%s,"membrane::task:c")))' % REFG)
    assert empty["waitingOn"] == ""


@NODE
def test_selected_entity_is_plain_in_text_chat_and_logs():
    out = _js('const g=%s;const h={properties:{summary:"x membrane::task:a:k1"}};'
              'console.log(JSON.stringify([F.mentionHtml(g,"membrane::task:a:k1 and membrane::goal:g","membrane::task:a:k1"),'
              'F.windowInfo(g,"membrane::task:a:k1",h,[]).textHtml]))' % GRAPH)
    assert 'data-eid="membrane::task:a:k1"' not in json.dumps(out)
    assert "Find the issue" in out[0] and "Find the issue" in out[1]
    other = _js('console.log(JSON.stringify(F.mentionHtml(%s,"membrane::task:a:k1","membrane::goal:g")))' % GRAPH)
    assert 'data-eid="membrane::task:a:k1"' in other


@NODE
def test_goal_in_info_is_a_link():
    g = ('{nodes:[{id:"t",kind:"task",title:"T",status:"open",waiting_on:[]},{id:"g",kind:"goal",title:"The <goal>",status:"open"}],'
         'edges:[{from:"t",to:"g",rel:"of"}]}')
    out = _js('console.log(JSON.stringify(F.relHtml(%s,"t")))' % g)
    assert 'data-eid="g"' in out["goal"] and "The &lt;goal&gt;" in out["goal"]
    assert _js('console.log(JSON.stringify(F.relHtml(%s,"g")))' % g)["goal"] == ""


@NODE
def test_mention_click_selects_the_entity_in_the_scene():
    out = _js('''
let Cls; global.HTMLElement = class {}; global.customElements = {get: () => null, define: (n, c) => { Cls = c; }};
delete require.cache[require.resolve(%r)];
const F2 = require(%r);
const v = new Cls(); const calls = [];
v.open = (id) => calls.push(["open", id]);
v._scene = {select: (id) => calls.push(["select", id]), api: {onSelect: (id) => calls.push(["onSelect", id])}};
v.querySelector = () => null; v.innerHTML = ""; v.style = {};
v._graph = {nodes: [], edges: []}; v._open = null;
v._draw();
v.onclick({target: {closest: (s) => s === ".ment" ? {getAttribute: () => "membrane::task:z"} : null}, preventDefault() {}});
console.log(JSON.stringify(calls));''' % ((str(ROOT / "membrane" / "ui" / "fieldview.js"),) * 2))
    # home's onSelect is _select(node): it needs {id, work:true}, a bare string would close the card it opened
    assert out == [["open", "membrane::task:z"], ["select", "membrane::task:z"],
                   ["onSelect", {"id": "membrane::task:z", "work": True}]]


def test_3d_title_resolves_field_mirror_ids():
    from membrane.lanes import _field3d_title, _field3d_title_index
    from types import SimpleNamespace as NS
    docs = {"membrane::Goal:g1": NS(cls="Goal", properties={"title": "Ship it"}),
            "membrane::task:t1": NS(cls="Task", properties={"title": "Draft"}),
            "membrane::Note:n": NS(cls="Note", properties={"title": "no"})}
    idx = _field3d_title_index(docs)
    assert _field3d_title(idx, "m:goal:g1") == "Ship it"      # mirror id, case differs from the store eid
    assert _field3d_title(idx, "m:task:t1") == "Draft"
    assert _field3d_title(idx, "membrane::Goal:g1") == "Ship it"
    assert _field3d_title(idx, "m:note:n") == "" and _field3d_title(idx, "m:task:none") == ""


def test_3d_title_index_is_a_snapshot_not_a_live_scan():
    from membrane.lanes import _field3d_title, _field3d_title_index
    from types import SimpleNamespace as NS

    class Racy(dict):                    # a store other threads grow while it is iterated
        def items(self):
            it = super().items()
            self["membrane::Task:late"] = NS(cls="Task", properties={"title": "L"})
            return it
    docs = Racy({"membrane::Goal:g1": NS(cls="Goal", properties={"title": "Ship it"})})
    idx = _field3d_title_index(docs)     # must not raise "changed size during iteration"
    assert _field3d_title(idx, "m:goal:g1") == "Ship it"
    assert _field3d_title(idx, "m:nothing") == ""
