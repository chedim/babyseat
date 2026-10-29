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

    def test_starting_weights_are_arm_4_not_cosine_only(self):
        w = RB.DEFAULT_WEIGHTS
        self.assertTrue(w["inv_rank"] > 0 and w["bm25"] > 0 and w["speaker"] > 0 and w["neighbour"] > 0)
        # not a no-op: a row with the words and the speaker beats a higher-cosine, wordless one
        rows = [{"sim": 0.62, "answer": ["The sky is blue today."], "a_n": [1], "q_n": 1, "speaker": "Melanie"},
                {"sim": 0.60, "answer": ["Caroline adopted a dog named Max, a rescue dog."],
                 "a_n": [50], "q_n": 50, "speaker": "Caroline"}]
        self.assertEqual(RB.rank(rows, "what dog did Caroline adopt?")[0]["a_n"][0], 50)

    def test_weights_come_from_the_field_entity(self):
        f = field_with(["hello there friend"])
        self.assertEqual(RB.load_weights(f), RB.DEFAULT_WEIGHTS)
        RB.save_weights(f, {**RB.DEFAULT_WEIGHTS, "overlap": 5.0})
        self.assertEqual(RB.load_weights(f)["overlap"], 5.0)
        got = RB.rank(self.rows(), "what did Caroline adopt?", RB.load_weights(f))
        self.assertEqual(got[0]["a_n"][0], 5)       # word overlap now outweighs raw cosine

    def test_speaker_match_feature(self):
        rows = [{"sim": 0.8, "answer": ["I painted a lake."], "a_n": [1], "q_n": 1, "speaker": "Melanie"},
                {"sim": 0.8, "answer": ["I painted a lake."], "a_n": [2], "q_n": 2, "speaker": "Caroline"}]
        w = {**RB.DEFAULT_WEIGHTS, "speaker": 2.0}
        self.assertEqual(RB.rank(rows, "what did Caroline paint?", w)[0]["a_n"][0], 2)

    def test_speaker_comes_from_the_events_own_speaker_slot(self):
        f = field_with(["[user: Caroline]\nI adopted a dog named Max.", "Nice."])
        n = next(d["n"] for _k, d in f.b.scan("events", {}) if "Max" in (d.get("text") or ""))
        got = RB.with_speakers(f, [{"a_n": [n], "answer": ["x"], "sim": 0.5}, {"a_n": [], "answer": ["y"]}])
        self.assertEqual(got[0]["speaker"].lower(), "caroline")
        self.assertEqual(got[1]["speaker"], "")


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


class TestCost(unittest.TestCase):
    def test_a_new_message_is_embedded_once_for_the_banner(self):
        f = field_with(["Caroline adopted a dog named Max.", "Lovely.", "Melanie paints lakes."])
        calls = []
        real = f.emb.embed
        f.emb.embed = lambda xs: (calls.append(len(xs)), real(xs))[1]
        RB.build(f, "which dog did Caroline adopt last spring?", session="s:1:a")
        self.assertLessEqual(sum(calls), 1)


class TestTiming(unittest.TestCase):
    """The live-field exhibit (2026-10-28): the block cost 15-175 s a turn on
    the seat and 0.6 s offline. Each rebuild says where its time went."""

    def test_a_rebuild_reports_its_steps(self):
        f = field_with(["Caroline adopted a dog named Max.", "Lovely.", "Melanie paints lakes."])
        _lines, state = RB.build(f, "which dog did Caroline adopt last spring?", session="s:1:a", sim_min=0)
        t = state["timing"]
        self.assertEqual(set(t), {"vec", "recall", "speakers", "recent", "pick", "rows"})
        self.assertTrue(all(v >= 0 for v in t.values()))

    def test_recall_lines_logs_one_line_per_rebuild(self):
        import io
        import types
        from contextlib import redirect_stderr
        from membrane import conv_banner as CB
        f = field_with(["Caroline adopted a dog named Max.", "Lovely."])
        srv = types.SimpleNamespace()
        err = io.StringIO()
        with redirect_stderr(err):
            CB.recall_lines(srv, f, "conv-1", [], "which dog did Caroline adopt?")
            CB.recall_lines(srv, f, "conv-1", [], "ok")           # no topic: no rebuild, no line
        log = [ln for ln in err.getvalue().splitlines() if ln.startswith("[recall-banner] ")]
        self.assertEqual(len(log), 1)
        self.assertIn("recall", log[0])
        self.assertIn("vec", log[0])


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


class TestAskLaneWidth(unittest.TestCase):
    """The pool (POOL rows) never widens the earlier-asks lane: at k=50 it
    searched 1800 sentences and re-read each hit's vectors, 16 of 20 s a
    live turn (#668)."""

    def test_pool_k_does_not_widen_the_ask_lane(self):
        from unittest import mock
        from membrane import entity_block as EB
        f = field_with(["how do we deploy the seat after a merge?",
                        "run the deploy script, then restart the seat once idle."])
        seen = []
        real = EB.past_answers

        def spy(*a, k=3, **kw):
            seen.append(k)
            return real(*a, k=k, **kw)

        with mock.patch.object(EB, "past_answers", spy):
            EB.recall_answers(f, "how do we deploy the seat?", k=RB.POOL)
        self.assertEqual(seen, [EB.ASK_K])
        self.assertEqual(EB.ASK_K, 3)


class TestHistoryTurn(unittest.TestCase):
    """The banner's query is the message plus the chat's previous turn (either
    side): on the live field it held the model's own next search finds in 26
    of 63 cases against 16 without (2026-10-29; operator-approved window 1)."""

    def test_the_query_adds_the_previous_turn(self):
        from unittest import mock
        from membrane import efgate as G
        from membrane import entity_block as EB
        f = field_with(["where is the bilge pump alarm published?",
                        "It goes to the boat/bilge MQTT topic on the phone seat."])
        seen = {}
        real = EB.recall_answers

        def spy(*a, qvec=None, **kw):
            seen["qvec"] = qvec
            return real(*a, qvec=qvec, **kw)

        with mock.patch.object(EB, "recall_answers", spy):
            RB.build(f, "and which port does that broker listen on?", session="s:1:a")
        v_msg = G.message_vec(f, "and which port does that broker listen on?")
        v_prev = G.message_vec(f, "It goes to the boat/bilge MQTT topic on the phone seat.")
        want = EF.unit(v_msg + v_prev)
        self.assertAlmostEqual(float(seen["qvec"] @ want), 1.0, places=5)
        self.assertEqual(RB.HISTORY_TURNS, 1)

    def test_a_new_chat_queries_the_message_alone(self):
        from unittest import mock
        from membrane import efgate as G
        from membrane import entity_block as EB
        f = field_with(["where is the bilge pump alarm published?"], session="s:9:z")
        seen = {}
        real = EB.recall_answers

        def spy(*a, qvec=None, **kw):
            seen["qvec"] = qvec
            return real(*a, qvec=qvec, **kw)

        with mock.patch.object(EB, "recall_answers", spy):
            RB.build(f, "which port does the broker listen on?", session="s:new:chat")
        v = G.message_vec(f, "which port does the broker listen on?")
        self.assertAlmostEqual(float(seen["qvec"] @ v), 1.0, places=5)

    def test_the_message_itself_already_captured_is_not_its_own_history(self):
        f = field_with(["where is the bilge pump alarm published?",
                        "It goes to the boat/bilge MQTT topic on the phone seat.",
                        "and which port does that broker listen on?"])
        got = RB.history_texts(f, "s:1:a", "and which port does that broker listen on?")
        self.assertEqual(got, ["It goes to the boat/bilge MQTT topic on the phone seat."])


class TestRenderForReusesTheBanner(unittest.TestCase):
    """The ask-time block's past answers are the banner's own top rows for
    the same message, not a second recall (~1.6 s a turn, #668)."""

    def test_build_keeps_its_top_rows(self):
        f = field_with(["where is the bilge pump alarm published?",
                        "It goes to the boat/bilge MQTT topic on the phone seat."])
        _lines, state = RB.build(f, "which topic carries the bilge pump alarm?", session="s:1:a")
        self.assertIn("top", state)
        self.assertLessEqual(len(state["top"]), 3)

    def test_render_for_takes_given_rows_without_recall(self):
        from unittest import mock
        from membrane import entity_block as EB
        f = field_with(["where is the bilge pump alarm published?",
                        "It goes to the boat/bilge MQTT topic on the phone seat."])
        _lines, state = RB.build(f, "which topic carries the bilge pump alarm?", session="s:1:a")
        with mock.patch.object(EB, "recall_answers", side_effect=AssertionError("recall ran twice")):
            EB.render_for(f, "which topic carries the bilge pump alarm?", rows=state["top"])

    def test_the_proxy_passes_the_banner_rows(self):
        src = (Path(__file__).parent.parent / "membrane" / "lanes.py").read_text()
        self.assertIn("render_for(fld, query, vec, rows=_banner_top(srv, query), entities=False)", src)


class TestReuseMatchesAcrossTheSpeakerLine(unittest.TestCase):
    """The banner builds from the raw message ('[user: Dima] ...'); the gate's
    query has the speaker line cut -- the reuse never matched and recall ran
    twice every turn (2026-10-29 profile)."""

    def test_banner_top_matches_with_or_without_the_speaker_line(self):
        from types import SimpleNamespace
        from unittest import mock
        from membrane import lanes as L
        srv = SimpleNamespace(_recall_prev={"sid:x": {"text": "[user: Dima] which port does the proxy use?",
                                                      "top": [{"a_n": [1]}]}})
        with mock.patch.object(L._CAPMOD, "current_conversation", return_value="sid:x"):
            self.assertEqual(L._banner_top(srv, "which port does the proxy use?"), [{"a_n": [1]}])
            self.assertIsNone(L._banner_top(srv, "something else entirely?"))


class TestNoContextDropsTheEntitiesSection(unittest.TestCase):
    """No-context mode: the ask-time block keeps its past answers and drops
    the ENTITIES section -- the banner's RECALL and the conversation block
    cover it (operator, 2026-10-29); ~1 s and ~600 tokens a turn."""

    def test_render_for_without_entities_runs_no_entity_view(self):
        from unittest import mock
        from membrane import entity_block as EB
        f = field_with(["where is the bilge pump alarm published?",
                        "It goes to the boat/bilge MQTT topic on the phone seat."])
        with mock.patch.object(EB, "entities_for", side_effect=AssertionError("entity views ran")):
            EB.render_for(f, "where is the bilge pump alarm published?", entities=False)

    def test_the_no_context_path_asks_for_no_entities(self):
        src = (Path(__file__).parent.parent / "membrane" / "lanes.py").read_text()
        self.assertIn("render_for(fld, query, vec, rows=_banner_top(srv, query), entities=False)", src)
