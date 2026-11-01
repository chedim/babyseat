"""k7: the item card has tabs Info, Chat, Logs. Chat = message entities that -refers-to-> the item
(convgraph's own edge); Logs = the k5 entity_history call."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
G = MEMBRANE_NS + "g"


def _srv():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    est.upsert(QuantumEntity(eid=G, cls="Goal", properties={"name": "ship", "status": "open"}))
    be = MemBackend()
    ef = SimpleNamespace(b=be, origin="o", deltas_of=lambda eid: [])
    return SimpleNamespace(entity_store=est, efield=ef), est, be


def test_item_chat_says_and_lists_messages_referring_to_the_item():
    srv, est, be = _srv()
    r = DM.item_say(srv, G, "is this blocked?", who="dima")
    assert r["ok"] and r["message"].startswith(MEMBRANE_NS + "msg:")
    DM.item_say(srv, G, "second")
    chat = DM.item_chat(srv, G)
    assert [m["text"] for m in chat["messages"]] == ["is this blocked?", "second"]
    assert chat["messages"][0]["who"] == "dima"
    # the same convgraph edge: message -refers-to-> item
    assert any(x["subj"] == r["message"] and x["obj"] == G for x in R.of(be, "o", G, "refers-to"))
    # a message about another item does not appear
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "h", cls="Goal", properties={"name": "other"}))
    DM.item_say(srv, MEMBRANE_NS + "h", "elsewhere")
    assert len(DM.item_chat(srv, G)["messages"]) == 2
    assert DM.item_chat(srv, "nope")["ok"] is False
    assert DM.item_say(srv, G, "  ")["ok"] is False


def _js(expr):
    js = "const F=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / "fieldview.js"), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_card_tabs_and_their_rows():
    assert _js("F.TABS") == ["Info", "Chat", "Logs"]
    chat = {"messages": [{"who": "dima", "text": "hi"}, {"text": "yo"}]}
    assert _js("F.chatRows(%s)" % json.dumps(chat)) == ["dima: hi", "yo"]
    hist = {"properties": {"receipt": "r1", "name": "x"},
            "changes": [{"seq": 3, "stream": "obs", "text": "moved"}],
            "relations": [{"subj": "a", "rel": "of", "obj": "b"}]}
    rows = _js("F.logRows(%s)" % json.dumps(hist))
    assert any("receipt" in r and "r1" in r for r in rows)
    assert any("moved" in r for r in rows) and any("of" in r and "b" in r for r in rows)


def test_card_reads_only_k5_and_item_chat():
    src = (UI / "fieldview.js").read_text()
    assert "/membrane/entity-history" in src and "/membrane/item-chat" in src
