"""0.7.52: the best recall stack of 2026-11-07 (the fitted vote) in the product's ranking path.

Five voters, Borda over each one's top 20 (points w x (20 - i), voters in alphabetical order, stable sort), the
weights the mean of the two held-out folds (learned map dropped: no training ships), approved by the operator
2026-11-11:
  embedder            the direct lane's own top 20
  entity recall       the field things nearest the question, then the events that moved them most
  instance histories  the tense rule's instances (spaCy, parsed once at capture) Borda'd with the person / place
                      ballots and current recall
  move log            settling over the move log (how far each event moved each thing; the turn before it)
  word lane           the word lane's own top 20
The field is one field: every chat, every person, tool outputs -- the vote ranks the whole pool."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

try:
    import spacy  # noqa: F401
    spacy.load("en_core_web_sm")
    HAVE_SPACY = True
except Exception:
    HAVE_SPACY = False


def _field(be=None):
    return EF.EntityField(be or MemBackend(), FakeEmbedder(), "seat@host")


def _say(f, session, lines, t0=1_700_000_000.0):
    """[(role, text)] as one chat; returns the event keys."""
    evs = [ST.Event(stream="discourse", role=r, text=t, ts=t0 + i * 60) for i, (r, t) in enumerate(lines)]
    keys = f.write_events(evs, session=session)
    f.process()
    return keys


def _n(f, key):
    return int(f.b.get("events", key)["n"])


class Borda(unittest.TestCase):
    def test_points_order_and_ties_as_the_bench(self):
        from membrane import recall_vote as RV
        # alphabetical voters: a first; points w x (20 - i)
        got = RV.borda({"b": [1, 2], "a": [2, 3]}, {"a": 1.0, "b": 1.0})
        self.assertEqual(got, [2, 1, 3])
        # a tie keeps first-seen order (stable sort over insertion order)
        self.assertEqual(RV.borda({"a": [5], "b": [6]}, {"a": 1.0, "b": 1.0}), [5, 6])
        # weight 0 adds nothing but the voter's items still enter (as the bench's dict did)
        self.assertEqual(RV.borda({"a": [7], "b": [8]}, {"a": 0.0, "b": 1.0}), [8, 7])

    def test_only_the_top_20_count(self):
        from membrane import recall_vote as RV
        got = RV.borda({"a": list(range(30))}, {"a": 1.0})
        self.assertEqual(got, list(range(20)))

    def test_the_approved_weights(self):
        from membrane import recall_vote as RV
        self.assertEqual(RV.VOTE_WEIGHTS, {"embedder": 0.125, "entity recall": 0.75, "instance histories": 3.0,
                                           "move log": 2.0, "word lane": 0.125})
        self.assertEqual(RV.DEPTH, 20)


class MoveLog(unittest.TestCase):
    def test_capture_records_how_far_each_event_moved_each_thing(self):
        f = _field()
        k = _say(f, "chat-a", [("user", "I started a pottery class with Mia on Tuesday.")])[0]
        ml = f.move_log
        self.assertIn(k, ml.moves)
        self.assertTrue(ml.moves[k])
        self.assertTrue(all(v > 0 for v in ml.moves[k].values()))
        self.assertEqual(ml.chat_of(k), "chat-a")

    def test_it_survives_a_restart_without_a_replay(self):
        be = MemBackend()
        f = _field(be)
        k = _say(f, "chat-a", [("user", "I started a pottery class with Mia on Tuesday.")])[0]
        want = dict(f.move_log.moves[k])
        f.flush()
        g = _field(be)                                   # a restart: nothing replayed
        g.move_log.load(g)
        self.assertEqual(set(g.move_log.moves[k]), set(want))
        for e, v in want.items():
            self.assertAlmostEqual(g.move_log.moves[k][e], v, places=4)

    def test_settle_finds_the_event_that_moved_the_rare_thing(self):
        from membrane import movelog as ML
        f = _field()
        keys = _say(f, "chat-a", [("user", "The weather was nice today."),
                                  ("user", "I started a pottery class on Tuesday."),
                                  ("user", "The weather was cold yesterday.")])
        got = [k for k, _s in ML.settle(f, "What class did I start?")]
        self.assertTrue(got)
        self.assertEqual(got[0], keys[1])

    def test_a_reply_holds_the_turn_it_answers(self):
        from membrane import movelog as ML
        f = _field()
        keys = _say(f, "chat-a", [("user", "Tell me about the pottery class."),
                                  ("model", "It meets every week and it is fun.")])
        held = ML.held(f.move_log)
        self.assertTrue(set(f.move_log.moves[keys[0]]) <= set(held[keys[1]]))


class Backfill(unittest.TestCase):
    def test_events_from_before_the_move_log_get_their_moves_once(self):
        from membrane import movelog as ML
        be = MemBackend()
        f = _field(be)
        k = _say(f, "chat-a", [("user", "I started a pottery class with Mia on Tuesday.")])[0]
        f.flush()
        for dk, d in list(be.scan("entity_deltas", {"origin": f.origin, "mv": 1})):   # as 0.7.51 wrote it
            be.upsert("entity_deltas", dk, {x: v for x, v in d.items() if x not in ("mv", "moves", "chat")})
        g = _field(be)
        ml = ML.of(g)
        self.assertEqual(ml.load(g), 0)
        self.assertEqual(ml.backfill(g), 1)
        self.assertTrue(ml.moves[k])
        self.assertEqual(ml.chat_of(k), "chat-a")
        h = _field(be)                                        # kept: the next boot loads, nothing to backfill
        self.assertEqual(ML.of(h).load(h), 1)
        self.assertEqual(ML.of(h).backfill(h), 0)


class EntityRecall(unittest.TestCase):
    def test_events_that_moved_the_nearest_things(self):
        from membrane import recall_vote as RV
        f = _field()
        keys = _say(f, "chat-a", [("user", "The kubernetes cluster runs on port 8443."),
                                  ("user", "I like tea.")])
        got = RV.entity_recall(f, "which port does the kubernetes cluster use?")
        self.assertIn(_n(f, keys[0]), got)


@unittest.skipUnless(HAVE_SPACY, "spaCy and en_core_web_sm not installed")
class TenseRule(unittest.TestCase):
    def _phr(self, text):
        from membrane import instances as IN
        return IN.phrases(IN.nlp()(text))

    def test_a_past_clause_starts_an_instance_a_generic_one_is_the_class(self):
        new = {p[0][0]: p[2] for p in self._phr("I adopted a dog last week.")}
        self.assertEqual(new.get("dog"), "new")
        gen = {p[0][0]: p[2] for p in self._phr("Dogs are loyal animals.")}
        self.assertIsNone(gen.get("dog"))

    def test_owners_numbers_and_definites_refer(self):
        for text in ("My dog is called Rex.", "The dog is called Rex.", "We have two dogs."):
            got = {p[0][0]: p[2] for p in self._phr(text)}
            self.assertEqual(got.get("dog"), "refers", text)

    def test_the_ladder_and_the_owner(self):
        (ladder, own, _part, mods), *_ = [p for p in self._phr("My cousin's beach wedding was lovely.")
                                          if p[0][0] == "wedding"]
        self.assertEqual(ladder, ["wedding", "beach wedding"])
        self.assertEqual(own, "@1's cousin")
        self.assertEqual(mods, ["beach"])


@unittest.skipUnless(HAVE_SPACY, "spaCy and en_core_web_sm not installed")
class Instances(unittest.TestCase):
    def test_parsed_once_at_capture_and_kept_across_a_restart(self):
        from membrane import instances as IN
        be = MemBackend()
        f = _field(be)
        IN.attach(f)
        _say(f, "chat-a", [("user", "I adopted a dog last week."), ("user", "The dog is called Rex.")])
        IN.drain(f)
        store = IN.store_of(f)
        self.assertEqual(len(store.rows), 2)
        nlp = IN.nlp()
        with mock.patch.object(nlp, "pipe", side_effect=AssertionError("the field re-parsed at ask time")):
            IN.ballot(f, "What is the dog called?")          # ask time: the question only, never the field
        g = _field(be)                                         # restart: the rows load, nothing re-parsed
        calls = []
        real = nlp.pipe
        with mock.patch.object(nlp, "pipe", side_effect=lambda *a, **kw: calls.append(1) or real(*a, **kw)):
            IN.attach(g)
            IN.drain(g)
        self.assertEqual(len(IN.store_of(g).rows), 2)
        self.assertEqual(calls, [])

    def test_events_from_before_the_parser_are_read_once_in_the_background(self):
        from membrane import instances as IN
        f = _field()
        _say(f, "chat-a", [("user", "I adopted a dog last week.")])     # no parser attached yet
        IN.attach(f)
        IN.drain(f)
        self.assertEqual(len(IN.store_of(f).rows), 1)

    def test_the_dog_joins_the_dog_that_was_adopted(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        keys = _say(f, "chat-a", [("user", "I adopted a dog last week."),
                                  ("user", "We went to the beach yesterday."),
                                  ("user", "The dog is called Rex.")])
        IN.drain(f)
        line = [it["line"] for it in IN.instances(f).values() if it["ladder"][0] == "dog"]
        self.assertEqual(line, [[_n(f, keys[0]), _n(f, keys[2])]])


@unittest.skipUnless(HAVE_SPACY, "spaCy and en_core_web_sm not installed")
class TheVoteOverTheWholeField(unittest.TestCase):
    def _ranked(self, f, q):
        from membrane import recall_banner as RB
        from membrane import instances as IN
        from membrane.entity_block import recall_answers
        IN.drain(f)
        pool = recall_answers(f, q, k=RB.POOL, sim_min=0.0)
        with mock.patch("membrane.recall_rerank.scores", return_value=None):
            return RB.rank(RB.with_speakers(f, pool), q, RB.load_weights(f), field=f)

    def test_evidence_in_another_chat(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        ev = _say(f, "chat-a", [("user", "I adopted a dog named Rex last week."),
                                ("model", "Congratulations on the new dog!")])
        _say(f, "chat-b", [("user", "The weather is nice."), ("user", "Lunch was pasta.")], t0=1_700_100_000.0)
        got = [r["a_n"][0] for r in self._ranked(f, "What is the name of the dog I adopted?")]
        self.assertIn(_n(f, ev[0]), got[:3])

    def test_evidence_in_a_tool_output(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        ev = f.write_events([ST.Event(stream="observation", tool="shell", args={"command": "cat config"},
                                      result_head="The deploy listens on port 8443 behind nginx.",
                                      outcome="ok", ts=1_700_000_000.0, call_id="c-1")], session="chat-tools")
        f.process()
        _say(f, "chat-b", [("user", "The weather is nice."), ("user", "Lunch was pasta.")], t0=1_700_100_000.0)
        got = [r["a_n"][0] for r in self._ranked(f, "Which port does the deploy listen on?")]
        self.assertIn(_n(f, ev[0]), got[:3])

    def test_vote_rows_carry_the_vote(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        _say(f, "chat-a", [("user", "I adopted a dog named Rex last week.")])
        got = self._ranked(f, "What is the name of the dog I adopted?")
        self.assertTrue(got)
        self.assertIn("vote", got[0])


class Fallbacks(unittest.TestCase):
    def test_without_spacy_the_vote_runs_without_instance_histories(self):
        from membrane import instances as IN
        from membrane import recall_vote as RV
        f = _field()
        _say(f, "chat-a", [("user", "I started a pottery class on Tuesday.")])
        with mock.patch.object(IN, "nlp", return_value=None):
            lists = RV.ballots(f, [], "What class did I start?")
        self.assertNotIn("instance histories", lists)
        self.assertIn("move log", lists)

    def test_a_voter_that_fails_never_breaks_recall(self):
        from membrane import recall_vote as RV
        from membrane import recall_banner as RB
        from membrane.entity_block import recall_answers
        f = _field()
        _say(f, "chat-a", [("user", "I started a pottery class on Tuesday.")])
        pool = recall_answers(f, "What class did I start?", k=RB.POOL, sim_min=0.0)
        with mock.patch.object(RV, "entity_recall", side_effect=RuntimeError("boom")), \
                mock.patch("membrane.recall_rerank.scores", return_value=None):
            got = RB.rank(pool, "What class did I start?", field=f)
        self.assertEqual(len(got), len(pool))

    def test_off_switch_keeps_the_old_order(self):
        from membrane import recall_banner as RB
        from membrane.entity_block import recall_answers
        f = _field()
        _say(f, "chat-a", [("user", "I started a pottery class on Tuesday."), ("user", "Tea is nice.")])
        pool = recall_answers(f, "What class did I start?", k=RB.POOL, sim_min=0.0)
        with mock.patch.dict(os.environ, {"MEMBRANE_VOTE": "0"}), \
                mock.patch("membrane.recall_rerank.scores", return_value=None):
            a = [r["a_n"] for r in RB.rank(pool, "What class did I start?", field=f)]
            b = [r["a_n"] for r in RB.rank(pool, "What class did I start?")]
        self.assertEqual(a, b)


class Wiring(unittest.TestCase):
    def test_every_ranking_caller_passes_the_field(self):
        src = (Path(__file__).parent.parent / "membrane" / "lanes.py").read_text()
        self.assertIn("RB.rank(RB.with_speakers(fld, pool), q, RB.load_weights(fld), field=fld", src)
        rb = (Path(__file__).parent.parent / "membrane" / "recall_banner.py").read_text()
        self.assertIn("ranked = rank(spk, text, load_weights(field), field=field", rb)

    def test_the_seat_warms_the_vote_after_the_field(self):
        src = (Path(__file__).parent.parent / "membrane" / "opencode.py").read_text()
        self.assertLess(src.index("_LX.warm(srv.efield)"), src.index("recall_vote.warm("))


if __name__ == "__main__":
    unittest.main()
