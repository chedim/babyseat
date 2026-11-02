"""k6: ONE field view -- icons for goals, tasks, decisions drawn only from GET /membrane/work-graph;
hover grows an icon, click opens a card. It replaces Everything and the Decisions cockpit (default)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

G = {"nodes": [
    {"id": "g1", "kind": "goal", "title": "ship", "status": "open", "goal": None},
    {"id": "t1", "kind": "task", "title": "build", "status": "open", "goal": "g1"},
    {"id": "d1", "kind": "decision", "title": "which?", "status": "open", "goal": "g1"}],
    "edges": [{"from": "t1", "to": "g1", "rel": "of"}, {"from": "d1", "to": "g1", "rel": "of"}, {"from": "d1", "to": "t1", "rel": "blocks"}]}


def _js(expr, f="fieldview.js", name="F"):
    js = "const %s=require(%r); process.stdout.write(JSON.stringify(%s))" % (name, str(UI / f), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_icons_by_kind_and_positions():
    icons = _js("F.layout(%s, 800, 600)" % json.dumps(G))
    assert {i["id"]: i["kind"] for i in icons} == {"g1": "goal", "t1": "task", "d1": "decision"}
    assert len({i["glyph"] for i in icons}) == 3
    assert all(0 <= i["x"] <= 800 and 0 <= i["y"] <= 600 for i in icons)
    assert len({(i["x"], i["y"]) for i in icons}) == 3


@NODE
def test_hover_grows_and_card_has_info():
    assert _js("F.scale(true)") > _js("F.scale(false)")
    c = _js("F.cardInfo(%s, 'd1')" % json.dumps(G))
    assert c["title"] == "which?" and c["kind"] == "decision" and c["status"] == "open"
    assert c["of"] == "ship" and c["blocks"] == ["build"]
    assert _js("F.cardInfo(%s, 't1')" % json.dumps(G))["blockedBy"] == ["which?"]


@NODE
def test_field_replaces_everything_and_decisions():
    H = lambda e: _js(e, "home.js", "H")
    for raw in (None, "", "bogus", "all", "decisions", "field"):
        assert H("H.knownView(%s)" % json.dumps(raw)) == "field", raw
    assert H('H.knownView("chat")') == "chat" and H('H.knownView("graph")') == "graph"
    rows = H('H.standaloneMenu("field")')
    assert [r["v"] for r in rows] == ["chat", "graph", "field"]
    assert [r["v"] for r in rows if r["current"]] == ["field"]


def test_the_view_reads_only_work_graph():
    src = (UI / "fieldview.js").read_text()
    assert "/membrane/work-graph" in src
    for other in ("/membrane/decisions", "/membrane/decision/"):
        assert other not in src
