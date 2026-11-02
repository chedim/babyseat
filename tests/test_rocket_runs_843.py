"""#843: rocket view -- finished runs feed the work graph; flights drift back; cap 12 in flight; click opens a run."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _run(est, h, ts, n=1, rc=0, children=()):
    root, res = f"{MEMBRANE_NS}node:sh:{h}", f"{MEMBRANE_NS}result:{h}:{n}"
    est.upsert(QuantumEntity(eid=root, cls="Component", properties={
        "kind": "program", "lang": "sh", "text": "echo hi", "args": list(children)}))
    for c in children:
        est.upsert(QuantumEntity(eid=c, cls="Component", properties={"kind": "program", "args": []}))
    est.upsert(QuantumEntity(eid=res, cls="Component", properties={
        "root": root, "rc": rc, "outcome": "ok", "head": "hi", "tail": "", "text": "echo hi", "ts": ts, "ms": 5}))
    return root, res


def test_finished_run_feed_returns_tree_nodes_and_result_ref():
    est = _est()
    kid = MEMBRANE_NS + "node:sh:kid"
    root, res = _run(est, "aaa", 100.0, children=[kid])
    runs = DM.work_graph(est)["runs"]
    assert [r["id"] for r in runs] == [res]
    r = runs[0]
    assert r["root"] == root and r["rc"] == 0 and r["ts"] == 100.0
    assert {n["id"] for n in r["nodes"]} == {root, kid}
    assert {n["id"]: n["parent"] for n in r["nodes"]}[kid] == root


def test_in_flight_cap_12_drops_oldest():
    est = _est()
    for i in range(13):
        _run(est, f"h{i:02d}", 100.0 + i)
    runs = DM.work_graph(est)["runs"]
    assert len(runs) == 12
    assert runs[0]["ts"] == 112.0 and all(r["ts"] != 100.0 for r in runs), "oldest must be dropped"


DRV = r"""
const fs = require('fs'); const L = require(process.argv[1]);
const src = fs.readFileSync(process.argv[1], 'utf8');
const out = {};
out.hasZ = typeof L.rocketZ === 'function';
if (out.hasZ) out.z = [L.rocketZ({ts: 100}, 100), L.rocketZ({ts: 100}, 110), L.rocketZ({ts: 100}, 130)];
const a = src.indexOf('  _rocketClick('); out.hasClick = a >= 0;
if (a >= 0) {
  const b = src.indexOf('\n  }\n', a); const body = src.slice(a, b).replace(/^[^{]*\{/, '');
  const fn = new Function('id', body); const opened = [];
  const self = { _runs: [{id: 'r1', root: 'n1', rc: 3, text: 'echo hi', head: 'out'}], _workOpen: (x) => opened.push(x) };
  fn.call(self, 'r1'); out.opened = opened;
}
process.stdout.write(JSON.stringify(out));
"""


def _drive():
    return json.loads(subprocess.check_output(["node", "-e", DRV, str(UI / "field3d.js")]))


@NODE
def test_flight_state_advances_backward_over_time():
    z = _drive().get("z")
    assert z and z[0] < z[1] < z[2], "rocketZ must grow (drift back) as time passes"


@NODE
def test_click_resolves_to_work_open_with_code_rc_output():
    o = _drive().get("opened")
    assert o and len(o) == 1
    p = o[0]
    assert p["id"] == "r1" and p["rc"] == 3 and p["code"] == "echo hi" and p["output"] == "out"


def test_node_cap_is_a_named_constant_default_12():
    assert DM.ROCKET_MAX_NODES == 12
    est = _est()
    kids = [MEMBRANE_NS + f"node:sh:k{i}" for i in range(30)]
    _run(est, "big", 100.0, children=kids)
    assert len(DM.work_graph(est)["runs"][0]["nodes"]) == 12


DRV2 = r"""
const L = require(process.argv[1]);
const nodes = [{id:'r',parent:null}]; for (let i=0;i<30;i++) nodes.push({id:'c'+i,parent:i%2?'c'+(i-1):'r'});
const g = L.rocketGraph({nodes: nodes});
const o = L.rocketOrigin({root:'r'}, (id)=> id==='r' ? [5,6,7] : null);
const o2 = L.rocketOrigin({root:'zz'}, ()=>null);
process.stdout.write(JSON.stringify({cap:L.ROCKET_NODE_CAP, speed:L.ROCKET_SPEED, n:g.nodes.length, e:g.edges.length,
  ok: g.edges.every(e=>g.nodes.some(n=>n.id===e.from)&&g.nodes.some(n=>n.id===e.to)), o, o2}));
"""


@NODE
def test_miniature_graph_nodes_edges_capped_and_origin():
    o = json.loads(subprocess.check_output(["node", "-e", DRV2, str(UI / "field3d.js")]))
    assert o["cap"] == 12 and o["speed"] == 0.4
    assert o["n"] == 12 and o["e"] == 11 and o["ok"], "nodes+edges, capped, edges only between kept nodes"
    assert o["o"] == [5, 6, 7], "run starts at its root's position"
    assert o["o2"] == [0, 0, 0], "falls back to the centre"
