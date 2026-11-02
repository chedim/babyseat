"""#850 (Dima's addition): selecting fades what is not in the item's goal graph and frames
that graph (item centred, whole graph in view). Pure functions, driven through node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
G = {"nodes": [{"id": "g", "kind": "goal"}, {"id": "a", "kind": "task", "goal": "g"},
               {"id": "b", "kind": "task", "goal": "g"}, {"id": "z", "kind": "task", "goal": "h"},
               {"id": "h", "kind": "goal"}], "edges": [{"from": "g", "to": "a"}]}


def js(expr):
    code = "const L=require(process.argv[1]);process.stdout.write(JSON.stringify(%s))" % expr
    return json.loads(subprocess.check_output(["node", "-e", code, str(UI / "field3d.js")]))


@NODE
def test_scope_of_work_item_is_its_goal_graph():
    assert sorted(js("L.selectionScope(%s,'a',[])" % json.dumps(G))) == ["a", "b", "g"]
    assert sorted(js("L.selectionScope(%s,'g',[])" % json.dumps(G))) == ["a", "b", "g"]


@NODE
def test_scope_without_goal_graph_uses_relations():
    assert sorted(js("L.selectionScope(%s,'e1',['e2','e3'])" % json.dumps(G))) == ["e1", "e2", "e3"]


@NODE
def test_scope_none_selected_is_null():
    assert js("L.selectionScope(%s,null,[])" % json.dumps(G)) is None


@NODE
def test_fade_outside_scope_only():
    assert js("[L.fadeFor(['a'],'a'),L.fadeFor(['a'],'x'),L.fadeFor(null,'x')]") == [1, js("L.F3_FADE"), 1]
    assert 0 < js("L.F3_FADE") < 1


@NODE
def test_frame_centres_item_and_fits_graph():
    r = js("L.frameSelection([1,2,3],[[1,2,3],[101,2,3],[1,-98,3]],0.96,1.5)")
    assert r["target"] == [1, 2, 3]
    far = js("L.frameBall(100,0.96,1.5)")
    assert r["r"] == pytest.approx(far)


@NODE
def test_frame_alone_keeps_a_floor():
    r = js("L.frameSelection([0,0,0],[[0,0,0]],0.96,1.5)")
    assert r["target"] == [0, 0, 0] and r["r"] > 0
