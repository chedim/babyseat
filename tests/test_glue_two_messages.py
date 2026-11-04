"""Review of #903 (re-review): per-message state of messages glued into one turn.
RED tests: the per-session slots (_cg_understood, _cg_filled, _cg_text, _cg_said_now,
_cg_pending_answers) hold one message, so two glued messages / a card answered before the glue
land on the wrong message."""
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_conversation_graph import SID, _seat  # noqa: E402
from test_same_turn_relay_865 import _begin, _card  # noqa: E402
import membrane.server as MS  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import decision_map as DM  # noqa: E402

TURN = "twelve is fine for now"
A_WORDS, B_WORDS = "close goal one", "also check the garage"


def _door(srv, *glued):
    """The door's stash for each glued person message, as TurnRunner keeps it."""
    fake = types.SimpleNamespace(srv=srv, _glue_pre={})
    for mid, words in glued:
        fake._glue_pre[mid] = ({}, words)
    return fake


def _apply(fake, sid, mid):
    return MS.TurnRunner._apply_glue_pre(fake, sid, {"id": mid})


class TestTwoGluedMessages(unittest.TestCase):
    def test_two_glued_messages_each_keep_their_own_understanding_and_fill(self):
        from membrane import language as LL
        if not LL.available():
            self.skipTest("spaCy not installed")
        srv, runner = _seat()
        LL.seed(srv.entity_store)
        runner.messages.add(SID, "msg_u1", "user", TURN)
        ga = runner.messages.add(SID, "msg_ga", "user", A_WORDS)
        gb = runner.messages.add(SID, "msg_gb", "user", B_WORDS)
        a = runner.messages.add(SID, "msg_a1", "assistant", "done")
        fake = _door(srv, ("msg_ga", A_WORDS), ("msg_gb", B_WORDS))
        # what each glued message's fresh round leaves in the session slots (lanes._with_referent)
        for mid, mark in (("msg_ga", "A"), ("msg_gb", "B")):
            _apply(fake, SID, mid)
            srv.__dict__.setdefault("_cg_understood", {})[SID] = {"mark": mark, "surprise": []}
            srv.__dict__.setdefault("_cg_filled", {})[SID] = {"mark": mark, "words": []}
        CG.on_glued(srv, runner, SID, {"msgs": [(ga, "[user: Dima]\n" + A_WORDS), (gb, "[user: Dima]\n" + B_WORDS)],
                                       "orig": {}}, a)
        docs = srv.entity_store.docs
        sa = docs[CG.msg_eid("msg_ga")].properties["shadow"]
        sb = docs[CG.msg_eid("msg_gb")].properties["shadow"]
        self.assertEqual(sa["understanding"].get("mark"), "A", "A got another message's understanding")
        self.assertEqual(sb["understanding"].get("mark"), "B", "B lost its own understanding")
        self.assertEqual((sa.get("filled") or {}).get("mark"), "A")
        self.assertEqual((sb.get("filled") or {}).get("mark"), "B")


class TestAnswerCardsAcrossGlue(unittest.TestCase):
    def setUp(self):
        self.srv, self.runner = _seat()
        self.est = self.srv.entity_store
        self.card = _card(self.srv)

    def test_card_answered_before_the_glue_stays_on_the_turns_own_message(self):
        _begin(self.srv, TURN)
        self.assertTrue(DM.relay(self.srv, self.card, 12, TURN)["ok"])
        g = self.runner.messages.add(SID, "msg_g1", "user", A_WORDS)
        fake = _door(self.srv, ("msg_g1", A_WORDS))
        _apply(fake, SID, "msg_g1")
        u = self.runner.messages.add(SID, "msg_u1", "user", TURN)
        a = self.runner.messages.add(SID, "msg_a1", "assistant", "ok")
        CG.on_glued(self.srv, self.runner, SID, {"msgs": [(g, "[user: Dima]\n" + A_WORDS)], "orig": {}}, a)
        CG.on_turn_end(self.srv, self.runner, SID, u, TURN, a, "ok")
        self.assertEqual(self.est.docs[self.card].properties.get("message"), "msg_u1")

    def test_answer_card_quoting_the_first_message_still_works_after_a_glue(self):
        _begin(self.srv, TURN)                               # the turn's own words go to _cg_said_now
        fake = _door(self.srv, ("msg_g1", A_WORDS))
        _apply(fake, SID, "msg_g1")                          # the glue makes the glued words current
        res = DM.relay(self.srv, self.card, 12, TURN)        # the agent quotes the turn's own message
        self.assertTrue(res["ok"], res)


if __name__ == "__main__":
    unittest.main()
