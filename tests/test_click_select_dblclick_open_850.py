"""#850: in the field view one click only SELECTS an item (work marker or field entity), a
double-click OPENS it, a click on the background clears the selection."""
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
const fn = new Function('document', 'workMarkers', 'WORK_GLYPH', body);
const els = []; const doc = { createElement: () => { const h = {}; const el = { style: {}, h, remove() {},
  addEventListener(t, f) { h[t] = f; } }; els.push(el); return el; } };
const log = { open: [], select: [], onSelect: [] };
const self = { _work: %s, _workEls: new Map(), _workPos: { t1: [0, 0, 0] }, slots: new Map(),
  labelLayer: { appendChild() {} }, _project: () => [1, 2], _stepWorkEdges() {},
  _workOpen: (id) => log.open.push(id), select: (id) => log.select.push(id),
  api: { onSelect: (m) => log.onSelect.push(m && m.id) } };
fn.call(self, doc, L.workMarkers, L.WORK_GLYPH);
const el = els[0];
if (el.onclick) el.onclick({}); if (el.h.click) el.h.click({});
const afterClick = JSON.parse(JSON.stringify(log));
if (el.h.dblclick) el.h.dblclick({});
process.stdout.write(JSON.stringify({ afterClick, afterDbl: log, hasDbl: !!el.h.dblclick }));
"""


def _drive():
    out = subprocess.check_output(["node", "-e", DRIVER % json.dumps(G), str(UI / "field3d.js")])
    return json.loads(out)


@NODE
def test_work_marker_single_click_only_selects():
    r = _drive()["afterClick"]
    assert r["open"] == [], "single click must not open the work marker"
    assert "t1" in r["select"] or "t1" in r["onSelect"], "single click must select it"


@NODE
def test_work_marker_dblclick_opens():
    r = _drive()
    assert r["hasDbl"] and r["afterDbl"]["open"] == ["t1"]


def test_field_entity_single_click_does_not_open_drawer():
    src = (UI / "field.js").read_text()
    m = re.search(r"onSelect:\s*\(m\)\s*=>\s*\{(.*?)\},\n", src, re.S)
    assert m, "onSelect handler not found"
    assert "_select3d" not in m.group(1), "onSelect (single click) must only select; opening belongs to dblclick"
    assert re.search(r"onOpen:", src), "field.js must open the drawer from a dblclick (api.onOpen)"


def test_canvas_dblclick_opens_and_background_click_clears():
    src = (UI / "field3d.js").read_text()
    assert re.search(r'addEventListener\("dblclick".*api\.onOpen', src), "canvas dblclick must call api.onOpen"
    assert "this.select(null); this.api.onSelect(null)" in src  # background click clears (kept)
