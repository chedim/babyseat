"""#843: the 3-D view's rocket flights drift back; click opens a run (the page code; the feed is gone from the Python work graph)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


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


DRV3 = r"""
const fs = require('fs'); const src = fs.readFileSync(process.argv[1], 'utf8');
function body(name) { let a = src.indexOf('\n  ' + name + '('); if (a < 0) a = src.indexOf('\n  async ' + name + '(');
  const b = src.indexOf('\n  }\n', a + 1); return src.slice(a + 1, b).replace(/^[^{]*\{/, ''); }
const out = {};
const sw = new Function('graph', 'onOpen', 'workShows', 'workMarkers', body('setWork'));
const SW = (self, graph) => sw.call(self, graph, null, () => [], () => []);
const mk = (on) => ({ rocketRuns: on, locateCache: new Map(), placeOf: async () => null });
let s = mk(false); SW(s, {nodes: [], runs: [{id: 'r'}]}); out.off = s._runs.length;
s = mk(true); SW(s, {nodes: [], runs: [{id: 'r'}]}); out.on = s._runs.length;
s = mk(false); SW(s, {nodes: [], runs: [{id: 'r'}], rocket: true}); out.srv = s._runs.length;
const st = new Function(body('_stepRocket'));
s = {rocketRuns: false, _runs: [{id: 'r'}], slots: new Map()}; st.call(s); out.stepOff = !s._rocketEls || s._rocketEls.size === 0;
const AF = Object.getPrototypeOf(async function(){}).constructor;
const loc = new AF('eid', 'locateCandidates', 'LOCATE_CACHE_MAX', 'cubeToWorld', body('_locate'));
let err = ' 410 ';
const lv = {locateCache: new Map(), pid: 'p', api: {get: async () => { if (err) throw new Error(err); return {p: [1,2,3]}; }}};
const lc = (e) => [e];
(async () => {
  out.first = await loc.call(lv, 'e1', lc, 500); out.cached410 = lv.locateCache.has('e1');
  err = null; out.second = await loc.call(lv, 'e1', lc, 500);
  err = ' 404 '; await loc.call(lv, 'e2', lc, 500); out.cached404 = lv.locateCache.has('e2');
  process.stdout.write(JSON.stringify(out));
})();
"""


@NODE
def test_switch_and_locate_410_behaviour():
    o = json.loads(subprocess.check_output(["node", "-e", DRV3, str(UI / "field3d.js")]))
    assert o["off"] == 0 and o["on"] == 1 and o["srv"] == 1
    assert o["stepOff"] is True
    assert o["cached410"] is False, "410 must not cache null"
    assert o["second"] == {"p": [1, 2, 3]}, "after a 410 the next locate retries and finds it"
    assert o["cached404"] is True
