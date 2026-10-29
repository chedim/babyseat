"""#668 PR B: learning from use -- when the model searches after the banner, the
pool row its answer actually uses is a positive label, the rows the scorer ranked
above it are negatives; the night pass refits the weights from those labels
(decayed), replacing the starting weights in the field's config entity."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import recall_labels as RL  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
DAY = 86400.0


def pool_of(*facts):
    """A snapshot in scorer order: each row has features and a fact line."""
    return [{"feat": {"sim": 0.5, "inv_rank": 1 / (1 + i), "bm25": 0.0, "overlap": 0.0,
                      "speaker": 0.0, "neighbour": 0.0}, "fact": f} for i, f in enumerate(facts)]


class TestLabel(unittest.TestCase):
    pool = pool_of("The deploy runs on port 8080 behind nginx.",
                   "Lunch was a sandwich near the harbour.",
                   "Caroline adopted a rescue dog named Max.")
    found = "situation: dog adoption response: Caroline adopted a rescue dog named Max last spring"

    def test_used_and_found_row_is_positive_rows_above_are_negatives(self):
        ex = RL.label(self.pool, self.found, "She adopted a rescue dog called Max, as Caroline said.")
        self.assertEqual([e["y"] for e in ex], [0, 0, 1])
        self.assertEqual(ex[2]["feat"], self.pool[2]["feat"])

    def test_row_not_in_the_answer_is_no_label(self):
        self.assertIsNone(RL.label(self.pool, self.found, "I could not find anything about that."))

    def test_row_the_search_did_not_find_is_no_label(self):
        self.assertIsNone(RL.label(self.pool, "nothing relevant here at all", "Caroline adopted a rescue dog named Max."))

    def test_positive_in_first_place_has_no_negatives_but_still_counts(self):
        p = pool_of("Caroline adopted a rescue dog named Max.", "Lunch was a sandwich.")
        ex = RL.label(p, self.found, "Caroline adopted a rescue dog named Max.")
        self.assertEqual([e["y"] for e in ex], [1])

    def test_a_near_copy_of_the_positive_is_not_a_negative(self):
        p = pool_of("Caroline adopted a rescue dog named Max.", "Caroline adopted a rescue dog named Max!",
                    "Caroline adopted a rescue dog named Max, last spring.")
        ex = RL.label(p, self.found, "Caroline adopted a rescue dog named Max, last spring.")
        self.assertEqual([e["y"] for e in ex], [1])


class TestSearchDetect(unittest.TestCase):
    def test_memory_searches(self):
        for n in ("query", "mcp__membrane__query", "find"):
            self.assertTrue(RL.is_search(n, {"action": "memory"} if n == "find" else {}), n)

    def test_not_searches(self):
        for n in ("code_run", "entity_read", "state", "learn"):
            self.assertFalse(RL.is_search(n, {}), n)


def mk_field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def reply_in(f, session, ts, text):
    f.write_events([ST.Event(stream="discourse", role="model", ts=ts, text=text)], session=session)
    f.process()


class TestNoteAndResolve(unittest.TestCase):
    def state(self):
        return {"lines": [], "text": "what dog did Caroline adopt?", "session": "s:1:a",
                "pool": pool_of("The deploy runs on port 8080.", "Caroline adopted a rescue dog named Max.")}

    def test_no_pool_no_record(self):
        f = mk_field()
        self.assertIsNone(RL.note_search(f, None, "c1", "conv", "q", T0))
        self.assertIsNone(RL.note_search(f, {"lines": ["x"]}, "c1", "conv", "q", T0))

    def test_resolves_once_the_reply_arrives(self):
        f = mk_field()
        key = RL.note_search(f, self.state(), "c1", "conv", "Caroline adopted a rescue dog named Max", T0)
        self.assertTrue(key)
        self.assertEqual(RL.resolve(f, T0 + 10), {"done": 0, "dropped": 0, "open": 1})
        reply_in(f, "s:1:a", T0 + 5, "Caroline adopted a rescue dog named Max.")
        self.assertEqual(RL.resolve(f, T0 + 10), {"done": 1, "dropped": 0, "open": 0})
        self.assertEqual(RL.examples(f, T0 + 10)[-1]["y"], 1)
        self.assertEqual(len(RL.examples(f, T0 + 10)), 2)

    def test_unanswered_record_is_dropped_after_the_wait(self):
        f = mk_field()
        RL.note_search(f, self.state(), "c1", "conv", "Caroline adopted a rescue dog named Max", T0)
        self.assertEqual(RL.resolve(f, T0 + RL.WAIT_S + 1)["dropped"], 1)

    def test_a_reply_that_uses_no_row_is_dropped(self):
        f = mk_field()
        RL.note_search(f, self.state(), "c1", "conv", "Caroline adopted a rescue dog named Max", T0)
        reply_in(f, "s:1:a", T0 + 5, "Nothing relevant came back.")
        self.assertEqual(RL.resolve(f, T0 + 10), {"done": 0, "dropped": 1, "open": 0})
        self.assertEqual(RL.examples(f, T0 + 10), [])


def fake_examples(n_pos, good="overlap"):
    """Rows where `good` separates the positive from the negatives."""
    out = []
    for _i in range(n_pos):
        base = {"sim": 0.5, "inv_rank": 0.5, "bm25": 0.0, "overlap": 0.0, "speaker": 0.0, "neighbour": 0.0}
        out.append({"feat": {**base}, "y": 0})
        out.append({"feat": {**base, good: 1.0}, "y": 1})
    return out


class TestFit(unittest.TestCase):
    def test_moves_toward_the_feature_that_separates(self):
        w = RL.fit(fake_examples(60), RB.DEFAULT_WEIGHTS, now=T0, stamped=[T0] * 120)
        self.assertGreater(w["overlap"], RB.DEFAULT_WEIGHTS["overlap"] + 1.0)

    def test_prior_holds_with_few_labels(self):
        few = RL.fit(fake_examples(2), RB.DEFAULT_WEIGHTS, now=T0, stamped=[T0] * 4)
        many = RL.fit(fake_examples(60), RB.DEFAULT_WEIGHTS, now=T0, stamped=[T0] * 120)
        w0 = RB.DEFAULT_WEIGHTS["overlap"]
        self.assertLess(few["overlap"] - w0, many["overlap"] - w0)
        self.assertLess(few["overlap"] - w0, 2.5)

    def test_old_labels_weigh_less(self):
        old = fake_examples(60, "speaker")
        new = fake_examples(60, "overlap")
        w = RL.fit(old + new, RB.DEFAULT_WEIGHTS, now=T0,
                   stamped=[T0 - 10 * RL.HALF_LIFE_DAYS * DAY] * len(old) + [T0] * len(new))
        self.assertGreater(w["overlap"] - RB.DEFAULT_WEIGHTS["overlap"], w["speaker"] - RB.DEFAULT_WEIGHTS["speaker"])


class TestNight(unittest.TestCase):
    def put(self, f, n, ts):
        for i in range(n):
            f.b.upsert(RL.COLL, f"k{i}:{ts}", {"status": "done", "done_ts": ts, "ts": ts,
                                                  "examples": fake_examples(1)})

    def test_too_few_positives_keeps_the_weights(self):
        f = mk_field()
        self.put(f, RL.MIN_POS - 1, T0)
        out = RL.night(f, T0 + 1)
        self.assertFalse(out["refit"])
        self.assertEqual(RB.load_weights(f), RB.DEFAULT_WEIGHTS)

    def test_refit_replaces_the_starting_weights_in_the_field(self):
        f = mk_field()
        self.put(f, RL.MIN_POS + 20, T0)
        out = RL.night(f, T0 + 1)
        self.assertTrue(out["refit"])
        self.assertGreater(RB.load_weights(f)["overlap"], RB.DEFAULT_WEIGHTS["overlap"])
        # nothing new since: the next night does not refit
        self.assertFalse(RL.night(f, T0 + 2)["refit"])

    def test_labels_older_than_the_keep_window_are_pruned(self):
        f = mk_field()
        self.put(f, 3, T0)
        RL.night(f, T0 + (RL.KEEP_DAYS + 1) * DAY)
        self.assertEqual(RL.examples(f, T0 + (RL.KEEP_DAYS + 1) * DAY), [])


class TestBuildKeepsThePool(unittest.TestCase):
    def test_state_carries_the_pool_text_and_session(self):
        f = mk_field()
        evs = [ST.Event(stream="discourse", role="model", ts=T0 + i, text=t) for i, t in enumerate(
            ["Caroline adopted a rescue dog named Max.", "The deploy runs on port 8080."])]
        f.write_events(evs, session="s:0:z")
        f.process()
        lines, st = RB.build(f, "what dog did Caroline adopt?", session="s:1:a", sim_min=0.0)
        self.assertIn("pool", st)
        self.assertEqual(st["session"], "s:1:a")
        self.assertEqual(st["text"], "what dog did Caroline adopt?")
        self.assertTrue(all({"feat", "fact"} <= set(r) for r in st["pool"]))


class TestWiring(unittest.TestCase):
    def test_the_capture_hook_notes_a_search_with_the_banners_pool(self):
        import types
        from membrane import lanes as L
        f = mk_field()
        pool = pool_of("Caroline adopted a rescue dog named Max.")
        srv = types.SimpleNamespace(
            _recall_prev={"conv-1": {"lines": [], "pool": pool, "text": "dog?", "session": "s:1:a"}},
            _tool_results={}, capturer=None,
            membrane=types.SimpleNamespace(_pending_tools={"c9": {"query": "dog?", "name": "query", "args": "{}"}}))
        body = {"messages": [{"role": "tool", "tool_call_id": "c9", "content": "Caroline adopted a rescue dog named Max"}]}
        from membrane import capture as C
        C.set_conversation("conv-1")
        orig = L._efield_on
        L._efield_on = lambda _s: f
        try:
            L._capture_tool_results(srv, body)
        finally:
            L._efield_on = orig
            C.set_conversation("")
        self.assertIsNotNone(f.b.get(RL.COLL, "conv-1:c9"))

    def test_the_night_pass_runs_the_label_step(self):
        from membrane import efmind
        import inspect
        self.assertIn("RL.night", inspect.getsource(efmind.night))


if __name__ == "__main__":
    unittest.main()
