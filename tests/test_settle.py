"""Settling recall (2026-11-06): the question's things cue the episodes that moved them; an episode also holds what
the turn before it moved (neighbors in time); and the cued episodes' other things cue again (completion), so an
episode that shares no word with the question is reached through one that does."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MEMBRANE_STORE", "json")

from membrane import efield as EF  # noqa: E402
from membrane import settle as SE  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class AliasEmbedder(FakeEmbedder):
    """The fake embedder, with some words meaning another (a bare term embeds as its alias)."""

    def __init__(self, alias):
        super().__init__()
        self.alias = alias

    def embed(self, texts):
        return super().embed([self.alias.get(str(t).lower(), t) for t in texts])


def talk(texts, emb=None):
    old, EF.K_MAT = EF.K_MAT, 1                  # every word tracked from its first mention (the bench's setting)
    try:
        f = EF.EntityField(MemBackend(), emb or FakeEmbedder(), "test@host")
        evs = [ST.Event(stream="discourse", role="user", text=t, ts=1000.0 + i) for i, t in enumerate(texts)]
        f.write_events(evs, session="s1")
        f.process()
    finally:
        EF.K_MAT = old
    return f, [f.event_key(e, "s1") for e in evs]


class Settle(unittest.TestCase):
    def test_each_event_is_an_episode_in_its_chats_order(self):
        f, keys = talk(["The hike was long.", "The lake was cold.", "The tent leaked."])
        self.assertEqual([f.episodes[k] for k in keys], [("s1", 0), ("s1", 1), ("s1", 2)])

    def test_an_episode_holds_what_the_turn_before_it_moved(self):
        f, keys = talk(["Did you go on the hike?", "We did it to relax.", "We went to relax at the spa."])
        top = [k for k, _s in SE.settle(f, "the hike to relax", rounds=1, neighbors=True)]
        self.assertEqual(top[0], keys[1])                  # relax, and the hike it answers

    def test_completion_reaches_an_episode_through_the_things_of_a_cued_one(self):
        f, keys = talk(["I love counseling and therapy.", "Therapy changed my career.", "The dog likes the park."])
        once = dict(SE.settle(f, "career", rounds=1, neighbors=False))
        twice = [k for k, _s in SE.settle(f, "career", rounds=2, neighbors=False)]
        self.assertNotIn(keys[0], once)                    # says nothing the question says
        self.assertEqual(twice[:2], [keys[1], keys[0]])    # reached through therapy
        self.assertNotIn(keys[2], twice[:2])

    def test_a_word_the_talk_never_said_cues_the_thing_its_seed_sits_nearest(self):
        f, keys = talk(["I took a pottery class.", "The dog likes the park."], AliasEmbedder({"pastime": "pottery"}))
        self.assertEqual(SE.settle(f, "pastime", rounds=1, neighbors=False), [])
        top = [k for k, _s in SE.settle(f, "pastime", rounds=1, neighbors=False, seeds=True)]
        self.assertEqual(top[:1], [keys[0]])

    def test_a_message_moves_a_thing_by_its_largest_sentence_not_their_sum(self):
        reply = ("Emma sang a lovely song at the concert tonight.\n\nEmma danced with everyone at the party "
                 "afterwards.\n\nEmma left early to catch the last train home.")
        f, keys = talk([reply, "Emma graduated.", "The dog likes the park."])
        steps = [s for _ts, k, s in f.worldlines["w:emma"] if k == keys[0]]
        self.assertGreater(len(steps), 1)
        summed = dict(SE.settle(f, "emma", rounds=1, neighbors=False))
        largest = dict(SE.settle(f, "emma", rounds=1, neighbors=False, move="max"))
        self.assertAlmostEqual(summed[keys[0]] / summed[keys[1]], sum(steps) / f.moves[keys[1]]["w:emma"], places=4)
        self.assertAlmostEqual(largest[keys[0]] / largest[keys[1]], max(steps) / f.moves[keys[1]]["w:emma"], places=4)

    def test_the_question_is_explained_best_by_the_message_whose_things_it_is_made_of(self):
        f, keys = talk(["The cat chased the ball.", "The dog slept all day."])
        got = SE.explain(f, "Did the cat chase the ball?", keys)
        self.assertLess(got[keys[0]], got[keys[1]])                     # less left unexplained

    def test_the_fair_probe_fits_every_message_with_as_many_things_as_the_question_names(self):
        f, keys = talk(["The cat chased the ball.", "The dog slept all day long in the warm sun by the old barn door."])
        fair = SE.explain(f, "Did the cat chase the ball?", keys, fair=True)
        self.assertLess(fair[keys[0]], fair[keys[1]])
        m = len([e for e in SE.cues(f, "Did the cat chase the ball?") if e in {x for mv in f.moves.values() for x in mv}])
        self.assertGreater(m, 0)

    def test_a_fact_explains_the_question_from_the_things_its_own_words_name(self):
        f, _keys = talk(["The cat chased the ball.", "The dog slept all day."])
        got = SE.explain_texts(f, "Did the cat chase the ball?", {"a": "the cat chased the ball", "b": "the dog slept"})
        self.assertLess(got["a"], got["b"])

    def test_one_round_is_the_default(self):
        self.assertEqual(SE.ROUNDS, 1)


if __name__ == "__main__":
    unittest.main()
