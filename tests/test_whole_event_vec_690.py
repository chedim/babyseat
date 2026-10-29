"""#690: an event keeps its whole vector, unit(sum of its sentence vectors),
written when its sentences are stored. direct_answers and near_asks read it
instead of every sentence row."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efgate as G  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend, unpack_f16  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
Q = "which license does the project use?"


def _field():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    f.write_events([
        ST.Event(stream="discourse", role="user", ts=T0, text="which license does this project use?"),
        ST.Event(stream="discourse", role="model", ts=T0 + 1,
                 text="The project license is PolyForm Noncommercial. It was changed this week."),
        ST.Event(stream="discourse", role="user", ts=T0 + 2, text="tidy the readme for me please")],
        session="s:1:0")
    f.process()
    return f


class _Count:
    """Counts the sentence rows a backend serves."""

    def __init__(self, b):
        self.b, self.rows = b, 0

    def get_multi(self, coll, keys):
        keys = list(keys)
        if coll == "sentences":
            self.rows += len(keys)
        return self.b.get_multi(coll, keys)

    def __getattr__(self, n):
        return getattr(self.b, n)


def _events(f):
    return [(k, d) for k, d in f.b.scan("events", {"origin": f.origin}) if d.get("sids")]


class TestStored(unittest.TestCase):
    def test_event_carries_its_whole_vector(self):
        f = _field()
        for _k, d in _events(f):
            got = f.b.get_multi("sentences", list(dict.fromkeys(d["sids"])))
            want = EF.unit(sum(EF.unit(got[s]["vec"]) for s in dict.fromkeys(d["sids"])))
            self.assertTrue(np.allclose(unpack_f16(d["wvec"]), want, atol=2e-3))

    def test_forgotten_event_drops_it(self):
        f = _field()
        k, d = _events(f)[0]
        f.b.upsert("events", k, {**d, "status": "pending", "sids": [], "wvec": None})
        self.assertFalse(f.b.get("events", k).get("wvec"))


class TestReads(unittest.TestCase):
    def _strip(self, f):
        for k, d in _events(f):
            f.b.upsert("events", k, {**d, "wvec": None})

    def test_same_rows_and_scores_with_or_without_stored_vectors(self):
        f = _field()
        with_w = EB.direct_answers(f, Q, sim_min=0)
        self._strip(f)
        without = EB.direct_answers(f, Q, sim_min=0)
        self.assertEqual([r["answer"] for r in with_w], [r["answer"] for r in without])
        for a, b in zip(with_w, without):
            self.assertAlmostEqual(a["sim"], b["sim"], places=2)

    def test_no_sentence_rows_read_for_whole_event_scores(self):
        f = _field()
        qv = G.message_vec(f, Q)
        real = f.b
        f.b = _Count(real)
        EB.direct_answers(f, Q, qvec=qv, sim_min=0)
        with_w = f.b.rows
        f.b = real
        self._strip(f)
        f.b = _Count(real)
        EB.direct_answers(f, Q, qvec=qv, sim_min=0)
        self.assertLess(with_w, f.b.rows)

    def test_older_event_scores_without_a_write(self):
        # the hotfix: recall computes an older event's vector (one batch, kept
        # in RAM) and never writes it back during a turn
        f = _field()
        self._strip(f)
        self.assertTrue(EB.direct_answers(f, Q, sim_min=0))
        got = [d for _k, d in _events(f) if d.get("role") == "model"]
        self.assertTrue(got and not any(d.get("wvec") for d in got))

    def test_readonly_store_just_computes(self):
        f = _field()
        self._strip(f)
        f.b.upsert = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read-only"))
        self.assertTrue(EB.direct_answers(f, Q, sim_min=0))

    def test_near_asks_reads_no_sentence_rows_and_same_order(self):
        f = _field()
        qv = G.message_vec(f, Q)
        want = [(round(s, 2), k) for s, k, _d in G.near_asks(f, qv)]
        real = f.b
        f.b = _Count(real)
        got = [(round(s, 2), k) for s, k, _d in G.near_asks(f, qv)]
        rows = f.b.rows
        f.b = real
        self.assertEqual(got, want)
        # only the ANN's own sentence lookup remains; no per-ask rebuild
        self.assertEqual(rows, 0)


if __name__ == "__main__":
    unittest.main()
