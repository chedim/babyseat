"""#865: a chat answer relayed with answer_card in the turn the person gave it. The banner
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


class TestSameTurnRelay(unittest.TestCase):
    def setUp(self):
        self.srv, self.runner = _seat()
        self.est = self.srv.entity_store
        self.card = _card(self.srv)

    def props(self):
        return self.est.docs[self.card].properties

    def test_the_banner_does_not_take_the_words_away(self):
        _begin(self.srv)
        res = DM.relay(self.srv, self.card, 12, WORDS)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.props()["status"], "decided")

    def test_the_card_links_to_the_message_once_it_is_minted(self):
        _begin(self.srv)
        self.assertTrue(DM.relay(self.srv, self.card, 12, WORDS)["ok"])
        mid = _end(self.srv, self.runner)
        self.assertEqual(self.props()["message"], mid)
        self.assertEqual(len(_answers(self.srv, CG.msg_eid(mid), self.card)), 1)

    def test_the_words_do_not_outlive_the_turn(self):
        _begin(self.srv)
        _end(self.srv, self.runner)
        later = _card(self.srv)
        self.est.docs[later].properties["born"] = time.time() + 5   # made after that turn
        self.assertFalse(DM.relay(self.srv, later, 12, WORDS)["ok"])

    def test_a_relay_from_an_earlier_turn_keeps_its_link(self):
        u = self.runner.messages.add(SID, "msg_u0", "user", WORDS)
        a = self.runner.messages.add(SID, "msg_a0", "assistant", "ok")
        CG.on_turn_end(self.srv, self.runner, SID, u, WORDS, a, "ok")
        res = DM.relay(self.srv, self.card, 12, WORDS)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.props()["message"], "msg_u0")
        self.assertEqual(len(_answers(self.srv, CG.msg_eid("msg_u0"), self.card)), 1)


if __name__ == "__main__":
    unittest.main()
