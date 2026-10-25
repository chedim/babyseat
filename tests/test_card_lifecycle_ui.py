"""Card lifecycle on the board: a decided card that needs work sits in the
work band; closed and dropped cards are the past; open cards carry a Drop button."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(expr):
    js = "const D=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "decisions.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def test_bands_put_waiting_work_in_now_and_closed_in_past():
    board = {"cards": [
        {"eid": "a", "title": "a", "status": "decided", "work_needed": True},
        {"eid": "b", "title": "b", "status": "closed"},
        {"eid": "c", "title": "c", "status": "dropped"},
        {"eid": "d", "title": "d", "status": "in_flight"},
        {"eid": "e", "title": "e", "status": "open"},
    ], "edges": []}
    out = _js("D.bands(%s)" % json.dumps(board))
    assert sorted(c["eid"] for c in out["now"]) == ["a", "d"]
    assert sorted(c["eid"] for c in out["past"]) == ["b", "c"]
    assert [c["eid"] for c in out["future"]] == ["e"]


def test_open_card_has_a_drop_button():
    src = (UI / "decisions.js").read_text()
    assert 'class="drop"' in src and "/membrane/decision/drop" in src
