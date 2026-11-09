"""#960 / PR 961 review: the field view's own filter keeps watch nodes, the 👁 icon sits at the watched
entity (node id "<eid>#watch<i>" is no entity), the watches edge is drawn although the watched entity is no
work-graph node, and only the assistant / a goal worker own the drawn watches."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_work_graph_watches_960 import _op, _store  # noqa: E402
from membrane import decision_map as DM  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node missing")


def _f3(expr):
    js = "const F=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "field3d.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


GRAPH = {"nodes": [{"id": "E1#watch0", "kind": "watch", "title": "Memo watches ci", "status": "open",
                    "watching": "E1", "owner": "Memo"}],
         "edges": [{"from": "E1#watch0", "to": "E1", "rel": "watches"}]}


@NODE
def test_workshows_keeps_watch_nodes():
    assert [n["id"] for n in _f3("F.workShows(%s)" % json.dumps(GRAPH))] == ["E1#watch0"]


@NODE
def test_watch_icon_is_placed_at_the_watched_entity():
    pos = {"E1": [1, 2, 3]}                      # only the watched entity has a position
    ms = _f3("F.workMarkers(%s, %s)" % (json.dumps(GRAPH), json.dumps(pos)))
    assert [(m["id"], m["kind"], m["p"]) for m in ms] == [("E1#watch0", "watch", [1, 2, 3])]
    assert _f3("F.WORK_GLYPH.watch") == "👁"


@NODE
def test_watches_edge_drawn_when_watched_entity_is_not_a_work_node():
    pos = {"E1": [1, 2, 3]}
    es = _f3("F.workEdges(%s, %s, {}, null)" % (json.dumps(GRAPH), json.dumps(pos)))
    assert len(es) == 1 and es[0]["from"] == "E1#watch0" and es[0]["to"] == "E1"


def test_field3d_places_a_watch_by_its_watched_eid():
    src = (UI / "field3d.js").read_text()
    assert 'n.kind === "watch" && n.watching' in src and "this.placeOf(at)" in src


def test_only_assistant_or_goal_worker_watches_are_drawn():
    est, G, T, X = _store()
    from membrane.entities import QuantumEntity
    est.upsert(QuantumEntity(eid=X, cls="Component", properties={"name": "ci", "trigger": T, "watch_ops": [
        _op("Memo"), _op("watch:abc"), _op("operator"), _op("dmitrii"), _op("Blurp"), _op("membrane::self"), _op("")]}))
    owners = sorted(n["owner"] for n in DM.work_graph(est)["nodes"] if n["kind"] == "watch")
    assert owners == ["Memo", "watch:abc"]
