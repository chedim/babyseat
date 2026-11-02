"""The field view lives IN the field3d scene: work items sit at their entity field positions and follow
them; the real chat stays visible under the field view (no separate overlay / ring layout / 'ask…' box)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
G = {"nodes": [{"id": "g1", "kind": "goal", "title": "ship", "status": "open"},
               {"id": "t1", "kind": "task", "title": "build", "status": "open"},
               {"id": "d1", "kind": "decision", "title": "which?", "status": "open"}], "edges": []}


def _f3(expr):
    js = "const F=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "field3d.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_work_items_are_placed_at_entity_positions_and_follow_moves():
    pos = {"g1": [0.1, 0.2, 0.3], "t1": [-0.5, 0.4, 0.0], "d1": [0.7, -0.7, 0.2]}
    a = _f3("F.workMarkers(%s, %s)" % (json.dumps(G), json.dumps(pos)))
    assert {m["id"]: m["p"] for m in a} == pos
    assert {m["id"]: m["kind"] for m in a} == {"g1": "goal", "t1": "task", "d1": "decision"}
    pos["t1"] = [0.9, 0.9, 0.9]
    b = _f3("F.workMarkers(%s, %s)" % (json.dumps(G), json.dumps(pos)))
    assert {m["id"]: m["p"] for m in b}["t1"] == [0.9, 0.9, 0.9]


def test_no_fixed_ring_layout_or_dom_overlay():
    fv = (UI / "fieldview.js").read_text()
    assert "RING" not in fv and "function layout" not in fv, "fixed ring layout still drives the icons"
    assert "attachShadow" not in fv, "separate DOM overlay still draws the items"


def test_chat_stays_visible_under_field_view():
    css = (UI / "home.js").read_text()
    hidden = re.findall(r"\.view-field[^{}]*\{[^}]*display:\s*none", css)
    assert not any(re.search(r"\.view-field\s+\.(chat|msg)\b", h) for h in hidden), "field view hides .chat/.msg"
    assert 'placeholder="ask…"' not in (UI / "fieldview.js").read_text(), "plain ask… input replaces the chat"


@NODE
def test_finished_goals_and_tasks_leave_the_field_without_reload():
    pos = {"g1": [0, 0, 0], "t1": [1, 1, 1], "d1": [2, 2, 2], "g2": [3, 3, 3]}
    for st in ("fulfilled", "done", "dropped"):
        g = {"nodes": [{"id": "g1", "kind": "goal", "status": st}, {"id": "t1", "kind": "task", "status": st},
                       {"id": "d1", "kind": "decision", "status": "open"},
                       {"id": "g2", "kind": "goal", "status": "open"}], "edges": []}
        ids = [m["id"] for m in _f3("F.workMarkers(%s, %s)" % (json.dumps(g), json.dumps(pos)))]
        assert ids == ["d1", "g2"], (st, ids)
    src = (UI / "field3d.js").read_text()
    a = src.index("setWork(graph, onOpen)")
    body = src[a:src.index("_stepWork() {", a)]
    assert "workMarkers(" in body and "el.remove()" in body, "setWork must drop elements of finished nodes"


def test_normal_chat_input_history_and_live_replies_untouched():
    # the real chat is panel.js (input bar, history on reload, live replies); the field view must not replace or edit it
    panel = (UI / "panel.js").read_text()
    assert re.search(r"/message|prompt_async", panel), "chat send path missing"
    assert re.search(r"history|/message\b", panel), "chat history reload path missing"
    assert "placeholder" in panel and "<input" in panel or "textarea" in panel, "chat input bar missing"
    fv = (UI / "fieldview.js").read_text()
    assert 'placeholder="ask…"' not in fv and "panel" not in fv.split("customElements")[0].lower().replace("panel-", ""), \
        "field view must not reach into the chat panel"
