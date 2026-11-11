"""Response shaping by the asked class (the operator 2026-11-07: the asked slot shapes the response, never recall).
For "what / which X", the caller hands memspeak a `kind` test (is this word a kind of X? -- the class graph's
answer). memspeak keeps the fact it found and the ranking it was handed; it only says, from that message's facts, the
words that pass the test. With no test, or no word passing, nothing changes.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import infer  # noqa: E402
from test_conversation_graph import _seat  # noqa: E402
from test_structures_932 import _structs  # noqa: E402
from test_when_answers import T0, _fact  # noqa: E402

PLAYS = [["sam", "DOER"], ["plays", "VERB"], ["the", "DET"], ["violin", "OBJ"], ["and", "OTHER"],
         ["the", "DET"], ["clarinet", "OBJ"]]
INSTRUMENTS = {"violin", "clarinet"}


def _ask(srv, text, pool, kind=None):
    return infer.ask(srv, "", text, pool=pool, prefer=False, whole=True, check=True, pick="learned", hole=True,
                     kind=kind)


class TypedAnswers(unittest.TestCase):
    def setUp(self):
        self.srv, _r = _seat()
        self.srv.structures = _structs()
        _fact(self.srv, "m:other", [["the", "DET"], ["dog", "DOER"], ["bit", "VERB"], ["the", "DET"],
                                    ["man", "OBJ"]], T0)
        _fact(self.srv, "m:plays", PLAYS, T0 + 86400)

    def test_the_answer_is_the_words_of_the_asked_kind(self):
        got = _ask(self.srv, "What instruments does Sam play?", ["m:plays"],
                   kind=lambda w: w.lower() in INSTRUMENTS)
        self.assertIn("violin", got[0])
        self.assertIn("clarinet", got[0])
        self.assertNotIn("plays", got[0])

    def test_shaping_never_reorders_what_was_found(self):
        _ask(self.srv, "What instruments does Sam play?", ["m:other", "m:plays"])
        before = list(infer.LAST["ranking"])
        _ask(self.srv, "What instruments does Sam play?", ["m:other", "m:plays"],
             kind=lambda w: w.lower() in INSTRUMENTS)
        self.assertEqual(infer.LAST["ranking"], before)

    def test_a_list_answer_gathers_the_kind_across_the_facts_that_answer(self):
        _fact(self.srv, "m:violin", [["sam", "DOER"], ["plays", "VERB"], ["the", "DET"], ["violin", "OBJ"]], T0 + 2e5)
        _fact(self.srv, "m:clarinet", [["sam", "DOER"], ["plays", "VERB"], ["the", "DET"], ["clarinet", "OBJ"]],
              T0 + 3e5)
        got = _ask(self.srv, "What instruments does Sam play?", ["m:violin", "m:other", "m:clarinet"],
                   kind=lambda w: w.lower() in INSTRUMENTS)
        self.assertIn("violin", got[0])
        self.assertIn("clarinet", got[0])

    def test_no_test_or_no_word_of_the_kind_changes_nothing(self):
        plain = _ask(self.srv, "What instruments does Sam play?", ["m:plays"])
        none = _ask(self.srv, "What instruments does Sam play?", ["m:plays"], kind=lambda w: False)
        self.assertEqual(plain, none)


if __name__ == "__main__":
    unittest.main()
