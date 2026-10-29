"""The word lane's `matched` words come from the index's postings, not from
re-reading each hit's text: ~600 single cold reads a turn on a pool of 50,
2.3 s of a live turn (#668). Same words, by construction."""

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import lexical as LX  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

WORDS = "bilge pump alarm mqtt broker port seat proxy deploy merge restart field banner".split()


class TestWordsIn(unittest.TestCase):
    def test_equals_the_text_tokens(self):
        rnd = random.Random(7)
        ix = LX.LexIndex()
        texts = [" ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 9))) for _ in range(400)]
        for i, t in enumerate(texts):
            ix.add_chunk(f"k:{i}", f"e{i}", t)
        q = ["bilge", "port", "banner", "nothing", "seat"]
        for did, t in enumerate(texts):
            self.assertEqual(ix.words_in(did, q), set(LX.tokens(t)) & set(q), did)


class TestWordLaneReadsNoTexts(unittest.TestCase):
    def test_rows_from_hits_takes_matched_from_the_index(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events([ST.Event(stream="discourse", role="user" if i % 2 == 0 else "model",
                                 ts=1_800_000_000.0 + i, text=t)
                        for i, t in enumerate(["where is the bilge pump alarm published?",
                                               "The bilge pump alarm goes to the mqtt broker on port 1883.",
                                               "which port does the broker use?",
                                               "The mqtt broker listens on port 1883 on the phone seat."])],
                       session="s:1:a")
        f.process()
        want = EB.word_answers(f, "which port does the mqtt broker listen on?", k=8, tools_ok=True)
        with mock.patch.object(EB, "_doc_text", side_effect=AssertionError("a text was re-read")):
            got = EB.word_answers(f, "which port does the mqtt broker listen on?", k=8, tools_ok=True)
        self.assertEqual([(r["a_n"], r.get("matched")) for r in got],
                         [(r["a_n"], r.get("matched")) for r in want])
        self.assertTrue(got)


if __name__ == "__main__":
    unittest.main()
