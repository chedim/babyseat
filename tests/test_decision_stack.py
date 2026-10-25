"""The decision cockpit's open pile stacks instead of growing: the first rows stay
in flow, newer (farther) cards hide behind older ones with a strip showing, and a
hovered card pops over the rest, so the pile never pushes the bar off the page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr):
    js = "const D=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "decisions.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_the_first_rows_stay_in_flow_the_rest_stack_behind():
    flow = _js("D.STACK_FLOW_ROWS")
    assert flow >= 1
    for r in range(flow):
        assert _js("D.stackSlot(%d)" % r) == {"stacked": False, "shift": 0, "z": 0}
    s1, s2 = _js("D.stackSlot(%d)" % flow), _js("D.stackSlot(%d)" % (flow + 1))
    assert s1["stacked"] and s2["stacked"]
    assert s1["shift"] == 0 and s2["shift"] == _js("D.STACK_STRIP")  # the front card sits flush; one more strip per newer card
    assert s1["z"] > s2["z"] > 0                                   # newer is behind older


@NODE
def test_the_stack_never_grows_past_its_cap():
    far = _js("D.stackSlot(500)")
    assert far["shift"] == _js("D.stackPad()") == _js("(D.STACK_SHOWN - 1) * D.STACK_STRIP")
    assert far["z"] >= 1


def test_a_hovered_stacked_card_pops_over_every_other():
    src = (UI / "decisions.js").read_text()
    assert ".stack .dc:hover" in src and ".stack .dc:focus-visible" in src
    assert "z-index:60" in src
