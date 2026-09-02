"""The compactor night: junk sweep (consensus + cap), gray-band
registration, entity materialization + consolidation. Deterministic:
a keyword-separable fake embedder (no Ollama, no Couchbase)."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, QuantumEntity
from membrane.night import (sweep_junk, register_gray_forms,
                            consolidate_entities)

JUNK_WORDS = ("pid ", "command ", "traceback", "lsof", "dump", "raw",
              "listing", "log line", "blob", "hex", "minified",
              "shebang", "columns")


class FakeEmb:
    """2-d keyword-separable embedder: junk keywords -> dim 0, else dim 1."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 8
            tl = str(t).lower()
            if any(k in tl for k in JUNK_WORDS):
                v[0] = 1.0
            else:
                v[1] = 1.0
            out.append(v)
        return out


class Ex:
    def __init__(self, sit, safe=True, vec=None, born=None, tags=None):
        self.situation, self.action, self.safe = sit, "", safe
        self.extra, self.model, self.vec, self.outcome = {}, "", vec, ""
        if born:
            self.extra["eph_born"] = born
        if tags:
            self.extra["entities"] = tags


class Store:
    def __init__(self, cells):
        self.exemplars = cells
        self.mut_lock = type("L", (), {
            "__enter__": lambda s: None,
            "__exit__": lambda s, *a: None})()
        self.field_path = "/tmp/never.json"
        self.saved = 0

    def __len__(self):
        return len(self.exemplars)

    def set_safe(self, i, safe):
        self.exemplars[i].safe = safe

    def save(self, p):
        self.saved += 1


class Mem:
    def __init__(self, store, emb):
        self.store, self.embedder, self.mut_lock = store, emb, store.mut_lock
        self.field_path = "/tmp/never.json"
        self.gate_notes = []


class FakeSrv:
    backend, idle_cortex_model = "fake", "fake-model"


def fake_chat(reply: str):
    def _chat(backend, model, msgs, timeout=None):
        return reply
    return _chat


ANCHOR_REPLY = ('{"anchor": "raw command-line process dumps with pid and '
                'path", "entities": [{"class": "FailureMode", "name": '
                '"gray-band-probe", "summary": "a durable fact from the '
                'gray band"}]}')


class TestSweep(unittest.TestCase):
    def _world(self, n_junk=20, n_work=5):
        emb = FakeEmb()
        est = EntityStore(path=None)          # RAM-only
        junk = [Ex(f"PID {i} COMMAND /usr/bin/thing {i}",
                   vec=emb.embed([f"PID {i} COMMAND x"])[0])
                for i in range(n_junk)]
        work = [Ex(f"we fixed the banner junk screen {i}",
                   vec=emb.embed([f"we fixed the banner junk screen {i}"])[0])
                for i in range(n_work)]
        cells = junk + work
        store = Store(cells)
        return emb, est, cells, Store(cells), Mem(store, emb), store

    def test_consensus_demotes_junk_not_work(self):
        emb, est, cells, store, mem, _ = self._world()
        res = sweep_junk(mem, store, est)
        self.assertEqual(res["demoted"], 20)      # all junk
        self.assertTrue(all(not c.safe for c in cells[:20]))
        self.assertTrue(all(c.safe for c in cells[20:]))   # work survives

    def test_provenance_landed(self):
        emb, est, cells, store, mem, _ = self._world()
        sweep_junk(mem, store, est)
        nj = cells[0].extra.get("night_junk")
        self.assertIsNotNone(nj)
        self.assertEqual(nj["origin"], "night-sweep")
        self.assertGreaterEqual(nj["sim"], 0.70)
        self.assertEqual(nj["cut"], 0.70)

    def test_demote_cap_bounds_blast_radius(self):
        emb, est, cells, store, mem, _ = self._world(n_junk=60, n_work=5)
        res = sweep_junk(mem, store, est)
        self.assertLessEqual(res["demoted"], 50)   # the absolute cap
        self.assertGreater(res["demoted"], 0)

    def test_single_anchor_cannot_kill(self):
        """CONSENSUS: a cell near ONE hot anchor (possibly mis-aimed)
        survives; near >= 2 anchors it demotes."""
        emb, est, cells, store, mem, _ = self._world(n_junk=3, n_work=5)
        # leave only ONE junk cell: its 3 cells hit ~10 dim-0 anchors --
        # consensus passes; the work cells near only seed-9 must survive
        res = sweep_junk(mem, store, est)
        self.assertTrue(all(c.safe for c in cells[3:]))
        # the 3 junk cells: 10 anchors near -> demoted
        self.assertTrue(all(not c.safe for c in cells[:3]))


class TestRegistration(unittest.TestCase):
    def test_anchor_and_entity_growth_rate_limited(self):
        emb = FakeEmb()
        est = EntityStore(path=None)
        junk = [Ex(f"PID {i} COMMAND thing", vec=emb.embed([f"PID {i} COMMAND x"])[0])
                for i in range(4)]
        work = [Ex(f"we fixed the screen {i}", vec=emb.embed([f"clean {i}"])[0])
                for i in range(5)]
        cells = junk + work
        store = Store(cells)
        mem = Mem(store, emb)
        res = sweep_junk(mem, store, est)
        gray = res.get("_gray_idx") or []
        reg = register_gray_forms(FakeSrv(), mem, est, gray,
                                  chat=fake_chat(ANCHOR_REPLY))
        self.assertEqual(reg["anchors"], 1)     # the rate limit
        self.assertEqual(reg["entities"], 1)
        self.assertEqual(sum(1 for q in est.docs.values()
                             if "junk-anchor:night" in q.eid), 1)
        self.assertIsNotNone(est.docs.get("membrane::synth:gray-band-probe"))

    def test_no_gray_no_call(self):
        emb = FakeEmb()
        est = EntityStore(path=None)
        cells = [Ex("clean prose", vec=emb.embed(["clean prose"])[0])]
        store = Store(cells)
        mem = Mem(store, emb)
        calls = []

        def counting_chat(b, m, msgs):
            calls.append(1)
            return ANCHOR_REPLY

        reg = register_gray_forms(FakeSrv(), mem, est, [],
                                  chat=counting_chat)
        self.assertEqual(reg, {"anchors": 0, "entities": 0})
        self.assertEqual(calls, [])


class TestConsolidation(unittest.TestCase):
    def _world(self):
        emb = FakeEmb()
        est = EntityStore(path=None)
        now = time.time()
        cells = []
        for i in range(6):
            sit = ("work on router.py ladder" if i % 2 == 0
                   else "router.py failover")
            c = Ex(sit, vec=emb.embed([sit])[0], born=now - i * 600,
                   tags={"files": ["router.py"]})
            cells.append(c)
        store = Store(cells)
        return emb, est, Store(cells), Mem(store, emb), now

    def test_materialize_and_seed(self):
        emb, est, store, mem, now = self._world()
        r = consolidate_entities(mem, est)
        self.assertEqual(r["materialized"], 1)
        ent = est.docs.get("membrane::files:router.py")
        self.assertIsNotNone(ent)
        self.assertTrue(ent.position)
        self.assertEqual(len(ent.position_history), 1)

    def test_quiet_cycle_no_reseed(self):
        emb, est, store, mem, now = self._world()
        consolidate_entities(mem, est)
        r2 = consolidate_entities(mem, est)
        self.assertEqual(r2["materialized"], 0)
        ent = est.docs.get("membrane::files:router.py")
        self.assertEqual(len(ent.position_history), 1)   # no motion, no entry


if __name__ == "__main__":
    unittest.main()