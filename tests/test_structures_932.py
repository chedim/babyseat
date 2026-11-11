"""#932, the adopted design (2026-11-05): a word-by-word structure reader, facts on one world spine (or a
possible branch) by their moment, threads read in moment order, the field's move log, and the 🧩 speaking from
recall's messages (time, order, timeline and speaker from the graph; preferences, never gates).

Pinned with a fake word embedder: a word alone is a part of its own; in a sentence it also carries its kind's
direction and, after the verb, a direction of being done to (what the sentence does to it)."""
import os
import sys
import unittest
import zlib
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import facts as F  # noqa: E402
from membrane import infer  # noqa: E402
from membrane import structures as ST  # noqa: E402
from test_conversation_graph import _seat, _turn  # noqa: E402

KIND = {"the": "det", "a": "det", "that": "det", "what": "wh", "when": "wh", "did": "aux", "yesterday": "time",
        "bit": "verb", "chased": "verb", "found": "verb", "find": "verb", "chase": "verb", "bite": "verb",
        "might": "modal", "go": "verb"}
_BASE: dict = {}


def fake_words(texts):
    after = np.random.RandomState(99).randn(32)
    out = []
    for t in texts:
        words = [w for w in t.lower().replace("?", " ").replace(".", " ").split() if w]
        vecs, seen_verb = [], False
        for w in words:
            k = KIND.get(w, "noun")
            if k not in _BASE:
                _BASE[k] = np.random.RandomState(len(_BASE) + 1).randn(32)
            own = np.random.RandomState(zlib.crc32(w.encode())).randn(32)
            ctx = _BASE[k] * 2 + (after * 2 if seen_verb and k == "noun" else 0)
            vecs.append(own + (ctx if len(words) > 1 else 0))
            seen_verb = seen_verb or k == "verb"
        out.append((words, np.array(vecs, dtype=np.float32)))
    return out


def _structs():
    s = ST.Structures(fake_words)
    s.learn("The dog bit the man.", ["DET", "DOER", "VERB", "DET", "OBJ"], [0] * 5)
    s.learn("The cat chased the dog yesterday.", ["DET", "DOER", "VERB", "DET", "OBJ", "TIME"], [0] * 6)
    s.learn("The man might go.", ["DET", "DOER", "MODAL", "POSSIBLE"], [0] * 4)
    s.learn("What did the dog find?", ["WH_O", "OTHER", "DET", "DOER", "VERB"], [0] * 5)
    s.learn("When did the cat chase the dog?", ["WH_ADV", "OTHER", "DET", "DOER", "VERB", "DET", "OBJ"], [0] * 7)
    s.learn_asked(["WH_O", "OTHER", "DET", "DOER", "VERB"], "OBJ")
    return s


def _talk():
    srv, runner = _seat()
    srv.structures = _structs()
    for i, t in enumerate(["The man bit the man.", "The cat found the man.", "A cat chased that mouse yesterday.",
                           "The cat found the ball."]):
        _turn(srv, runner, i, "[user: Sam]\n" + t, "ok")
    return srv


class Reader(unittest.TestCase):
    def test_words_take_blocks_word_by_word(self):
        got = _structs().read("A cat chased that mouse.")[0]
        self.assertEqual(got["labels"], ["DET", "DOER", "VERB", "DET", "OBJ"])

    def test_a_question_is_read_by_the_question_reader(self):
        got = _structs().read("What did the cat find?")[0]
        self.assertEqual(got["labels"][0], "WH_O")

    def test_the_asked_block_is_learned_per_question_word(self):
        s = ST.Structures(fake_words)
        labs = ["WH_O", "VERB", "DOER", "VERB", "OBJ"]
        s.learn_asked(labs, "TIME", ["when", "is", "x", "doing", "y"])
        s.learn_asked(labs, "TIME", ["when", "is", "x", "doing", "y"])
        s.learn_asked(labs, "OBJ", ["what", "does", "x", "think", "y"])
        self.assertEqual(s.asked_for(labs, ["what", "does", "z", "think", "w"]), "OBJ")   # what: from what's
        self.assertEqual(s.asked_for(labs, ["when", "did", "z", "go", "w"]), "TIME")
        self.assertEqual(s._qword(["WH_ADV", "TIME"], ["how", "long"]), "how long")

    def test_a_hole_is_predicted_from_the_rest_of_its_sentence(self):
        s = _structs()
        for t in ["The dog found the man.", "The man chased the cat.", "The cat bit the dog.", "The dog chased the man."]:
            s.learn(t, ["DET", "DOER", "VERB", "DET", "OBJ"], [0] * 5)
        s.learn_holes()
        self.assertIn("OBJ", s.holes)
        words, vecs = s.words(["The cat found the ball."])[0]
        pred = s.hole(words, vecs, ["DET", "DOER", "VERB", "DET", "OBJ"], "OBJ")
        ball, cat = ST._unit(vecs[4]), ST._unit(vecs[1])
        self.assertGreater(float(pred @ ball), float(pred @ cat))       # the done-to's place, not the doer's

    def test_the_holes_report_a_no_map_baseline_and_learn_in_the_leftover_space_too(self):
        s = _structs()
        for t in ["The dog found the man.", "The man chased the cat.", "The cat bit the dog.", "The dog chased the man."]:
            s.learn(t, ["DET", "DOER", "VERB", "DET", "OBJ"], [0] * 5)
        s.read("The cat found the ball.")                                # the readers (and the spill fit) prepared
        raw = s.learn_holes()
        self.assertIn("base_cos", raw["OBJ"])
        self.assertIn("centroid_cos", raw["OBJ"])
        left = s.learn_holes(space="leftover")
        self.assertEqual(s.hole_space, "leftover")
        words, vecs = s.words(["The cat found the ball."])[0]
        self.assertIsNotNone(s.hole(words, vecs, ["DET", "DOER", "VERB", "DET", "OBJ"], "OBJ"))
        self.assertIn("OBJ", left)

    def test_a_question_to_answer_map_is_learned_from_labelled_evidence(self):
        s = _structs()
        rows = [("What did the cat find?", ["The man bit the dog.", "The cat found the ball."], 1),
                ("What did the dog chase?", ["The dog chased the cat.", "The man found the ball."], 0),
                ("What did the man find?", ["The dog bit the man.", "The man found the ball."], 1),
                ("What did the cat chase?", ["The cat chased the dog.", "The dog bit the man."], 0)] * 3
        got = s.learn_answers(rows)
        self.assertIn("base_mrr", got)
        self.assertGreaterEqual(got["mrr"], got["base_mrr"] - 1e-9)
        self.assertIsInstance(s.answer_score("What did the cat find?", "The cat found the ball."), float)

    def test_the_binder_learns_which_verb_each_word_belongs_to(self):
        s = _structs()
        for t, labs, cl in [("The dog chased the cat and found the ball.", ["DET", "DOER", "VERB", "DET", "OBJ", "OTHER",
                                                                            "VERB", "DET", "OBJ"], [2, 2, 2, 2, 2, 6, 6, 6, 6]),
                            ("The man found the dog and chased the cat.", ["DET", "DOER", "VERB", "DET", "OBJ", "OTHER",
                                                                           "VERB", "DET", "OBJ"], [2, 2, 2, 2, 2, 6, 6, 6, 6])] * 4:
            s.learn(t, labs, cl)
        got = s.learn_binder()
        self.assertGreater(got["rank"], 0)
        words, vecs = s.words(["The cat chased the dog and found the man."])[0]
        labs = ["DET", "DOER", "VERB", "DET", "OBJ", "OTHER", "VERB", "DET", "OBJ"]
        verb_of = s.bind(words, vecs, labs)
        self.assertEqual(verb_of[4], 2)          # "dog" belongs to "chased"
        self.assertEqual(verb_of[8], 6)          # "man" belongs to "found"

    def test_meaning_spillage_is_fitted_and_taken_out(self):
        rs = np.random.RandomState(0)
        c = np.array([0.0, 0.05, 0.2, 0.6, 0.15, 0.02, 0.0, 0.3])
        pairs = []
        for _ in range(40):
            A = ST._unit(rs.randn(int(rs.randint(2, 12)), 16))
            n = len(A)
            M = np.full((n, n), c[-1] / n)
            for k, o in enumerate(range(-3, 4)):
                for i in range(n):
                    if 0 <= i + o < n:
                        M[i, i + o] += c[k]
            pairs.append((M @ A, A))
        got = ST.fit_spill(pairs)
        self.assertEqual(int(np.argmax(got[:7])), 3)


class Spine(unittest.TestCase):
    def test_facts_go_on_the_world_spine_at_their_moment(self):
        srv = _talk()
        fs = F.clauses(srv.entity_store)
        self.assertTrue(fs)
        self.assertTrue(all(f["timeline"] == F.WORLD for f in fs if not f["possible"]))
        chased = next(f for f in fs if "chased" in f["name"])
        self.assertIsNotNone(chased["span"])               # "yesterday": a day before it was said
        self.assertLess(chased["moment"], chased["said"])

    def test_a_things_thread_is_its_facts_in_moment_order(self):
        srv = _talk()
        th = F.thread(srv.entity_store, "cat")
        self.assertEqual([F.order_key(f) for f in th], sorted(F.order_key(f) for f in th))
        self.assertIn("chased", th[0]["name"])            # said third, about the day before: the earliest

    def test_an_unplaced_time_sorts_before_or_after_its_day(self):
        a, b, c = ({"moment": 5.0, "side": s, "said": 5.0} for s in ("past", None, "future"))
        self.assertEqual(sorted([c, a, b], key=F.order_key), [a, b, c])


class MoveLog(unittest.TestCase):
    def test_the_field_records_how_far_each_event_moved_each_entity(self):
        srv = _talk()
        f = srv.efield
        self.assertTrue(isinstance(f.moves, dict) and isinstance(f.worldlines, dict))


class Puzzle(unittest.TestCase):
    def _pool(self, srv):
        return [d.eid for d in srv.entity_store.docs.values() if d.cls == "Message"]

    def test_when_says_the_facts_period(self):
        srv = _talk()
        got = infer.ask(srv, "", "When did the cat chase the mouse?", pool=self._pool(srv))
        self.assertIn("A cat chased that mouse yesterday.", got[1])

    def test_the_asked_block_is_preferred_never_required(self):
        srv = _talk()
        pool = self._pool(srv)
        got = infer.ask(srv, "", "What did the cat find?", pool=pool)
        self.assertNotEqual(got, ["I don't know"])
        self.assertTrue(infer.LAST["ranking"])

    def test_first_and_now_walk_the_things_thread(self):
        srv = _talk()
        pool = self._pool(srv)
        infer.ask(srv, "", "What did the cat find first?", pool=pool)
        first = infer.LAST["facts"][0]
        infer.ask(srv, "", "What did the cat find now?", pool=pool)
        last = infer.LAST["facts"][0]
        self.assertLessEqual(F.order_key(first), F.order_key(last))
        self.assertNotEqual(first["eid"], last["eid"])

    def test_nothing_to_read_is_not_known(self):
        srv, _runner = _seat()
        srv.structures = _structs()
        self.assertEqual(infer.ask(srv, "", "What did the cat find?", pool=[]), ["I don't know"])


class Reasoning(unittest.TestCase):
    """2026-11-06: a reply is read with the turn it answers; the 🧩 follows a shared thing to the next facts."""

    def _chat(self, lines):
        srv, runner = _seat()
        srv.structures = _structs()
        return srv, [_turn(srv, runner, i, "[user: Sam]\n" + t, "")[0] for i, t in enumerate(lines)]

    def test_a_reply_is_read_with_the_turns_before_it(self):
        srv, ids = self._chat(["Did the cat chase the mouse?", "The cat did it yesterday.", "The dog found the ball."])
        self.assertEqual(infer.context(srv, ids[1], 1), [ids[0]])
        self.assertEqual(infer.context(srv, ids[2], 2), [ids[1], ids[0]])
        self.assertEqual(infer.context(srv, ids[0], 2), [])

    def test_the_thread_counts_the_words_of_the_turn_answered(self):
        srv, ids = self._chat(["The man found the mouse.", "Did the cat chase the mouse?", "The cat did it yesterday."])
        infer.ask(srv, "", "What did the cat chase first?", pool=ids[::-1], prefer=False, context=1)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[2])     # said nowhere but in the turn it answers

    def test_the_next_fact_holds_the_shared_thing_in_the_asked_block(self):
        srv, ids = self._chat(["The cat found the ball.", "The ball chased that mouse.", "The man bit the ball."])
        infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, join=True)
        self.assertEqual(infer.LAST["ranking"][:2], [ids[0], ids[2]])   # the ball done-to again, not its doer

    def test_a_pronoun_takes_the_same_blocks_words_from_the_turn_answered(self):
        srv, ids = self._chat(["The cat chased the ball.", "The dog found it yesterday."])
        fact = next(f for f in F.clauses(srv.entity_store) if f["msg"] == ids[1])
        self.assertEqual(infer.resolved(srv, fact), [("it", ["ball"], "OBJ")])
        said = infer.ask(srv, "", "What did the dog find?", pool=[ids[1]], prefer=False, whole=True, resolve=True)
        self.assertIn("ball", said[0])
        self.assertNotIn(" it ", f" {said[0]} ")


class AnswerCheck(unittest.TestCase):
    """2026-11-06: memspeak checks each candidate fact with yes/no tests (it holds the asked part; the person the
    question names said it or is in it; its time fits the question's own date), passing ones first, ties in the
    pool's order; it can say the asked part only, and join the parts of the facts that pass (up to JOIN_DEPTH)."""

    def _chat(self, lines, who="Sam"):
        srv, runner = _seat()
        srv.structures = _structs()
        ids = []
        for i, t in enumerate(lines):
            w = who[i] if isinstance(who, list) else who
            ids.append(_turn(srv, runner, i, f"[user: {w}]\n" + t, "")[0])
        return srv, ids

    def test_a_fact_holding_the_asked_part_passes_before_one_that_does_not(self):
        srv, ids = self._chat(["The cat chased the dog.", "The cat chased the dog yesterday."])
        infer.ask(srv, "", "When did the cat chase the dog?", pool=ids, prefer=False)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[0])            # no check: the pool's first
        infer.ask(srv, "", "When did the cat chase the dog?", pool=ids, prefer=False, check=True)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])            # the one that says a time

    def test_the_named_person_passes_before_another(self):
        srv, ids = self._chat(["The cat found the ball.", "The cat found the mouse."], who=["Ann", "Sam"])
        infer.ask(srv, "", "What did Sam find?", pool=ids, prefer=False, check=True)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])

    def test_the_fact_whose_asked_part_means_what_the_question_says_passes(self):
        srv, ids = self._chat(["The cat found the mouse.", "The cat found the ball."])
        infer.ask(srv, "", "What did the cat find near the ball?", pool=ids, prefer=False, check=True)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])            # its done-to sits nearest the question

    def test_within_a_message_the_sentence_nearest_the_question_by_meaning_is_said(self):
        srv, ids = self._chat(["The cat found the ball. The dog bit the man."])
        dog = next(f for f in F.clauses(srv.entity_store) if "dog" in f["name"])
        orig, infer._near = infer._near, lambda st, q, text, facts, hole=False: {id(f): (1.0 if f["eid"] == dog["eid"] else 0.0)
                                                                     for f in facts}
        try:
            said = infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, check=True, pick="meaning")
        finally:
            infer._near = orig
        self.assertIn("dog", said[0])                                      # by meaning, not by shared words

    def test_learned_only_takes_the_learned_asked_part_over_the_question_word_table(self):
        srv, _ids = self._chat(["The cat found the ball."])
        srv.structures.learn_asked(["WH_ADV", "OTHER", "DET", "DOER", "VERB", "DET", "OBJ"], "OBJ",
                                   ["when", "did", "the", "cat", "chase", "the", "dog"])
        self.assertEqual(infer.read_question(srv, "When did the cat chase the dog?")["asked"], "TIME")   # the table
        self.assertEqual(infer.read_question(srv, "When did the cat chase the dog?", learned_only=True)["asked"], "OBJ")

    def test_with_the_check_latest_walks_the_passing_candidates_not_the_whole_talk(self):
        srv, ids = self._chat(["The cat found the ball.", "The cat found the mouse."])
        infer.ask(srv, "", "What did the cat find now?", pool=[ids[0]], prefer=False, check=True)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[0])            # the later mouse is not a candidate
        infer.ask(srv, "", "What did the cat find now?", pool=[ids[0]], prefer=False)
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])            # without it: the whole talk's latest

    def test_it_can_say_the_facts_own_clause(self):
        srv, ids = self._chat(["The cat found the ball yesterday."])
        fact = next(f for f in F.clauses(srv.entity_store) if f["msg"] == ids[0])
        said = infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, check=True, unit="clause")
        self.assertEqual(said[0], " ".join(w for w, _l in fact["words"]))

    def test_the_span_is_the_asked_run_nearest_the_question_across_the_sentence(self):
        pairs = [("The", "DET"), ("cat", "DOER"), ("found", "VERB"), ("the", "DET"), ("ball", "OBJ"), ("and", "OTHER"),
                 ("the", "DET"), ("mouse", "OBJ")]
        self.assertEqual(infer.runs_of(pairs, ("OBJ",)), [[3, 4], [6, 7]])
        self.assertEqual(infer.runs_of(pairs, ("PLACE",)), [])
        orig, infer._run_score = infer._run_score, lambda st, q, text, words, idx: 1.0 if "mouse" in words[idx[-1]] else 0.0
        try:
            got = infer.speak_best(None, {"asked": "OBJ", "words": []}, "q", [{"words": [list(p) for p in pairs],
                                                                             "text": "The cat found the ball and the mouse."}])
        finally:
            infer._run_score = orig
        self.assertEqual(got, "the mouse")
        none_asked = infer.speak_best(None, {"asked": "PLACE", "words": []}, "q", [{"words": [list(p) for p in pairs],
                                                                                  "text": "x"}])
        self.assertTrue(none_asked)                                       # falls back to the content runs

    def test_i_in_a_question_names_the_asker_when_one_is_given(self):
        srv, ids = self._chat(["The cat found the ball.", "The cat found the mouse."], who=["Ann", "Sam"])
        srv.structures.learn("I found the ball.", ["SELF", "VERB", "DET", "OBJ"], [0] * 4)      # the reader's self part
        srv.structures.learn("What did I find?", ["WH_O", "OTHER", "SELF", "VERB"], [0] * 4)
        infer.ask(srv, "", "What did I find?", pool=ids, prefer=False, check=True, asker="Sam")
        self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])           # Sam asks: Sam's own line passes

    def test_understanding_at_hearing_time_stores_facts_with_their_pronouns_resolved(self):
        srv, ids = self._chat(["The cat chased the ball.", "The dog found it yesterday."])
        n = infer.resolve_facts(srv)
        self.assertEqual(n, 1)
        fact = next(f for f in F.clauses(srv.entity_store) if f["msg"] == ids[1])
        self.assertIn("ball", [w for w, _l in fact["words"]])
        self.assertNotIn("it", [w.lower() for w, _l in fact["words"]])
        self.assertIn("ball", fact["text"])
        self.assertEqual(fact["said_as"], "The dog found it yesterday.")          # what was said, kept
        self.assertEqual(infer.resolve_facts(srv), 0)                              # once

    def test_the_binding_test_puts_the_clause_binding_the_question_to_a_new_filler_first(self):
        srv, ids = self._chat(["Did the cat chase the ball?", "The cat chased the ball yesterday.", "The dog chased the ball."])
        got = infer.binding(srv, "When did the cat chase the ball?", F.clauses(srv.entity_store))
        best = max(got, key=lambda eid: got[eid])
        msg = next(f["msg"] for f in F.clauses(srv.entity_store) if f["eid"] == best)
        self.assertEqual(msg, ids[1])                                   # its things, and a time the question lacks
        echo = next(f["eid"] for f in F.clauses(srv.entity_store) if f["msg"] == ids[0])
        self.assertLess(got[echo], got[best])                           # the echo of the question fails "new"

    def test_speaking_on_facts_ignores_the_messages_order(self):
        srv, ids = self._chat(["The cat found the ball.", "The cat found the mouse."])
        mouse = next(f for f in F.clauses(srv.entity_store) if f["msg"] == ids[1])
        orig, infer._near = infer._near, lambda st, q, text, facts, hole=False: {id(f): (1.0 if f["eid"] == mouse["eid"] else 0.5)
                                                                                 for f in facts}
        try:
            infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, pick="meaning")
            self.assertEqual(infer.LAST["facts"][0]["msg"], ids[0])     # message order first: the first message's fact
            infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, pick="meaning", order="facts")
            self.assertEqual(infer.LAST["facts"][0]["msg"], ids[1])     # facts alone: the better fact, wherever it is
        finally:
            infer._near = orig

    def test_only_a_date_of_its_own_checks_time(self):
        self.assertIsNone(infer.own_span("yesterday"))                     # relative: depends on when it's asked
        got = infer.own_span("July 2023")
        self.assertIsNotNone(got)
        self.assertTrue(infer.time_fits(got, {"moment": got[0] + 86400}))
        self.assertFalse(infer.time_fits(got, {"moment": got[0] - 86400 * 40}))

    def test_it_can_say_the_asked_part_only(self):
        srv, ids = self._chat(["The cat found the ball."])
        said = infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, check=True, part=True)
        self.assertIn("ball", said[0])
        self.assertNotIn("cat", said[0].lower())

    def test_the_parts_of_the_passing_facts_are_joined(self):
        srv, ids = self._chat(["The cat found the ball.", "The cat found the mouse."])
        orig, infer._meaning = infer._meaning, lambda st, q, text, facts, sims=None: {id(f) for f in facts}   # joining alone
        try:
            said = infer.ask(srv, "", "What did the cat find?", pool=ids, prefer=False, check=True, part=True, fuse=True)
        finally:
            infer._meaning = orig
        self.assertIn("ball", said[0])
        self.assertIn("mouse", said[0])


if __name__ == "__main__":
    unittest.main()
