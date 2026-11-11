"""Messages as relations on the graph, read by our own reader (2026-11-06, the operator: "our own reader, swap the
reader"): each clause a statement with role edges; I/my -> the speaker; the reader's possible axis puts a clause on
the possible branch; questions never asserted; a question compiles to a pattern with a variable at its asked part,
matched by `near`, the best binding answering. No parser at read time."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import meaning as MN  # noqa: E402
from membrane import structures as STR  # noqa: E402

VERBS = STR.VERBS


def reading(words, labels, clauses, possible=None, text=None):
    return {"text": text or " ".join(words) + ".", "words": words, "said": words, "labels": labels,
            "clauses": clauses, "possible": possible or {}}


def exact(a, b):
    return 1.0 if a == b else 0.0


class Web(unittest.TestCase):
    def test_a_clause_becomes_a_statement_with_roles_and_the_speaker_bound(self):
        r = reading(["I", "researched", "adoption", "agencies", "yesterday"], ["SELF", "VERB", "OBJ", "OBJ", "TIME"],
                    [0, 0, 0, 0, 0])
        st = MN.from_reading(r, speaker="Caroline", listener="Melanie", verbs=VERBS)
        self.assertEqual(st[0]["verbs"], ["researched"])
        self.assertEqual(st[0]["roles"]["agent"], ["caroline"])
        self.assertEqual(st[0]["roles"]["obj"], ["adoption", "agencies"])
        self.assertEqual(st[0]["roles"]["when"], ["yesterday"])

    def test_a_possible_clause_is_on_the_possible_branch_and_questions_assert_nothing(self):
        r = reading(["I", "am", "thinking", "of", "working", "with", "people"],
                    ["SELF", "OTHER", "VERB", "OTHER", "POSSIBLE", "OTHER", "OBJ"], [0, 0, 0, 1, 1, 1, 1],
                    possible={1: True})
        st = MN.from_reading(r, speaker="Caroline", verbs=VERBS)
        self.assertEqual([s["possible"] for s in st], [False, True])
        q = reading(["did", "you", "go"], ["OTHER", "SELF", "VERB"], [0, 0, 0], text="Did you go?")
        self.assertEqual(MN.from_reading(q, speaker="Caroline", verbs=VERBS), [])

    def test_the_question_pattern_binds_its_variable_to_the_best_statement(self):
        web = MN.from_reading(reading(["I", "went", "to", "a", "group"], ["SELF", "VERB", "OTHER", "DET", "PLACE"],
                                      [0] * 5), speaker="Caroline", verbs=VERBS) + \
            MN.from_reading(reading(["I", "researched", "adoption", "agencies"], ["SELF", "VERB", "OBJ", "OBJ"],
                                    [0] * 4), speaker="Caroline", verbs=VERBS)
        q = reading(["what", "did", "Caroline", "researched"], ["WH_O", "OTHER", "DOER", "VERB"], [0] * 4)
        pat = MN.pattern(q, "OBJ", verbs=VERBS)
        got = MN.answer(pat, web, exact)
        self.assertEqual(got[0]["filler"], "adoption agencies")


    def test_rare_words_outweigh_common_ones_and_a_misread_asked_part_still_answers(self):
        web = MN.from_reading(reading(["Caroline", "thought", "about", "the", "accident"], ["DOER", "VERB", "OTHER", "DET", "OBJ"],
                                      [0] * 5), verbs=VERBS) + \
            MN.from_reading(reading(["Caroline", "made", "a", "necklace"], ["DOER", "VERB", "DET", "OBJ"], [0] * 4), verbs=VERBS)
        q = reading(["what", "did", "Caroline", "make", "for", "grandma"], ["WH_O", "OTHER", "DOER", "VERB", "OTHER", "OBJ"], [0] * 6)
        pat = MN.pattern(q, "MANNER", verbs=VERBS)            # a misread: asks "manner", no statement holds one
        idf = {"caroline": 0.1, "make": 2.0, "made": 2.0, "grandma": 3.0}
        got = MN.answer(pat, web, lambda a, b: 1.0 if a[:2] == b[:2] else 0.0, idf=idf)
        self.assertEqual(got[0]["statement"]["verbs"], ["made"])
        self.assertIn("necklace", got[0]["filler"])          # its words the question did not say


    def test_a_per_statement_weight_can_replace_rarity(self):
        web = MN.from_reading(reading(["Caroline", "made", "a", "necklace"], ["DOER", "VERB", "DET", "OBJ"], [0] * 4),
                              msg="m1", verbs=VERBS) + \
            MN.from_reading(reading(["Caroline", "made", "a", "bowl"], ["DOER", "VERB", "DET", "OBJ"], [0] * 4), msg="m2", verbs=VERBS)
        q = reading(["what", "did", "Caroline", "make"], ["WH_O", "OTHER", "DOER", "VERB"], [0] * 4)
        pat = MN.pattern(q, "OBJ", verbs=VERBS)
        moved = {"m1": 0.1, "m2": 0.9}                       # how far each message moved the matched words
        got = MN.answer(pat, web, lambda a, b: 1.0 if a[:2] == b[:2] else 0.0, weigh=lambda a, b, s: moved[s["msg"]])
        self.assertEqual(got[0]["statement"]["msg"], "m2")


    def test_a_chain_joins_statements_through_a_shared_rare_thing_and_a_shorter_chain_wins_ties(self):
        r = lambda ws, ls, m: MN.from_reading(reading(ws, ls, [0] * len(ws)), msg=m, verbs=VERBS)
        web = r(["Caroline", "wants", "counseling"], ["DOER", "VERB", "OBJ"], "a") + \
            r(["counseling", "helps", "trans", "people"], ["DOER", "VERB", "OBJ", "OBJ"], "b") + \
            r(["dogs", "chase", "cats"], ["DOER", "VERB", "OBJ"], "c")
        q = reading(["what", "does", "Caroline", "want", "for", "trans", "people"],
                    ["WH_O", "OTHER", "DOER", "VERB", "OTHER", "OBJ", "OBJ"], [0] * 7)
        pat = MN.pattern(q, "OBJ", verbs=VERBS)
        idf = {"caroline": 0.1, "want": 1.0, "wants": 1.0, "trans": 2.0, "people": 2.0, "counseling": 2.0}
        near = lambda a, b: 1.0 if a[:3] == b[:3] else 0.0
        got = MN.chains(pat, web, near, idf=idf, rare={"counseling", "trans", "people"})
        self.assertEqual([s["msg"] for s in got[0]["chain"]], ["a", "b"])   # joined on counseling: covers more
        one = MN.chains(pat, web[:1], near, idf=idf, rare=set())
        self.assertEqual(len(one[0]["chain"]), 1)


    def test_chains_form_around_the_person_asked_about_when_each_adds_to_the_answer(self):
        r = lambda ws, ls, m: MN.from_reading(reading(ws, ls, [0] * len(ws)), msg=m, verbs=VERBS)
        web = r(["Caroline", "wants", "counseling"], ["DOER", "VERB", "OBJ"], "a") + \
            r(["Caroline", "likes", "working", "with", "trans", "people"], ["DOER", "VERB", "OBJ", "OTHER", "OBJ", "OBJ"], "b") + \
            r(["Melanie", "paints", "sunsets"], ["DOER", "VERB", "OBJ"], "c")
        q = reading(["what", "career", "does", "Caroline", "want"], ["WH_O", "OBJ", "OTHER", "DOER", "VERB"], [0] * 5)
        pat = MN.pattern(q, "OBJ", verbs=VERBS)
        idf = {"caroline": 0.2, "want": 1.0, "wants": 1.0, "career": 2.0, "counseling": 2.0, "working": 2.0,
               "trans": 2.0, "people": 1.5}
        near = lambda a, b: 1.0 if a[:3] == b[:3] else 0.0
        got = MN.chains(pat, web, near, idf=idf, rare=set(), people={"caroline"}, answer_side=True)
        self.assertEqual([s["msg"] for s in got[0]["chain"]], ["a", "b"])   # Caroline's two pieces, joined
        self.assertIn("counseling", got[0]["filler"])
        self.assertIn("trans", got[0]["filler"])


if __name__ == "__main__":
    unittest.main()
