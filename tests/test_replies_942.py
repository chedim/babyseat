"""#942 stage 1: per-person reply models, "about X" counts and a failure ledger (membrane/replies.py), and their
three recall-banner features (listener, said, about).

membrane sits between the speakers: each message is counted as the reply to the message before it in its chat, into
its speaker's table and everyone's; a person's table backs off to everyone's (the shared count is the prior worth one
sample). Before counting, membrane predicts the message and writes down which model was right (the ledger); misses
are kept, never pruned. Dreams (predictions) never change the counts."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import replies as RP  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

TALK = [("Ann", "where did you go camping"), ("Bob", "we went camping at the lake"),
        ("Ann", "where was the party"), ("Bob", "the party was at the lake house"),
        ("Bob", "where is the lake"), ("Ann", "no idea honestly"),
        ("Ann", "where did you hike"), ("Bob", "we hiked at the lake trail")]


# everyday talk in another chat, so the toy's content words are rarer than the talk's typical word (keywords),
# as they are on a real seat
FILLER = [(("Ann", "Bob")[i % 2], f"i think it is a good day and you know the {w} was fine for me")
          for i, w in enumerate("game film book song walk lunch bus train tea class show park call nap ride meal "
                                "news shop show trip".split() * 2)]


def replies(talk=TALK, session="chat-1"):
    R = RP.Replies()
    for who, text in FILLER:
        R.add("chat-0", who, text)
    for who, text in talk:
        R.add(session, who, text)
    return R


class TestTables(unittest.TestCase):
    def test_each_message_is_counted_as_the_reply_to_the_one_before_into_its_speakers_table(self):
        R = replies()
        self.assertEqual(sorted(R.people), ["Ann", "Bob"])
        self.assertEqual(R.count("where", "lake", "Bob"), 3)          # Bob answered "where" with "lake" 3 times
        self.assertEqual(R.count("where", "lake", "Ann"), 0)
        self.assertEqual(R.count("where", "lake"), 3)                 # everyone's table

    def test_chats_do_not_run_into_each_other(self):
        R = RP.Replies()
        R.add("chat-1", "Ann", "where is the lake")
        R.add("chat-2", "Bob", "the lake is north")                    # first message of another chat: no reply pair
        self.assertEqual(R.count("where", "north"), 0)


class TestScores(unittest.TestCase):
    def test_the_repliers_model_prefers_their_kind_of_answer(self):
        R = replies()
        q = "where did Bob go"
        self.assertGreater(R.listener(q, "Bob", "we hiked at the lake trail"), R.listener(q, "Bob", "where is the lake"))
        self.assertGreater(R.said(q, "Bob", "we went to the lake"), R.said(q, "Ann", "we went to the lake"))

    def test_scores_are_positive_lifts_and_zero_without_words(self):
        R = replies()
        self.assertEqual(R.listener("", "Bob", "lake"), 0.0)
        self.assertEqual(R.said("where", "Bob", ""), 0.0)
        self.assertGreaterEqual(R.listener("zzz unknown", "Bob", "qqq"), 0.0)

    def test_about_x_is_what_gets_said_where_the_thing_is_named(self):
        filler = [("Ann", "the game was fun"), ("Bob", "we cooked pasta"), ("Ann", "the train was late"),
                  ("Bob", "my bike broke"), ("Ann", "the movie was long"), ("Bob", "we painted the fence")]
        R = replies(filler + [("Ann", "my mom visited the hospital"), ("Bob", "how is your mom"),
                              ("Ann", "mom is home from the hospital now"), ("Bob", "the weather is nice")])
        self.assertGreater(R.about("how is Ann's mom", "she left the hospital"), R.about("how is Ann's mom", "nice weather"))


class TestLedger(unittest.TestCase):
    def test_every_prediction_is_recorded_hits_and_misses_alike(self):
        R = replies()
        # a prediction needs something else the speaker said to rank against: every reply but Bob's first
        seq = [("chat-0", w) for w, _t in FILLER] + [("chat-1", w) for w, _t in TALK]
        predicted = sum(1 for i, (c, w) in enumerate(seq)
                        if i > 0 and seq[i - 1][0] == c and any(x == w for _c, x in seq[:i]))
        steps = R.ledger.c[()][4]
        self.assertEqual(steps, predicted)                           # one record per prediction, misses included
        self.assertLess(R.ledger.c[()][1], steps)                    # ... and some of them are misses, kept

    def test_the_record_weighs_the_two_models(self):
        R = RP.Replies()
        for _ in range(6):
            R.ledger.add("Bob", False, (False, True, True, False))
        rs, rp = R.ledger.rates("Bob", False, "listen")
        self.assertGreater(rp, rs)
        rs2, rp2 = R.ledger.rates("Carol", False, "listen")          # unknown person: backs off to everyone's record
        self.assertGreater(rp2, rs2)
        ss, sp = R.ledger.rates("Bob", False, "say")                  # said has its own record (Memo's review, 7)
        self.assertGreater(ss, sp)


class TestDreams(unittest.TestCase):
    def test_dreaming_changes_no_counts(self):
        R = replies(TALK[:-1])                                        # Ann just asked "where did you hike"
        keys = [("where", "lake", None), ("where", "lake", "Bob"), ("did", "we", None)]
        before = ([R.count(a, b, w) for a, b, w in keys], R.n, dict(R.ledger.c))
        got = R.dream("chat-1", "Bob", ["we went to the lake", "where is the party", "no idea"])
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0][1], "we went to the lake")           # best first
        self.assertEqual(before, ([R.count(a, b, w) for a, b, w in keys], R.n, dict(R.ledger.c)))


def field_with(talk, session="s:1:a"):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    evs = [ST.Event(stream="discourse", role="user" if i % 2 == 0 else "model", ts=1_800_000_000.0 + i * 10, text=t)
           for i, (_w, t) in enumerate(talk)]
    f.write_events(evs, session=session)
    f.process()
    return f


class TestField(unittest.TestCase):
    def test_built_from_the_field_and_updated_as_the_talk_grows(self):
        f = field_with(TALK[:4])
        R = RP.of(f)
        n0 = R.n
        self.assertGreater(n0, 0)
        f.write_events([ST.Event(stream="discourse", role="user", ts=1_800_000_100.0, text="where is the beach")],
                       session="s:1:a")
        f.process()
        self.assertEqual(RP.of(f).n, n0 + 1)                         # only the new message is read

    def test_the_first_build_runs_in_the_background_and_the_banner_keeps_todays_rows_until_ready(self):
        f = field_with(TALK)
        RP._OF.pop(id(f), None)
        rows = [{"sim": 0.6, "answer": ["we hiked at the lake trail"], "a_n": [1], "q_n": 1, "speaker": "model"}]
        got = RP.annotate(f, rows, "where did you hike", wait=False)
        self.assertTrue(got == rows or "listener" in got[0])          # never blocks: today's rows or scored ones
        RP.ready(f, timeout=10)
        self.assertIn("listener", RP.annotate(f, rows, "where did you hike", wait=False)[0])

    def test_after_the_first_build_new_messages_arrive_without_scanning_the_field(self):
        f = field_with(TALK[:4])
        R = RP.of(f)
        n0 = R.n
        real_scan = f.b.scan

        def no_events_scan(coll, *a, **k):
            if coll == "events":
                raise AssertionError("a banner must not scan the field's events (cold collections read every row)")
            return real_scan(coll, *a, **k)
        f.write_events([ST.Event(stream="discourse", role="user", ts=1_800_000_200.0, text="where is the beach")],
                       session="s:1:a")
        f.process()                                   # the field tells the models (replies.note) as it writes
        f.b.scan = no_events_scan
        try:
            self.assertEqual(RP.of(f).n, n0 + 1)
        finally:
            f.b.scan = real_scan

    def test_banner_rows_carry_the_three_features(self):
        f = field_with(TALK)
        rows = [{"sim": 0.6, "answer": ["we hiked at the lake trail"], "a_n": [1], "q_n": 1, "speaker": "model"},
                {"sim": 0.6, "answer": ["no idea honestly"], "a_n": [2], "q_n": 2, "speaker": "user"}]
        got = RP.annotate(f, rows, "where did you hike")
        feats = RB.features(got, "where did you hike")
        for k in ("listener", "said", "about"):
            self.assertIn(k, feats[0])

    def test_the_fitted_scorer_weighs_the_reply_models(self):
        for w in (RB.DEFAULT_WEIGHTS, RB.RERANK_WEIGHTS):
            self.assertTrue(w["listener"] > 0 and w["about"] > 0 and w["said"] > 0)
        same = {"sim": 0.6, "answer": ["I painted a lake."], "q_n": None, "speaker": "Caroline"}
        rows = [dict(same, a_n=[1]), dict(same, a_n=[2], listener=3.0, about=3.0)]
        self.assertEqual(RB.rank(rows, "what did Caroline paint?", rerank=False)[0]["a_n"][0], 2)



class TestReview(unittest.TestCase):
    """Memo's review of #944."""

    def test_1_the_ledger_survives_a_restart(self):
        f = field_with(TALK[:4])
        RP.reset(f)
        RP.of(f)
        for i, (who, text) in enumerate(TALK[4:]):                   # live messages: predicted, then counted
            f.write_events([ST.Event(stream="discourse", role="user" if who == "Ann" else "model",
                                     ts=1_800_000_300.0 + i, text=text)], session="s:1:a")
            f.process()
        live = dict(RP.of(f).ledger.c)
        self.assertGreater(live[()][4], 0)
        RP.reset(f)                                                   # a restart
        self.assertEqual(dict(RP.of(f).ledger.c), live)               # the record comes back from the field

    def test_2_forget_drops_the_models_so_they_count_what_is_left(self):
        f = field_with(TALK)
        n0 = RP.of(f).n
        num = next(d["n"] for _k, d in f.b.scan("events", {}) if "lake house" in (d.get("text") or ""))
        f.forget(num)
        self.assertEqual(RP.of(f).n, n0 - 1)

    def test_3_rows_without_reply_scores_use_the_weights_fitted_without_them(self):
        rows = [{"sim": 0.62, "answer": ["The sky is blue today."], "a_n": [1], "q_n": 1, "speaker": "Melanie"},
                {"sim": 0.60, "answer": ["Caroline adopted a dog named Max, a rescue dog."], "a_n": [50], "q_n": 50,
                 "speaker": "Caroline"}]
        got = RB.rank(rows, "what dog did Caroline adopt?", rerank=False)
        self.assertEqual(got[0]["a_n"][0], 50)
        self.assertAlmostEqual(got[0]["score"], RB.score(got[0]["feat"], RB.LEGACY_WEIGHTS))

    def test_4_scoring_and_a_drain_never_overlap(self):
        f = field_with(TALK)
        RP.of(f)
        st = RP._OF[id(f)]
        self.assertTrue(st["lock"].acquire(timeout=1))
        st["lock"].release()                                          # annotate scores under this same lock
        got = RP.annotate(f, [{"sim": 0.5, "answer": ["we hiked at the lake trail"], "a_n": [1], "q_n": 1,
                                "speaker": "model"}], "where did you hike")
        self.assertIn("listener", got[0])

    def test_5_saved_weights_from_before_942_are_stale(self):
        f = field_with([("Ann", "hello there friend")])
        RB.save_weights(f, {**RB.LEGACY_WEIGHTS, "overlap": 5.0})
        self.assertEqual(RB.load_weights(f), RB.DEFAULT_WEIGHTS)      # not mixed with the new defaults
        RB.save_weights(f, {**RB.DEFAULT_WEIGHTS, "overlap": 5.0})
        self.assertEqual(RB.load_weights(f)["overlap"], 5.0)          # a full set is the seat's own

    def test_8_a_failed_build_is_tried_again(self):
        f = field_with(TALK)
        RP.reset(f)
        real = RP._read
        RP._read = lambda field, st: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertIsNone(RP.of(f))                               # failed: no partial tables served
        finally:
            RP._read = real
        self.assertIsNotNone(RP.of(f))                                # the next call builds again

    def test_the_keyword_cut_is_exact(self):
        R = replies()
        dfs = sorted((len(v) for v in R.post.values()), reverse=True)
        tot, acc, want = sum(dfs), 0, 0
        for d in dfs:
            acc += d
            if acc >= tot / 2:
                want = d
                break
        self.assertEqual(R._cut(), want)

    def test_note_keeps_only_what_it_reads(self):
        f = field_with(TALK[:2])
        RP.of(f)
        RP.note(f, {"stream": "discourse", "seq": 999, "session": "s", "text": "hi there", "wvec": [0.1] * 512,
                    "speaker": ["e", "Ann"], "role": "user", "sids": [1, 2]})
        q = RP._OF[id(f)]["pending"][-1]
        self.assertNotIn("wvec", q)
        self.assertNotIn("sids", q)

if __name__ == "__main__":
    unittest.main()
