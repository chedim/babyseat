"""#429 PR 1: recall ranks the person's earlier asks, not the seat's receipts.

Job receipts, task notifications and watch blocks reach the field as user
turns. They are tagged at capture (the event's `author`), recognised on
read by the same prefixes when an older record carries no tag, and never
count as an earlier ask. On a question-like turn, observation evidence
and non-person lines lose OBSERVATION_PENALTY."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efgate as G  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


class TestApprovedValues(unittest.TestCase):
    def test_the_five_receipt_prefixes(self):
        self.assertEqual(set(ST.RECEIPT_PREFIXES), {
            "[membrane] job #", "your job #", "<task-notification>",
            "[membrane-watch]", "[membrane-intercept]"})

    def test_penalty_and_question_words(self):
        self.assertEqual(ST.OBSERVATION_PENALTY, 0.10)
        self.assertEqual(ST.QUESTION_WORDS, frozenset(
            "what which when who whom whose how why where did does do is are "
            "was were can could should would will".split()))


class TestAuthor(unittest.TestCase):
    def test_prefixes_classify(self):
        self.assertEqual(ST.author_of("[membrane] job #9 finished (exec link x, 4.0 s): ok"), "receipt")
        self.assertEqual(ST.author_of("your job #11 finished (exec link None, 6.6 s)"), "receipt")
        self.assertEqual(ST.author_of("<task-notification> <task-id>a1</task-id>"), "notification")
        self.assertEqual(ST.author_of("[membrane-watch] your ask was read"), "sensor")
        self.assertEqual(ST.author_of("[membrane-intercept] paused: the lanes are down"), "receipt")
        self.assertEqual(ST.author_of("  [membrane] job #2 finished"), "receipt")

    def test_a_person(self):
        self.assertEqual(ST.author_of("which license does the project use?"), "person")
        self.assertEqual(ST.author_of("tell me about your job #1 please"), "person")

    def test_capture_tags_user_events(self):
        evs = ST.split_turn("[membrane] job #3 finished (x, 1 s): rc=0", "noted.")
        self.assertEqual(evs[0].role, "user")
        self.assertEqual(evs[0].author, "receipt")
        evs = ST.split_turn("which license does it use?", "PolyForm.")
        self.assertEqual(evs[0].author, "person")
        self.assertEqual(evs[1].author, "")           # the model is no author class

    def test_stored_event_carries_author(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        keys = f.write_events(ST.split_turn("<task-notification> done", "ok"), session="s")
        self.assertEqual(f.b.get("events", keys[0])["author"], "notification")

    def test_old_record_classified_on_read(self):
        self.assertEqual(ST.event_author({"role": "user", "text": "your job #4 finished"}), "receipt")
        self.assertEqual(ST.event_author({"role": "user", "text": "why is it slow?"}), "person")
        self.assertEqual(ST.event_author({"role": "user", "text": "x", "author": "sensor"}), "sensor")


class TestQuestionLike(unittest.TestCase):
    def test_question_mark_or_word(self):
        self.assertTrue(ST.is_question_like("the license?"))
        self.assertTrue(ST.is_question_like("Which license does it use"))
        self.assertTrue(ST.is_question_like("whose token is it"))
        self.assertTrue(ST.is_question_like("Will it restart"))
        self.assertFalse(ST.is_question_like("restart the router"))
        self.assertFalse(ST.is_question_like(""))
        self.assertFalse(ST.is_question_like("whatever works"))


def _field_with_receipt_and_ask():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    # an older record: a receipt captured as a user turn BEFORE the tag
    # existed (no author key), word for word closer to the question
    f.write_events([
        ST.Event(stream="discourse", role="user", ts=T0,
                 text="[membrane] job #2 finished: the seat license check of the project ran ok."),
        ST.Event(stream="discourse", role="model", ts=T0 + 1,
                 text="The license check job ran fine and printed nothing unusual today.")],
        session="s:1:0")
    f.write_events([
        ST.Event(stream="discourse", role="user", ts=T0 + 100,
                 text="which license does the project use now?"),
        ST.Event(stream="discourse", role="model", ts=T0 + 101,
                 text="The project moved to the PolyForm Noncommercial license last week.")],
        session="s:1:1")
    for _, d in f.b.scan("events", {"origin": f.origin}):
        d.pop("author", None)                        # an old field: never tagged
    f.process()
    return f


class TestNearAsks(unittest.TestCase):
    def test_receipts_never_count_as_earlier_asks(self):
        f = _field_with_receipt_and_ask()
        qv = G.message_vec(f, "which license does the project use?")
        asks = [d.get("text") for _, _, d in G.near_asks(f, qv)]
        self.assertTrue(asks)
        self.assertFalse([t for t in asks if t.startswith("[membrane] job #")])
        self.assertIn("which license does the project use now?", asks)

    def test_tagged_receipt_is_out_whatever_its_text(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        ev = ST.Event(stream="discourse", role="user", ts=T0,
                      text="which license does the project use?")
        ev.author = "sensor"
        f.write_events([ev], session="s")
        f.process()
        qv = G.message_vec(f, "which license does the project use?")
        self.assertEqual(G.near_asks(f, qv), [])

    def test_past_answers_skip_the_receipt(self):
        f = _field_with_receipt_and_ask()
        rows = EB.past_answers(f, "which license does the project use?", k=8, sim_min=0)
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if r["q"].startswith("[membrane] job #")])
        self.assertIn("PolyForm", " ".join(rows[0]["answer"]))


class TestObservationPenalty(unittest.TestCase):
    ROWS = [{"text": "a reading came in from the license sensor", "score": 0.70,
             "role": "user", "author": "sensor", "stream": "discourse"},
            {"text": "we decided the license is PolyForm", "score": 0.65,
             "role": "model", "stream": "discourse"}]

    def test_question_like_turn_ranks_discourse_above_the_reading(self):
        got = [r["text"] for r in EB.pick_evidence([dict(r) for r in self.ROWS], 1,
                                                    question_like=True)]
        self.assertEqual(got, ["we decided the license is PolyForm"])

    def test_observation_stream_takes_the_penalty_too(self):
        rows = [{"text": "edit applied to the license file", "score": 0.70,
                 "role": "tool", "stream": "observation"},
                {"text": "we decided the license is PolyForm", "score": 0.65,
                 "role": "model", "stream": "discourse"}]
        got = [r["text"] for r in EB.pick_evidence(rows, 1, question_like=True)]
        self.assertEqual(got, ["we decided the license is PolyForm"])

    def test_no_penalty_off_question(self):
        # a non-question turn keeps today's order (the user-role 0.15 still applies)
        rows = [{"text": "a reading came in from the license sensor", "score": 0.86,
                 "role": "user", "author": "sensor", "stream": "discourse"},
                {"text": "we decided the license is PolyForm", "score": 0.70,
                 "role": "model", "stream": "discourse"}]
        got = [r["text"] for r in EB.pick_evidence(rows, 1)]
        self.assertEqual(got, ["a reading came in from the license sensor"])

    def test_a_far_better_observation_still_wins(self):
        rows = [{"text": "a reading came in from the license sensor", "score": 0.86,
                 "role": "user", "author": "sensor", "stream": "discourse"},
                {"text": "we decided the license is PolyForm", "score": 0.50,
                 "role": "model", "stream": "discourse"}]
        got = [r["text"] for r in EB.pick_evidence(rows, 1, question_like=True)]
        self.assertEqual(got, ["a reading came in from the license sensor"])

    def test_evidence_rows_carry_the_author(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events([
            ST.Event(stream="discourse", role="user", ts=T0,
                     text="[membrane] job #5 finished: membrane/router.py reloaded."),
            ST.Event(stream="discourse", role="user", ts=T0 + 1,
                     text="why does membrane/router.py retry twice?")], session="s")
        for _, d in f.b.scan("events", {"origin": f.origin}):
            d.pop("author", None)                    # an old field: never tagged
        f.process()
        by = {r["text"]: r.get("author") for r in f.evidence("f:membrane/router.py", k=10)}
        self.assertEqual(by.get("[membrane] job #5 finished: membrane/router.py reloaded."), "receipt")
        self.assertEqual(by.get("why does membrane/router.py retry twice?"), "person")

if __name__ == "__main__":
    unittest.main()
