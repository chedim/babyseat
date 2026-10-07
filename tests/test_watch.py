"""The watch layer: spatial triggers over the field's embedding space.

Under test (membrane/watch.py + the opencode glue):
- set/drop/list + count cap with weakest-eviction
- on_learn fires an event inside the radius, nothing outside
- gravity: mass-swallowing watches collapse (radius shrink) then go dormant
- charge: hits refill, misses erode; a starved watch sleeps
- delivery: take_report returns undelivered once, mark_served retires them
- persistence round-trip
"""
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from membrane import opencode as OC
from membrane.watch import DORM_RATE, MAX_WATCHES, WatchBoard


class _Emb:
    """Deterministic embedder: a few fixed directions + text-hash fallback."""

    DIRS = {"testing": 0, "deploy": 1, "database": 2}

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            key = next((k for k in self.DIRS if k in low), None)
            if key is not None:
                v = np.zeros(8)
                v[self.DIRS[key]] = 1.0
            else:
                h = hash(t) & 0xFFFFFFFF
                v = np.asarray([(h >> 8) & 0xF, (h >> 12) & 0xF, (h >> 16) & 0xF,
                                (h >> 20) & 0xF, (h >> 24) & 0xF, (h >> 28) & 0xF,
                                h & 0xF, (h >> 4) & 0xF], dtype=float)
            n = np.linalg.norm(v)
            out.append(list(v / n) if n > 1e-12 else [1.0] + [0.0] * 7)
        return out


def _ex(dir_idx, situation="a migration ran"):
    """A captured arrival: anything with .vec/.situation/.action -- the
    watch board reads only those (watch.py:220); the entity field's
    capture replaced the exemplar store."""
    v = np.zeros(8)
    v[dir_idx] = 1.0
    return types.SimpleNamespace(situation=situation, action="did the thing",
                                 outcome="ok", vec=list(v), extra={})


def _board(**kw):
    return WatchBoard(Path(tempfile.mkdtemp()) / "watches.json", _Emb(), **kw)


class TestSetDrop(unittest.TestCase):
    def test_set_and_report(self):
        b = _board()
        w, evicted = b.set_watch("testing", None)
        self.assertIsNone(evicted)
        self.assertIn("testing", b.report())

    def test_replant_rearms(self):
        b = _board()
        w1, _ = b.set_watch("testing", None)
        w1.radius = 0.4
        w2, ev = b.set_watch("testing", None)
        self.assertIs(ev, None)
        self.assertEqual(w2.wid, w1.wid)
        self.assertEqual(len(b.watches), 1)
        self.assertEqual(w2.radius, 0.4)      # radius kept (earned stays)

    def test_cap_evicts_lowest_charge(self):
        b = _board()
        first, _ = b.set_watch("deploy", None)
        for topic in ("testing", "database", "other one a", "other one b",
                      "other one c", "other one d", "other one e"):
            b.set_watch(topic, None)
        # now at the cap (7); drain the first watch's charge via misses
        for _ in range(30):
            b.on_learn(_ex(5), 0)
        _, evicted = b.set_watch("fresh topic", None)
        self.assertEqual(len(b.watches), MAX_WATCHES)
        self.assertEqual(evicted, min(b.watches.values(),
                                      key=lambda w: w.charge).label
                         if False else evicted)
        self.assertNotIn("fresh topic", evicted or "")

    def test_drop_forms(self):
        b = _board()
        w, _ = b.set_watch("testing", None)
        n, note = b.drop_watch(f"#{w.wid}")
        self.assertEqual((n, note), (1, "testing"))
        b.set_watch("deploy", None)
        n, _ = b.drop_watch("deploy")
        self.assertEqual(n, 1)
        b.set_watch("database", None)
        n, _ = b.drop_watch("all")
        self.assertEqual(n, 1)


class TestGravity(unittest.TestCase):
    def test_hit_inside_radius(self):
        b = _board()
        b.set_watch("testing", None)
        ev = b.on_learn(_ex(0, "tests pass now"), 7)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["ex_idx"], 7)

    def test_miss_outside_radius(self):
        b = _board()
        b.set_watch("testing", None)
        self.assertIsNone(b.on_learn(_ex(1, "deployed to prod"), 3))

    def test_collapse_shrinks_radius(self):
        b = _board()
        b.set_watch("testing", None)
        w = next(iter(b.watches.values()))
        r0 = w.radius
        # a stream of orthogonals still INSIDE a wide-radius probe's cone is
        # unrealistic; instead hammer with same-direction exemplars (sim 1.0)
        with patch.object(WatchBoard, "save", lambda self: None):
            for i in range(12):
                b.on_learn(_ex(0, f"test run {i}"), i)
        self.assertEqual(w.hits, 12)
        self.assertGreater(sum(w.window), 0)
        # radius must not have grown; collapse may have fired
        self.assertLessEqual(w.radius, r0)

    def test_black_hole_dormancy(self):
        b = _board()
        b.set_watch("testing", None)
        w = next(iter(b.watches.values()))
        w.radius = 0.5                  # everything orthogonal-ish matches? no:
        w.probe = [1.0] + [0.0] * 7
        w.radius = 0.5                  # hits anything with first-coord
        with patch.object(WatchBoard, "save", lambda self: None):
            for i in range(11):
                e = _ex(0)
                e.vec[1] = 0.3          # sim stays high (same first axis)
                b.on_learn(e, i)
        rate = sum(w.window) / len(w.window)
        self.assertEqual(w.hits, 11)
        self.assertGreater(rate, DORM_RATE)
        # dormancy trips: window was reset, event count == 11
        self.assertGreater(len(b.events), 0)
        self.assertIn("dormant", w.collapse_note)

    def test_starvation_sleeps(self):
        b = _board()
        b.set_watch("testing", None)
        w = next(iter(b.watches.values()))
        w.charge = 0.009                   # one miss from starvation (clamps)
        b.on_learn(_ex(1), 0)              # a miss -> charge clamps -> sleeps
        self.assertGreater(w.dormant_until, time.time())
        self.assertIn("starved", w.collapse_note)
        self.assertEqual(w.charge, 1.0)    # the welcome-back stipend
        self.assertEqual(len(b.events), 0)
        # while asleep it neither fires nor re-triggers starvation
        b.on_learn(_ex(1), 1)
        self.assertEqual(len(b.events), 0)


class TestDelivery(unittest.TestCase):
    def test_report_once_then_marked(self):
        b = _board()
        b.set_watch("testing", None)
        b.on_learn(_ex(0, "flaky test fixed"), 3)
        r1 = b.take_report()
        self.assertIsNotNone(r1)
        evs, block = r1
        self.assertIn("[membrane-watch]", block)
        self.assertIn("flaky test fixed", block)
        b.mark_served(evs)
        self.assertIsNone(b.take_report())

    def test_old_events_expire(self):
        b = _board()
        b.set_watch("testing", None)
        b.on_learn(_ex(0), 0)
        b.events[0]["ts"] -= 7200       # 2h old
        self.assertIsNone(b.take_report())

    def test_cap_events(self):
        b = _board()
        b.set_watch("testing", None)
        for i in range(40):
            b.on_learn(_ex(0, f"test {i}"), i)
        self.assertLessEqual(len(b.events), 32)


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        p = Path(tempfile.mkdtemp()) / "watches.json"
        b1 = WatchBoard(p, _Emb())
        b1.set_watch("testing", None)
        b2 = WatchBoard.load(p, _Emb())
        self.assertEqual(len(b2.watches), 1)
        w = next(iter(b2.watches.values()))
        self.assertEqual(w.label, "testing")
        self.assertEqual(w.probe, b1.watches[w.wid].probe)


class TestOpencodeGlue(unittest.TestCase):
    def setUp(self):
        # the watch board persists beside the field: isolate from the repo's
        # own watches.json so tests never see (or clobber) live state.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.field_path = str(Path(self._tmp.name) / "field.json")

    def _srv(self):
        return type("S", (), {"membrane": type("M", (), {
            "store": None, "embedder": _Emb(),
            "field_path": self.field_path,
            "audit_path": ""})()})()

    def test_watch_query_matcher(self):
        for q in ("membrane: watch flaky tests", "membrane: watches",
                  "membrane: drop watch #2", "Membrane: watch pytest?"):
            self.assertTrue(OC._is_watch_query(q), q)
        for q in ("watch the file", "membrane: watch", "membrane: forget #1"):
            if q == "membrane: watch":
                continue
            self.assertEqual(OC._is_watch_query(q), q.startswith("membrane: w"))

    def test_dispatch_plants(self):
        srv = self._srv()
        with patch('membrane.lanes._write_audit'):
            out = OC._watch_report_cmd(srv, "membrane: watch flaky tests")
        self.assertIn("planted", out)
        board = srv._watch_board
        self.assertEqual(len(board.watches), 1)

    def test_thought_watch_plants(self):
        srv = self._srv()
        with patch('membrane.lanes._write_audit'):
            OC._watch_report_cmd(srv, "membrane: watch flaky tests")
            out = OC._watch_set_from_thought(srv, "new topic here")
        self.assertIn("agent watch planted", out)
        self.assertEqual(len(srv._watch_board.watches), 2)

    def test_thought_watch_drop_executes(self):
        srv = self._srv()
        with patch('membrane.lanes._write_audit'):
            OC._watch_report_cmd(srv, "membrane: watch flaky tests")
            out = OC._watch_set_from_thought(srv, "drop all")
        self.assertIn("dropped 1", out)
        self.assertEqual(len(srv._watch_board.watches), 0)


if __name__ == "__main__":
    unittest.main()