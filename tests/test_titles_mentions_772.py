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
