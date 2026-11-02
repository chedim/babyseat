"""#855: selecting keeps the camera on the centre and puts the item
in front of it; unselecting restores the pose from before the first selection. Pure functions via node."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def js(expr):
    code = "const L=require(process.argv[1]);process.stdout.write(JSON.stringify(%s))" % expr
    return json.loads(subprocess.check_output(["node", "-e", code, str(UI / "field3d.js")]))


def cam_pos(target, c):
    return [target[0] + c["r"] * math.sin(c["phi"]) * math.sin(c["theta"]),
            target[1] + c["r"] * math.cos(c["phi"]),
            target[2] + c["r"] * math.sin(c["phi"]) * math.cos(c["theta"])]


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def norm(a):
    return math.sqrt(sum(x * x for x in a))


@NODE
def test_selecting_keeps_target_and_puts_item_between_camera_and_centre():
    target, item = [0, 0, 0], [30, 10, -20]
    pts = [item, [60, 10, -20], [30, 70, -20], [30, 10, 40]]
    cur = {"theta": 0.3, "phi": 1.2, "r": 500}
    c = js("L.frameAround(%s,%s,%s,%s,0.96,1.5)" % (json.dumps(target), json.dumps(item), json.dumps(pts), json.dumps(cur)))
    pos = cam_pos(target, c)
    d_item, d_tgt = norm(sub(item, pos)), norm(sub(target, pos))
    assert d_item < d_tgt
    assert d_item + norm(sub(item, target)) == pytest.approx(d_tgt, rel=1e-6)
    fwd = [x / d_tgt for x in sub(target, pos)]
    half = min(0.96 / 2, math.atan(math.tan(0.48) * 1.5))
    for p in pts:
        v = sub(p, pos)
        assert math.acos(min(1.0, sum(a * b for a, b in zip(v, fwd)) / norm(v))) < half


@NODE
def test_item_at_centre_keeps_direction():
    cur = {"theta": 0.3, "phi": 1.2, "r": 500}
    c = js("L.frameAround([5,5,5],[5,5,5],[[5,5,5],[25,5,5]],%s,0.96,1.5)" % json.dumps(cur))
    assert c["theta"] == pytest.approx(0.3) and c["phi"] == pytest.approx(1.2) and c["r"] > 0


@NODE
def test_unselect_restores_pose_before_first_selection_through_a_then_b():
    r = js("""(()=>{const p0={target:[1,2,3],theta:.5,phi:1,r:400};
      let s=L.camSave(null,p0);
      s=L.camSave(s,{target:[1,2,3],theta:2,phi:2,r:90});
      return {kept:s,back:L.camRestore(s)}})()""")
    assert r["kept"]["r"] == 400 and r["back"] == {"target": [1, 2, 3], "theta": .5, "phi": 1, "r": 400}
    assert js("L.camRestore(null)") is None


@NODE
def test_click_on_empty_spot_during_first_clicks_flight_keeps_selection():
    assert js("L.clickAction('e1',null,true)") == "keep"      # the item flew away; this is not the background
    assert js("L.clickAction('e1',null,false)") == "unselect"  # real background click
    assert js("L.clickAction(null,null,true)") == "unselect"
    assert js("L.clickAction('e1',{id:'e2'},true)") == "select"  # another item swaps


def test_no_double_click_left():
    src = (UI / "field3d.js").read_text()
    for gone in ("F3_DBL_MS", "resolveClick", "dblclick", "_lastPick"):
        assert gone not in src, gone
