"""memspeak's "when" answers (the operator 2026-11-07: "lets fix it" -- bench arm 91e: memspeak lost the vote's right
#1 on 12 temporal questions and gained 7). The causes it found, pinned here:
  - a dated fact about ANOTHER happening beat the fact that says the asked one ("signed up for a pottery class
    yesterday" over "made it in pottery class yesterday" for "when did she make a plate in pottery class");
  - a fact on the FUTURE side ("I'm thinking of working with bloggers in the next few months") answered a past
    "when did" question.
The rule: the fact that says the asked happening wins (how many of the question's words it says, rare ones weighed
more), recall's order among equals; its time is its own span, else the day its message was said ("I just launched an
ad campaign" -> that day); among equal matches the one saying its own time first; a past question never takes a
future-side fact.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import facts as F  # noqa: E402
from membrane import infer  # noqa: E402
from membrane.entities import QuantumEntity  # noqa: E402
from test_conversation_graph import _seat  # noqa: E402
from test_structures_932 import _structs  # noqa: E402

DAY = 86400.0
T0 = 1_700_000_000.0


def _fact(srv, msg, words, said, span=None, side=None, possible=False, n=0):
    srv.entity_store.upsert(QuantumEntity(eid=f"{F.CLAUSE}{msg}:{n}", cls=F.CLASS_CLAUSE, properties={
        "name": " ".join(w for w, _l in words), "msg": msg, "speaker": "sam", "said": said,
        "text": " ".join(w for w, _l in words) + ".", "words": [list(x) for x in words], "possible": possible,
        "span": list(span[:2]) if span else None, "period": span[2] if span else None, "side": side,
        "moment": span[0] if span else said, "silent_birth": True}), embedder=None)


def _ask(srv, text, pool):
    return infer.ask(srv, "", text, pool=pool, prefer=False, whole=True, check=True, pick="learned", hole=True)


class WhenAnswers(unittest.TestCase):
    def setUp(self):
        self.srv, _r = _seat()
        self.srv.structures = _structs()

    def test_the_fact_that_says_the_happening_beats_a_dated_fact_about_another(self):
        _fact(self.srv, "m:other", [["the", "DET"], ["dog", "DOER"], ["bit", "VERB"], ["the", "DET"], ["man", "OBJ"],
                                    ["yesterday", "TIME"]], T0, span=(T0 - DAY, T0, "day"))
        _fact(self.srv, "m:event", [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["that", "DET"],
                                    ["mouse", "OBJ"]], T0 + 5 * DAY)
        got = _ask(self.srv, "When did the cat chase the mouse?", ["m:other", "m:event"])
        self.assertEqual(infer.LAST["facts"][0]["msg"], "m:event")
        self.assertIn("cat chased that mouse", got[1])

    def test_with_no_time_of_its_own_the_answer_is_the_day_it_was_said(self):
        _fact(self.srv, "m:event", [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["that", "DET"],
                                    ["mouse", "OBJ"]], T0 + 5 * DAY)
        got = _ask(self.srv, "When did the cat chase the mouse?", ["m:event"])
        self.assertEqual(got[0], F.period({"said": T0 + 5 * DAY}))

    def test_equal_matches_keep_recalls_order(self):
        for m, said in (("m:a", T0), ("m:b", T0 + 9 * DAY)):
            _fact(self.srv, m, [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["that", "DET"],
                                ["mouse", "OBJ"]], said)
        _ask(self.srv, "When did the cat chase the mouse?", ["m:b", "m:a"])
        self.assertEqual(infer.LAST["facts"][0]["msg"], "m:b")

    def test_only_the_chosen_fact_moves_up_the_rest_keep_recalls_order(self):
        _fact(self.srv, "m:a", [["the", "DET"], ["dog", "DOER"], ["bit", "VERB"], ["the", "DET"], ["man", "OBJ"],
                                ["yesterday", "TIME"]], T0, span=(T0 - DAY, T0, "day"))
        _fact(self.srv, "m:b", [["the", "DET"], ["cat", "DOER"], ["found", "VERB"], ["the", "DET"], ["ball", "OBJ"]],
              T0 + DAY)                                     # says "cat": a full re-sort would lift it over m:a
        _fact(self.srv, "m:event", [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["that", "DET"],
                                    ["mouse", "OBJ"]], T0 + 5 * DAY)
        _ask(self.srv, "When did the cat chase the mouse?", ["m:a", "m:b", "m:event"])
        self.assertEqual(infer.LAST["ranking"], ["m:event", "m:a", "m:b"])

    def test_a_past_question_never_takes_a_future_side_fact(self):
        _fact(self.srv, "m:plan", [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["that", "DET"],
                                   ["mouse", "OBJ"], ["next", "TIME"], ["month", "TIME"]], T0, side="future")
        _fact(self.srv, "m:done", [["the", "DET"], ["cat", "DOER"], ["chased", "VERB"], ["mouse", "OBJ"]],
              T0 + 40 * DAY)
        _ask(self.srv, "When did the cat chase the mouse?", ["m:plan", "m:done"])
        self.assertEqual(infer.LAST["facts"][0]["msg"], "m:done")


if __name__ == "__main__":
    unittest.main()
