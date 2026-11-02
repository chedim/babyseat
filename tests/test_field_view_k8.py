"""k8 (#772): each goal gets a distinct edge style; a bottom chat bar with a response window."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr):
    js = "const F=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "fieldview.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


G = {"nodes": [{"id": "g%d" % i, "kind": "goal", "title": "g", "status": "open"} for i in range(4)]
     + [{"id": "t1", "kind": "task", "title": "t", "status": "open", "goal": "g1"}],
     "edges": [{"from": "t1", "to": "g1", "rel": "of"}]}


@NODE
def test_each_goal_has_a_distinct_edge_style():
    s = _js("F.edgeStyles(%s)" % json.dumps(G))
    assert set(s) == {"g0", "g1", "g2", "g3"}
    assert len({(v["color"], v["dash"]) for v in s.values()}) == 4
    assert _js("F.edgeStyles(%s)" % json.dumps(G)) == s


@NODE
def test_chat_bar_sends_and_shows_the_response():
    st = _js("F.chatStep({log: []}, 'send', '  hi  ')")
    assert st["send"] == "hi" and st["log"][-1] == {"who": "me", "text": "hi"}
    assert _js("F.chatStep({log: []}, 'send', '   ')")["send"] is None
    st = _js("F.chatStep({log: []}, 'reply', 'hello')")
    assert st["log"][-1] == {"who": "bot", "text": "hello"}

