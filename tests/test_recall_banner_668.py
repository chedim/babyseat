"""#668 PR A: the banner is recall -- a learned pointwise scorer over the pool,
one fact line per row, the chat's own recent turns skipped, near-duplicates
collapsed, a no-topic message keeping the previous set."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def field_with(texts, session="s:1:a"):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    evs = [ST.Event(stream="discourse", role="user" if i % 2 == 0 else "model",
                    ts=T0 + i * 10, text=t) for i, t in enumerate(texts)]
    f.write_events(evs, session=session)
    f.process()
    return f


class TestTopic(unittest.TestCase):
    def test_no_topic_messages(self):
        for t in ("ok", "1. yes", "thanks!", "  "):
            self.assertFalse(RB.has_topic(t), t)

    def test_topic_messages(self):
        self.assertTrue(RB.has_topic("what did Caroline adopt last spring?"))
        self.assertTrue(RB.has_topic("membrane/router.py fallback"))


class TestScorer(unittest.TestCase):
    def rows(self):
        return [{"sim": 0.9, "answer": ["The sky is blue."], "a_n": [1], "q_n": 1},
                {"sim": 0.8, "answer": ["Caroline adopted a dog named Max."], "a_n": [5], "q_n": 5}]

    def test_starting_weights_are_cosine_only(self):
        self.assertEqual(RB.DEFAULT_WEIGHTS["sim"], 1.0)
        self.assertTrue(all(v == 0.0 for k, v in RB.DEFAULT_WEIGHTS.items() if k not in ("sim", "bias")))
        got = RB.rank(self.rows(), "what did Caroline adopt?")
        self.assertEqual([r["a_n"][0] for r in got], [1, 5])

    def test_weights_come_from_the_field_entity(self):
        f = field_with(["hello there friend"])
        self.assertEqual(RB.load_weights(f), RB.DEFAULT_WEIGHTS)
        RB.save_weights(f, {**RB.DEFAULT_WEIGHTS, "overlap": 5.0})
        self.assertEqual(RB.load_weights(f)["overlap"], 5.0)
        got = RB.rank(self.rows(), "what did Caroline adopt?", RB.load_weights(f))
        self.assertEqual(got[0]["a_n"][0], 5)       # word overlap now outweighs raw cosine

    def test_speaker_match_feature(self):
        rows = [{"sim": 0.8, "answer": ["Melanie: I painted a lake."], "a_n": [1], "q_n": 1},
                {"sim": 0.8, "answer": ["Caroline: I painted a lake."], "a_n": [2], "q_n": 2}]
        w = {**RB.DEFAULT_WEIGHTS, "speaker": 2.0}
        self.assertEqual(RB.rank(rows, "what did Caroline paint?", w)[0]["a_n"][0], 2)


class TestFactLine(unittest.TestCase):
    def test_the_sentence_nearest_the_message(self):
        r = {"answer": ["We talked about lunch. Caroline adopted a dog named Max. Then it rained."]}
        self.assertEqual(RB.fact_line(r, "which dog did Caroline adopt?"),
                         "Caroline adopted a dog named Max.")

    def test_clipped(self):
        self.assertLessEqual(len(RB.fact_line({"answer": ["word " * 200]}, "word")), 200)


class TestBanner(unittest.TestCase):
    def test_near_duplicates_collapse(self):
        rows = [{"sim": 0.9, "answer": ["Caroline adopted a dog named Max."], "a_n": [1], "q_n": 1},
                {"sim": 0.85, "answer": ["Caroline adopted a dog named Max!"], "a_n": [2], "q_n": 2},
                {"sim": 0.5, "answer": ["Melanie likes pottery."], "a_n": [3], "q_n": 3}]
        out = RB.pick(rows, "Caroline dog", n=12)
        self.assertEqual([r["a_n"][0] for r in out], [1, 3])

    def test_n_caps_the_banner(self):
        rows = [{"sim": 1 - i / 100, "answer": [f"distinct fact number {i} about topic{i}."],
                 "a_n": [i], "q_n": i} for i in range(30)]
        self.assertEqual(len(RB.pick(rows, "fact", n=12)), 12)
        self.assertEqual(RB.BANNER_N, 12)
        self.assertEqual(RB.POOL, 50)
        self.assertEqual(RB.NO_TOPIC_MIN, 0.3)

    def test_current_chats_recent_turns_are_skipped(self):
        f = field_with(["The deploy key lives in the vault.", "Noted, the deploy key is in the vault.",
                        "Where do we keep the deploy key?"], session="s:1:chat")
        recent = RB.recent_ns(f, "s:1:chat", n=8)
        self.assertTrue(recent)
        rows = [{"sim": 0.9, "answer": ["x"], "a_n": [min(recent)], "q_n": min(recent)},
                {"sim": 0.5, "answer": ["y"], "a_n": [9999], "q_n": 9999}]
        self.assertEqual([r["a_n"][0] for r in RB.pick(rows, "deploy key", skip=recent)], [9999])

    def test_build_finds_the_fact_and_no_topic_keeps_previous(self):
        f = field_with(["tell me about pets", "Caroline adopted a dog named Max in May.",
                        "and the weather?", "unrelated chatter about weather"], session="s:1:old")
        lines, state = RB.build(f, "Caroline adopted which dog?", session="s:1:new", prev=None, sim_min=0)
        self.assertTrue(any("Max" in ln for ln in lines))
        self.assertTrue(lines)
        again, state2 = RB.build(f, "ok", session="s:1:new", prev=state)
        self.assertEqual(again, lines)
        self.assertEqual(state2, state)

    def test_render(self):
        txt = RB.render(["▸ #1 (1h ago) user: a fact."])
        self.assertTrue(txt.startswith(RB.HEADER))
        self.assertIsNone(RB.render([]))


class TestInTheBanner(unittest.TestCase):
    def test_recall_lines_ride_the_banner_after_the_chat_block(self):
        from membrane import conv_banner as CB
        out = CB.render({"working": CB.Item("fix it", "ask", T0), "recall": ["▸ #3 (1h ago, reply) a fact."]},
                        now=T0 + 60)
        self.assertIn(RB.HEADER, out)
        self.assertLess(out.index(CB.HEADER), out.index(RB.HEADER))
        self.assertTrue(out.rstrip().endswith("▸ #3 (1h ago, reply) a fact."))

    def test_recall_alone_makes_a_block(self):
        from membrane import conv_banner as CB
        self.assertIn("a fact", CB.render({"recall": ["▸ #3 a fact"]}, now=T0))
        self.assertIsNone(CB.render({"recall": []}, now=T0))


if __name__ == "__main__":
    unittest.main()
