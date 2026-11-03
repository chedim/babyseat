"""#855 point 1: one click selects and opens the panel (drawer for entities, goal window for work markers);
another item swaps it; unselect closes it and gives the chat back; a click on the panel does not unselect."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
G = {"nodes": [{"id": "t1", "kind": "task", "title": "build", "status": "open", "goal": None}], "edges": []}

DRIVER = r"""
const fs = require('fs'); const L = require(process.argv[1]);
const src = fs.readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('  _stepWork() {'), b = src.indexOf('  _stepWorkEdges() {');
const body = src.slice(a, b).trim().replace(/^_stepWork\(\) \{/, '').replace(/\}\s*$/, '');
const fn = new Function('document', 'workMarkers', 'WORK_GLYPH', 'markerZ', 'MARKER_Z_HOVER', body);
const els = []; const doc = { createElement: () => { const h = {}; const el = { style: {}, h, remove() {},
  addEventListener(t, f) { h[t] = f; } }; els.push(el); return el; } };
const log = { open: [], select: [], onSelect: [] };
const self = { _work: %s, _workEls: new Map(), _workPos: { t1: [0, 0, 0] }, slots: new Map(),
  labelLayer: { appendChild() {} }, _project: () => [1, 2], _stepWorkEdges() {},
  _workOpen: (id) => log.open.push(id), select: (id) => log.select.push(id),
  api: { onSelect: (m) => log.onSelect.push(m && m.id) } };
fn.call(self, doc, L.workMarkers, L.WORK_GLYPH, L.markerZ, L.MARKER_Z_HOVER);
els[0].h.click({});
process.stdout.write(JSON.stringify({ log, hasDbl: !!els[0].h.dblclick }));
"""


@NODE
def test_work_marker_one_click_selects_and_opens_goal_window():
    r = json.loads(subprocess.check_output(["node", "-e", DRIVER % json.dumps(G), str(UI / "field3d.js")]))
    assert r["log"]["select"] == ["t1"] and r["log"]["open"] == ["t1"] and not r["hasDbl"]


def test_entity_select_opens_drawer_and_unselect_closes_it():
    src = (UI / "field.js").read_text()
    m = re.search(r"onSelect:\s*\(m\)\s*=>\s*\{(.*?)\},\n", src, re.S)
    assert m and "_select3d(m.id)" in m.group(1)          # one click opens the drawer (swap = same call again)
    assert 'classList.remove("open")' in m.group(1)       # nothing selected closes it
    assert "onOpen" not in src


def test_unselect_closes_goal_window_and_gives_chat_back():
    h = (UI / "home.js").read_text()
    sel = h[h.index("    _select(m) {"):h.index("    _showHits()")]
    assert 'toggle("covered", !!m)' in sel                # selection covers the chat, none uncovers it
    assert "this._cockpit.open(null)" in sel              # entity select / unselect closes the goal window
    assert "membrane-chat" not in sel and "innerHTML" not in sel   # the chat itself is never rebuilt: scroll and draft stay
    assert ".chat.covered" in h
    fv = (UI / "fieldview.js").read_text()
    assert "sc.select(null)" in fv                        # the window's own close button unselects


def test_click_on_panel_does_not_unselect():
    # unselect lives only in the canvas pointerup; the panels are siblings of the canvas, not its children
    src = (UI / "field3d.js").read_text()
    assert src.count("this.api.onSelect(null)") == 1
