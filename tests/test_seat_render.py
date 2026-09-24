"""THE SEAT STAMP + THE SITUATION-RESPONSE RENDER (the operator's ask):

- every learned cell carries WHICH seat (proxy instance) lived the exchange
  (extra["seat"] + the dataclass field, stamped from the peers registry id);
- recall renders entries as 'situation: ... / response: ...' -- situation-
  response, not Q/A -- with [seat: proxy-N] riding the header when present;
- the entry counters (n_ctx_mem, the [rag] ctx= log) count ALL formats
  (legacy 'Q: ', classic 'situation: ', action-first 'Memory (sim ..):').

Legacy rows (no seat) render WITHOUT the seat tag: single-seat provenance
is noise. The wire omits the empty seat key, so pre-seat JSON stays
byte-identical.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import rag as R
from membrane.field import Exemplar, ExemplarStore, _ex_to_wire, \
    _rec_from_wire, _seat_get, _seat_label
from membrane.opencode import _ctx_gave_summary, n_ctx_mem
from membrane.runtime import _seat_of


def _vec(w: str, dim: int = 32) -> np.ndarray:
    v = np.zeros(dim)
    for i, tok in enumerate(w.split()):
        v[i % dim] += 1.0 + (hash((tok, i)) % 97) / 97.0
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _ex(situation="q", action="a", seat="", extra=None):
    ex = Exemplar(situation=situation, action=action, outcome="pending",
                  vec=[1.0, 0.0], safe=True, extra=dict(extra or {}))
    ex.seat = seat
    return ex


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Mem:
    def __init__(self, store):
        self.store = store


class _Srv:
    def __init__(self, store):
        self.membrane = _Mem(store)
        self.rag_ctx_chars = 4000
        self.rag_k = 3
        self.rag_mem_chars = 200
        self.rag_sim_min = 0.0
        self.rag_mmr_sim = 0.99
        self.momentum = False
        self.global_field = None
        self.momentum_vec = None
        self.momentum_alpha = 0.0


class _Rag:
    """Just the methods under test, bound to a fake server."""
    _memory_block = R.RAGProxyHandler._memory_block
    _momentum_skew = R.RAGProxyHandler._momentum_skew
    _global_top_up = R.RAGProxyHandler._global_top_up


class TestSeatStamp(unittest.TestCase):
    def test_seat_get_legacy_row_is_empty(self):
        self.assertEqual(_seat_get(_ex()), "")

    def test_seat_get_dataclass_field(self):
        self.assertEqual(_seat_get(_ex(seat="proxy-8087")), "proxy-8087")

    def test_seat_get_extra_fallback(self):
        # a row written before the field upgrade carries the stamp in extra
        ex = _ex(extra={"seat": "proxy-9000"})
        ex.seat = ""
        self.assertEqual(_seat_get(ex), "proxy-9000")

    def test_seat_label_sits_only_on_seated_cells(self):
        self.assertEqual(_seat_label(_ex()), "")
        self.assertIn("[seat: proxy-8087]", _seat_label(_ex(seat="proxy-8087")))

    def test_seat_of_bare_membrane_is_empty(self):
        self.assertEqual(_seat_of(object()), "")

    def test_seat_of_seated_membrane(self):
        srv = type("S", (), {})()
        srv.peers = type("P", (), {"seat_id": "proxy-8087"})()
        mem = type("M", (), {})()
        mem.server_ref = srv
        self.assertEqual(_seat_of(mem), "proxy-8087")

    def test_wire_keeps_seated_row_omits_bare_row(self):
        rec = _ex_to_wire(_ex(seat="proxy-8087"))
        self.assertEqual(rec.get("seat"), "proxy-8087")
        back = _rec_from_wire(rec)
        self.assertEqual(back.seat, "proxy-8087")
        # legacy shape: no seat key on the wire at all
        bare = _ex_to_wire(_ex())
        self.assertNotIn("seat", bare)


class TestSituationResponseRender(unittest.TestCase):
    def _rag(self, store):
        r = _Rag()
        r.server = _Srv(store)
        return r

    def test_block_renders_situation_response_with_seat(self):
        store = _Store()
        store.append(Exemplar(
            situation="how do I run membrane tests",
            action=".venv/bin/python -m unittest discover -s tests",
            outcome="teed-pair", model="glm-5.3-flash:cloud",
            extra={"seat": "proxy-8087", "src": "user"},
            vec=list(_vec("how do I run membrane tests"))))
        r = self._rag(store)
        block = r._memory_block(_vec("run the tests"), messages=[])
        self.assertIsNotNone(block)
        self.assertIn("situation: how do I run membrane tests", block)
        self.assertIn("response: .venv/bin/python", block)
        self.assertIn("[seat: proxy-8087]", block)
        self.assertNotIn("Q: ", block)
        self.assertNotIn("\nA: ", block)

    def test_block_legacy_row_renders_without_seat(self):
        store = _Store()
        store.append(Exemplar(
            situation="how do I run membrane tests",
            action=".venv/bin/python -m unittest discover -s tests",
            outcome="teed-pair", vec=list(_vec("how do I run membrane tests"))))
        r = self._rag(store)
        block = r._memory_block(_vec("run the tests"), messages=[])
        self.assertIsNotNone(block)
        self.assertIn("situation: how do I run membrane tests", block)
        self.assertNotIn("[seat:", block)

    def test_action_first_entry_keeps_situation_tag(self):
        store = _Store()
        # a short situation with a long action -> action-first render
        store.append(Exemplar(
            situation="why?",
            action=("the whole design rationale, spelled out at length, "
                    "well beyond twice the situation's length so the "
                    "action-first branch fires"),
            outcome="teed-pair", extra={"seat": "proxy-8087"},
            vec=list(_vec("why?"))))
        r = self._rag(store)
        block = r._memory_block(_vec("why"), messages=[])
        self.assertIsNotNone(block)
        self.assertIn("Memory (sim ", block)
        self.assertIn("[seat: proxy-8087]:", block)
        self.assertIn("[situation:", block)


class TestEntryCounters(unittest.TestCase):
    def test_n_ctx_mem_counts_all_formats(self):
        ctx = ("Recalled memories:\n\n"
               "situation: a classic entry\n"
               "response: its answer (sim 0.80) [src: user]\n"
               "----\n"
               "Q: a legacy entry\nA: its answer\n"
               "----\n"
               "Memory (sim 0.80, [src: user]) [seat: proxy-8087]: the fact "
               "[situation: the original ask]")
        self.assertEqual(n_ctx_mem(ctx), 3)
        self.assertEqual(n_ctx_mem(None), 0)

    def test_ctx_gave_summary_parses_all_formats(self):
        ctx = ("Recalled memories:\n\n"
               "situation: a classic entry\n"
               "response: its answer\n"
               "----\n"
               "Q: a legacy entry\n"
               "A: its answer\n"
               "----\n"
               "Memory (sim 0.80, [src: user]) [seat: proxy-8087]: the fact "
               "[situation: the original ask]")
        gave = _ctx_gave_summary(ctx, cap=5)
        self.assertEqual(gave, ["a classic entry", "a legacy entry",
                                "the fact"])


if __name__ == "__main__":
    unittest.main()