"""Hotfix (#690 / #702): recall never writes. An older event without its
stored whole vector is computed in ONE batch per lane (as before #702) and
kept in RAM for the run; it was back-filled one event at a time with a write
each, during the person's turn (minutes on the live seat after a restart)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efgate as G  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from test_whole_event_vec_690 import Q, _Count, _events, _field  # noqa: E402


def _strip(f):
    for k, d in _events(f):
        f.b.upsert("events", k, {**d, "wvec": None})
    f.__dict__.pop("_wvec_cache", None)


class TestReadOnly(unittest.TestCase):
    def test_recall_never_writes(self):
        f = _field()
        _strip(f)
        writes = []
        real = f.b.upsert
        f.b.upsert = lambda *a, **k: (writes.append(a[:2]), real(*a, **k))[1]
        self.assertTrue(EB.direct_answers(f, Q, sim_min=0))
        G.near_asks(f, G.message_vec(f, Q, embed_missing=False))
        self.assertEqual([w for w in writes if w[0] == "events"], [])

    def test_one_batch_per_lane_then_the_cache(self):
        f = _field()
        _strip(f)
        real = f.b
        calls = []

        class _B(_Count):
            def get_multi(self, coll, keys):
                if coll == "sentences":
                    calls.append(len(list(keys)))
                return super().get_multi(coll, keys)
        f.b = _B(real)
        items = _events(f)
        got = G.event_vecs(f, items)
        self.assertEqual(len(calls), 1)                     # one batch for all of them
        self.assertEqual(set(got), {k for k, _d in items})
        G.event_vecs(f, items)
        self.assertEqual(len(calls), 1)                     # then the cache
        f.b = real

    def test_same_answers_as_with_stored_vectors(self):
        f = _field()
        want = [r["answer"] for r in EB.direct_answers(f, Q, sim_min=0)]
        _strip(f)
        self.assertEqual([r["answer"] for r in EB.direct_answers(f, Q, sim_min=0)], want)


if __name__ == "__main__":
    unittest.main()
