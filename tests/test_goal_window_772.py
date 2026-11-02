"""#772: one click on a goal marker opens a larger window: full goal text (links clickable), info, chat, logs."""
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


G = {"nodes": [{"id": "g1", "kind": "goal", "title": "G", "status": "open"},
               {"id": "t1", "kind": "task", "title": "T", "status": "open", "goal": "g1"}],
     "edges": [{"from": "t1", "to": "g1", "rel": "of"}]}
H = {"properties": {"summary": "Fix <b> see https://x.io/a?b=1&c=2 now"}, "changes": [], "relations": []}
C = {"messages": [{"who": "me", "text": "hi"}]}


@NODE
def test_linkify_makes_urls_clickable_and_escapes():
    h = _js("F.linkify('a <i> https://x.io/a?b=1&c=2.')")
    assert '<a href="https://x.io/a?b=1&amp;c=2"' in h and "&lt;i&gt;" in h and "<i>" not in h


@NODE
def test_window_has_full_text_info_chat_and_logs_in_one_view():
    w = _js("F.windowInfo(%s,'g1',%s,%s)" % (json.dumps(G), json.dumps(H), json.dumps(C)))
    assert "https://x.io/a" in w["text"] and 'href="https://x.io/a' in w["textHtml"]
    assert w["info"]["kind"] == "goal" and w["chat"] == ["me: hi"] and w["logs"]


@NODE
def test_window_is_larger_than_the_old_300px_card():
    assert _js("F.WINDOW.width") > 300 and _js("F.WINDOW.scroll") is True
