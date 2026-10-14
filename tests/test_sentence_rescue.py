"""THE FACT-ONE RESCUE (issue #4, PR #16): a short note without terminal
punctuation -- the seat's "battery near dead" -- fell through
`streams.sentences()`'s length gate: ZERO sentences, so the event never
reached the embedded status, apply never saw it, no entity_state formed
and recall was end-to-end empty. THE CONTRACT (the floor): non-blank
prose is at least one sentence -- whole and intact, never clipped --
and the rescued sentence reaches the field end to end: an entity_state
forms (the nursery, like every first mention) and recall over it finds
the topic.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def field(**kw):
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host", **kw)


def states(f):
    return {d["eid"]: d for _, d in f.b.scan("entity_states")}


class TestSentencesFloor(unittest.TestCase):
    def test_a_short_unpunctuated_note_is_ONE_sentence(self):
        # THE EXHIBIT: the seat's note, no period, five words.
        ev = {"stream": "discourse", "role": "user",
              "text": "battery near dead"}
        f = field()
        s = f._sentences_of(ev)
        self.assertEqual(s, ["battery near dead"])
        self.assertTrue(ST.sentences("battery near dead") == [] or
                        s == ST.sentences("battery near dead"))

    def test_splitter_output_passes_through_unchanged(self):
        # the rescue is a FLOOR, not a re-splitter: real prose keeps the
        # splitter's exact sentences
        ev = {"stream": "discourse", "role": "user",
              "text": "the gateway keeps timing out. restart it after lunch."}
        f = field()
        self.assertEqual(f._sentences_of(ev), ST.sentences(
            "the gateway keeps timing out. restart it after lunch."))

    def test_blank_prose_stays_empty(self):
        # the floor is for NON-BLANK prose only; a speaker-line-only or
        # voice-prefixed user event yields nothing (prose_of's own rules)
        f = field()
        self.assertEqual(f._sentences_of({"stream": "discourse",
                                          "role": "user", "text": "   "}), [])


class TestReachesTheField(unittest.TestCase):
    def test_the_rescued_note_reaches_the_embedded_status(self):
        # THE OLD SYMPTOM, end to end: the note's event 'never reaches
        # the embedded status' -- apply never sees it, so the field's
        # delta never runs on it. The rescued sentence must reach it.
        f = field()
        f.write_events([ST.Event(stream="discourse", role="user",
                                 text="battery near dead", ts=1.0)],
                       session="s1")
        f.process()
        statuses = [d["status"] for _, d in f.b.scan("events")]
        self.assertEqual(statuses, ["applied"], statuses)
        # the field's geometry moved with the note: a word state exists
        # in the nursery (the same tier every first mention starts at)
        self.assertTrue(any(d.get("tier") == "nursery"
                            for d in states(f).values()))

    def test_the_ones_that_always_worked_still_work(self):
        # regular punctuated prose is not disturbed by the floor
        f = field()
        f.write_events([ST.Event(stream="discourse", role="user",
                                 text="the gateway keeps timing out.",
                                 ts=1.0)], session="s1")
        f.process()
        s = [d["status"] for _, d in f.b.scan("events")]
        self.assertEqual(s, ["applied"], s)
        self.assertTrue(states(f))


if __name__ == "__main__":
    unittest.main()