"""#539: the lanes share ONE relevance score. A lane takes a slot only with a
row whose sentences sit near the ask; being the entity lane's rank 1 earns
nothing (the fixed share let a named speaker's lines crowd the top k)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efgate as G  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
import numpy as np  # noqa: E402


def _row(n, sim, how, via="direct"):
    return {"sim": sim, "q": "", "q_n": None, "a_n": [n], "ts": 1.0, "answer": [f"line {n}"],
            "via": via, "role": "note", "how": [how], "matched": []}


class TestOneScore(unittest.TestCase):
    def _run(self, direct, named, words=(), k=3):
        qv = np.ones(4, dtype="float32") / 2.0
        with mock.patch.object(G, "message_vec", return_value=qv), \
                mock.patch.object(EB, "past_answers", return_value=[]), \
                mock.patch.object(EB, "direct_answers", return_value=list(direct)), \
                mock.patch.object(EB, "tool_answers", return_value=[]), \
                mock.patch.object(EB, "word_answers", return_value=list(words)), \
                mock.patch.object(EB, "entity_answers", return_value=list(named)):
            return EB.recall_answers(object(), "what did Joanna say about the trip?", k=k)

    def test_a_far_entity_row_does_not_outrank_near_vector_rows(self):
        direct = [_row(1, 0.80, "vector"), _row(2, 0.78, "vector"), _row(3, 0.75, "vector")]
        named = [_row(101, 0.30, "entity"), _row(102, 0.29, "entity"), _row(103, 0.28, "entity")]
        rows = self._run(direct, named)
        self.assertEqual([r["a_n"][0] for r in rows], [1, 2, 3])

    def test_a_near_entity_row_still_wins_its_slot(self):
        direct = [_row(1, 0.60, "vector"), _row(2, 0.50, "vector")]
        named = [_row(101, 0.90, "entity")]
        rows = self._run(direct, named)
        self.assertEqual(rows[0]["a_n"][0], 101)
        self.assertIn("entity", rows[0]["how"])


if __name__ == "__main__":
    unittest.main()
