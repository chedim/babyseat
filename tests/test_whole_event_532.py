"""#532: an event scores by the better of its nearest sentence and the event
as a whole (the sum of its sentence vectors). A short sentence that names
the person ("Yep, Caroline.") is near any ask naming her; the turn whose
sentences together answer the ask must not lose to it."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
from membrane import efgate as G  # noqa: E402
from membrane import entity_block as EB  # noqa: E402

QV = np.array([1.0, 0.0, 0.0], dtype="float32")
VECS = {
    "a1": np.array([0.72, 0.694, 0.0], dtype="float32"),     # the short name-only sentence
    "b1": np.array([0.6, 0.8, 0.0], dtype="float32"),        # two halves of one answer:
    "b2": np.array([0.6, -0.8, 0.0], dtype="float32"),       # each far, together on the ask
}


class _B:
    def get_multi(self, _table, ids):
        return {i: {"vec": VECS[i], "text": f"text {i} words here now"} for i in ids if i in VECS}


class _F:
    b = _B()


def _ev(n, sids):
    return {"n": n, "role": "doc", "status": "applied", "sids": sids, "ts": 1.0}


class TestWholeEvent(unittest.TestCase):
    def test_the_turn_whose_sentences_together_answer_beats_a_near_short_sentence(self):
        hits = {"a1": 0.72, "b1": 0.60, "b2": 0.60}
        evs = {"ea": _ev(1, ["a1"]), "eb": _ev(2, ["b1", "b2"])}
        with mock.patch.object(G, "sentence_events", return_value=(hits, evs)), \
                mock.patch.object(EB, "_to_a_machine", return_value=False), \
                mock.patch.object(EB, "_title_like", return_value=False):
            rows = EB.direct_answers(_F(), "ask", qvec=QV, k=2, sim_min=0.7)
        self.assertEqual([r["a_n"][0] for r in rows], [2, 1])
        self.assertGreater(rows[0]["sim"], rows[1]["sim"])


if __name__ == "__main__":
    unittest.main()


class TestDirectScansASessionOnce(unittest.TestCase):
    """direct_answers tests every candidate reply for 'answers the machine'
    from one scan of its session, not one scan per candidate (#668)."""

    def test_one_session_scan_per_call(self):
        from membrane import efield as EF
        from membrane import entity_block as EB
        from membrane import streams as ST
        from membrane.efstore import MemBackend
        from _ef_fakes import FakeEmbedder
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        texts = []
        for i in range(6):
            texts += [f"where is the bilge pump alarm number {i} published?",
                      f"the bilge pump alarm number {i} is published on the boat topic."]
        f.write_events([ST.Event(stream="discourse", role="user" if i % 2 == 0 else "model",
                                 ts=1_800_000_000.0 + i, text=t) for i, t in enumerate(texts)],
                       session="s:1:a")
        f.process()
        calls = []
        real = f.b.scan

        def spy(coll, where=None, *a, **kw):
            if coll == "events" and (where or {}).get("role") == "user" and "session" in (where or {}):
                calls.append(where["session"])
            return real(coll, where, *a, **kw)

        f.b.scan = spy
        rows = EB.direct_answers(f, "where is the bilge pump alarm published?", k=50, sim_min=0.0)
        self.assertTrue(rows)
        self.assertLessEqual(len(calls), 1)
