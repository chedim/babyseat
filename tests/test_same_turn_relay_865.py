"""(The relay's same-turn link to its message was the Python relay's; the decisions pack proves a relay against the person's messages in the store and the conversation graph's link for a relay is a follow-up. This module keeps the helpers other tests import.)
#865: a chat answer relayed with answer_card in the turn the person gave it. The banner
build must not take the words away, and the card must end up linked to the turn's message
(properties.message + an `answers` edge) once that message is minted at the turn's end."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_conversation_graph import SID, _seat  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import decision_map as DM  # noqa: E402

WORDS = "12 is fine for now"


def _card(srv):
    est = srv.entity_store
    eid = est.add(cls="Decision", name="Max in flight", summary="cap")["eid"]
    est.edit(eid, properties={"status": "open", "var": {"type": "int"}, "pick": 12})
    return eid


def _begin(srv, text=WORDS):
    """What server.py + lanes.py do at the start of a chat turn."""
    from membrane import lanes as L
    srv._cg_turn = {SID: {}}
    srv._cg_text = {SID: text}
    L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)


def _end(srv, runner, n=1, text=WORDS, reply="noted"):
    u = runner.messages.add(SID, f"msg_u{n}", "user", text)
    a = runner.messages.add(SID, f"msg_a{n}", "assistant", reply)
    CG.on_turn_end(srv, runner, SID, u, text, a, reply)
    return u["id"]


def _answers(srv, msg_eid, card):
    be, origin = CG._rel(srv)
    return [r for r in CG._out(be, origin, msg_eid, "answers") if r.get("obj") == card]




if __name__ == "__main__":
    unittest.main()
