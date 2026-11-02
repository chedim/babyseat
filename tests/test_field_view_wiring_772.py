"""#772 re-review blockers (cd7dcafd): the field view's UI wiring, which the pure-function tests cannot see."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

# a stub DOM just big enough to construct the custom element and count its redraws
STUB = r"""
let Cls = null;
globalThis.HTMLElement = class { constructor(){ this.clientWidth=800; this.clientHeight=600; }
  attachShadow(){ this.shadowRoot = { innerHTML:"", querySelector(){ return null; } }; return this.shadowRoot; }
  dispatchEvent(){} };
globalThis.customElements = { get(){ return null; }, define(n, c){ Cls = c; } };
require(%r);
%s
"""


def _node(body):
    js = STUB % (str(UI / "fieldview.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_blocker1_home_listens_for_chat_send_and_replies():
    src = (UI / "home.js").read_text()
    assert re.search(r"addEventListener\(\s*['\"]chat-send['\"]", src), "home.js never hears the fieldview's chat-send"
    assert ".reply(" in src, "home.js never calls fieldview.reply() with the model's turn"


@NODE
def test_blocker2_home_api_has_post_for_item_chat_say():
    src = (UI / "home.js").read_text()
    m = re.search(r"_cockpit\.api\s*=\s*\{(.*?)\};", src, re.S)
    assert m and re.search(r"\bpost\b", m.group(1)), "home.js hands the field view an api with only get; _say throws"


@NODE
def test_blocker3_draw_assigns_innerhtml_once_not_plus_equals():
    src = (UI / "fieldview.js").read_text()
    assert "innerHTML +=" not in src, "innerHTML += re-parses the shadow root and kills the .say handler"
    assert len(re.findall(r"shadowRoot\.innerHTML\s*=|sr\.innerHTML\s*=", src)) == 1


@NODE
def test_blocker4_refresh_does_not_redraw_when_graph_unchanged():
    out = _node("""const e=new Cls(); let n=0; e._draw=()=>{n++}; const g={nodes:[{id:'a',kind:'goal',title:'x',status:'open',goal:null}],edges:[]};
    e._api={get:async()=>JSON.parse(JSON.stringify(g)),post:async()=>({})};
    (async()=>{ await e.refresh(); await e.refresh(); clearTimeout(e._t); process.stdout.write(JSON.stringify({n})); })();""")
    assert out["n"] == 1, "a refresh with an identical graph redrew (%d draws) and drops focus/typed text" % out["n"]
