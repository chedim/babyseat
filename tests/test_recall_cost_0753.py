"""0.7.53 (the operator's "recallastrophe"): on a big field (Memo's copy: 412K delta rows) one recall ask timed out
at 180 s. A full rebuild of the per-entity deltas index takes ~11 s there and ran UNDER THE FIELD LOCK on the ask
path whenever the index was invalidated, which a busy seat does again and again; the episode matrix rebuilt in a
Python loop on every closed episode. The rules now:
- once warm, deltas_of never rebuilds on the caller's path: a stale index is served and ONE background rebuild runs;
- the episode matrix is rebuilt with numpy (the background read once), not a per-point Python loop;
- the move-log backfill yields between batches."""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _say(f, texts, session="s1", t0=1000.0):
    f.write_events([ST.Event(stream="discourse", role="user", text=t, ts=t0 + i)
                    for i, t in enumerate(texts)], session=session)
    f.process()


def _world(n=40):
    f = _field()
    _say(f, [f"the release of membrane/mod{i % 7}.py needs the gradle fix number {i}" for i in range(n)])
    return f


class DeltasIndex(unittest.TestCase):
    def _count_scans(self, f):
        real = f.b.scan
        n = {"deltas": 0}

        def counted(coll, *a, **k):
            if coll == "entity_deltas":
                n["deltas"] += 1
            return real(coll, *a, **k)
        return n, mock.patch.object(f.b, "scan", side_effect=counted)

    def test_a_stale_index_is_served_and_rebuilt_in_the_background(self):
        f = _world()
        eid = next(e for e in f._states if e.startswith("f:"))
        before = f.deltas_of(eid)                      # warm
        self.assertTrue(before)
        f._invalidate_delta_index()                    # a delete / an out-of-order write elsewhere
        n, patch = self._count_scans(f)
        with patch:
            got = f.deltas_of(eid)                     # the ask path
            self.assertEqual(len(got), len(before))   # the stale copy, at once
            self.assertTrue(f.wait_delta_rebuild(5.0))
        self.assertEqual(n["deltas"], 1)               # exactly one rebuild, in the background

    def test_the_ask_path_never_scans_once_warm(self):
        f = _world()
        eid = next(e for e in f._states if e.startswith("f:"))
        f.deltas_of(eid)
        f._invalidate_delta_index()
        hold = threading.Event()
        real = f.b.scan

        def slow(coll, *a, **k):                       # a big field: the rebuild takes a while
            if coll == "entity_deltas":
                hold.wait(5.0)
            return real(coll, *a, **k)
        with mock.patch.object(f.b, "scan", side_effect=slow):
            t = time.time()
            for _ in range(5):
                f.deltas_of(eid)                       # never waits for the rebuild
            self.assertLess(time.time() - t, 1.0)
            hold.set()
            self.assertTrue(f.wait_delta_rebuild(5.0))

    def test_the_rebuild_does_not_hold_the_field_lock(self):
        f = _world()
        eid = next(e for e in f._states if e.startswith("f:"))
        f.deltas_of(eid)
        f._invalidate_delta_index()
        inside = threading.Event()
        free = threading.Event()
        real = f.b.scan

        def probe(coll, *a, **k):
            if coll == "entity_deltas":
                inside.set()
                got = f._lock.acquire(timeout=2.0)     # another thread could take the lock now
                if got:
                    f._lock.release()
                    free.set()
            return real(coll, *a, **k)
        with mock.patch.object(f.b, "scan", side_effect=probe):
            f.deltas_of(eid)
            self.assertTrue(f.wait_delta_rebuild(5.0))
        self.assertTrue(inside.is_set())
        self.assertTrue(free.is_set())

    def test_new_deltas_appear_after_the_rebuild(self):
        f = _world()
        eid = next(e for e in f._states if e.startswith("f:"))
        n0 = len(f.deltas_of(eid))
        f._invalidate_delta_index()
        f.deltas_of(eid)
        self.assertTrue(f.wait_delta_rebuild(5.0))
        _say(f, ["the release of membrane/mod0.py needs the gradle fix again"], t0=5000.0)
        f.deltas_of(eid)
        f.wait_delta_rebuild(5.0)
        mod0 = "f:membrane/mod0.py"
        self.assertGreaterEqual(len(f.deltas_of(mod0)), 1)
        self.assertGreaterEqual(len(f.deltas_of(eid)), n0)


class EpisodeMatrix(unittest.TestCase):
    def test_the_rebuild_reads_the_background_once(self):
        f = _field()
        bg = np.random.default_rng(0).normal(size=64)
        f._ostats["bg"]["discourse"] = EF.pack_f16(bg)
        rows = {f"f:x{i}.py": {"eps": [], "covered": True,
                               "pts": [np.random.default_rng(i).normal(size=64) for _ in range(3)]}
                for i in range(200)}
        f._ep_index = {"rows": rows, "M": None}
        calls = {"n": 0}
        real = f._bg

        def counted(stream):
            calls["n"] += 1
            return real(stream)
        with mock.patch.object(f, "_bg", side_effect=counted):
            got = f._ep_hits(EF.unit(np.ones(64)), 8)
        self.assertEqual(len(got), 8)
        self.assertLessEqual(calls["n"], 2)            # not once per point (600)

    def test_same_hits_as_the_per_point_loop(self):
        f = _field()
        bg = np.random.default_rng(0).normal(size=64)
        f._ostats["bg"]["discourse"] = EF.pack_f16(bg)
        rows = {f"f:x{i}.py": {"eps": [], "covered": True,
                               "pts": [np.random.default_rng(100 + i * 3 + j).normal(size=64) for j in range(3)]}
                for i in range(50)}
        f._ep_index = {"rows": rows, "M": None}
        q = EF.unit(np.random.default_rng(7).normal(size=64))
        got = f._ep_hits(f._centered(q), 8)
        owner, pts = [], []
        for eid, row in rows.items():
            for v in row["pts"]:
                owner.append(eid); pts.append(f._centered(v))
        sims = np.asarray(pts, np.float32) @ np.asarray(f._centered(q), np.float32)
        want = []
        for i in np.argsort(-sims):
            if owner[i] not in want:
                want.append(owner[i])
            if len(want) == 8:
                break
        self.assertEqual(got, want)


    def _stale_world(self):
        f = _field()
        bg = np.random.default_rng(0).normal(size=64)
        f._ostats["bg"]["discourse"] = EF.pack_f16(bg)
        rows = {f"f:x{i}.py": {"eps": [], "covered": True,
                               "pts": [np.random.default_rng(100 + i * 3 + j).normal(size=64) for j in range(3)]}
                for i in range(50)}
        f._ep_index = {"rows": rows, "M": None}
        return f, rows

    def test_a_closed_episode_serves_the_last_matrix_while_one_rebuild_runs(self):
        import threading
        import time
        f, rows = self._stale_world()
        q = EF.unit(np.random.default_rng(7).normal(size=64))
        first = f._ep_hits(f._centered(q), 8)           # nothing built yet: this one builds
        gate = threading.Event()
        started = {"n": 0}
        real = f._ep_build

        def slow(idx):
            started["n"] += 1
            gate.wait(5.0)
            return real(idx)
        rows["f:new.py"] = {"eps": [], "covered": True, "pts": [np.asarray(q, float)]}
        with mock.patch.object(f, "_ep_build", side_effect=slow):
            f._ep_dirty()                                # what an episode close does
            t0 = time.time()
            got1 = f._ep_hits(f._centered(q), 8)
            got2 = f._ep_hits(f._centered(q), 8)
            self.assertLess(time.time() - t0, 2.0)       # neither waited for the gated rebuild
            self.assertEqual(got1, first)                # the last matrix answered
            self.assertEqual(got2, first)
            self.assertEqual(started["n"], 1)            # one rebuild, not one per recall
            gate.set()
            self.assertTrue(f.wait_ep_rebuild(5.0))
        self.assertEqual(f._ep_hits(f._centered(q), 8)[0], "f:new.py")   # the new episode is in once it lands

    def test_a_dirty_matrix_that_never_existed_is_built_in_place(self):
        f, rows = self._stale_world()
        f._ep_dirty()
        q = EF.unit(np.random.default_rng(7).normal(size=64))
        self.assertEqual(len(f._ep_hits(f._centered(q), 8)), 8)         # first build still answers at once


class BackfillYields(unittest.TestCase):
    def test_the_move_log_backfill_sleeps_between_batches(self):
        from membrane import movelog as ML
        self.assertGreater(ML.BACKFILL_YIELD_S, 0)


if __name__ == "__main__":
    unittest.main()


class Warm(unittest.TestCase):
    def test_boot_builds_the_index_in_the_background(self):
        f = _world()
        f._deltas_index = None
        f.warm_delta_index()
        self.assertTrue(f.wait_delta_rebuild(5.0))
        self.assertIsNotNone(f._deltas_index)
        n, patch = DeltasIndex()._count_scans(f)
        with patch:
            f.deltas_of(next(e for e in f._states if e.startswith("f:")))
        self.assertEqual(n["deltas"], 0)

    def test_the_seat_warms_it_at_boot(self):
        src = (Path(__file__).parent.parent / "membrane" / "opencode.py").read_text()
        self.assertIn("srv.efield.warm_delta_index()", src)


class ReviewFixes(unittest.TestCase):
    """#1107 review: (1) the move-log backfill keeps the index current itself -- no full rebuilds; (2) a forget
    vanishes from deltas_of at once, never served stale; (3) the move flush's rewrites add no duplicates."""

    def _count_builds(self, f):
        real = f._build_delta_index
        n = {"builds": 0}

        def counted():
            n["builds"] += 1
            return real()
        return n, mock.patch.object(f, "_build_delta_index", side_effect=counted)

    def test_the_backfill_triggers_no_full_rebuild(self):
        f = _world(60)
        f.flush()
        eid = next(e for e in f._states if e.startswith("f:"))
        for k, d in list(f.b.scan("entity_deltas", {"origin": f.origin})):
            if str(d.get("eid") or "").startswith("e:") and d.get("mv"):
                f.b.upsert("entity_deltas", k, {kk: vv for kk, vv in d.items() if kk not in ("mv", "moves", "msum")})
        f.b.delete("annotations", "mvdone:" + f.origin)
        f.rebuild_delta_index()                        # warm and current before the backfill
        n, patch = self._count_builds(f)
        real_um = f.b.upsert_multi

        def bumping(coll, docs):                       # a backend whose rewrites move write_gen (the review's trigger)
            real_um(coll, docs)
            if coll == "entity_deltas" and any(str(d.get("eid") or "").startswith("e:") and d.get("mv")
                                               for d in docs.values()):
                f.b.write_gen["entity_deltas"] = f.b.write_gen.get("entity_deltas", 0) + 1
        with patch, mock.patch.object(f.b, "upsert_multi", side_effect=bumping):
            done = f.move_log.backfill(f, batch=5)     # many batches
            for i in range(3):
                _say(f, ["the release of membrane/mod1.py needs the gradle fix again"], t0=9000.0 + i)
                f.flush()
                f.deltas_of(eid)
            f.wait_delta_rebuild(5.0)
        self.assertGreater(done, 5)
        self.assertEqual(n["builds"], 0)
        self.assertFalse(f._deltas_stale)

    def test_a_forget_vanishes_at_once(self):
        f = _world(20)
        f.flush()
        eid = next(e for e in f._states if e.startswith("f:"))
        rows = f.deltas_of(eid)
        ev_key = rows[0]["event"]
        num = f.b.get("events", ev_key).get("n")
        real = f._build_delta_index
        gate = threading.Event()

        def slow():                                    # a big field: the background rebuild lags
            gate.wait(5.0)
            return real()
        with mock.patch.object(f, "_build_delta_index", side_effect=slow):
            f.forget(num)
            got = f.deltas_of(eid)                     # immediately, the rebuild still blocked
            self.assertNotIn(ev_key, [d["event"] for d in got])
            gate.set()
            f.wait_delta_rebuild(5.0)

    def test_the_move_flush_adds_no_duplicates(self):
        f = _world(30)
        f.flush()
        for e in list(f._states):
            f.deltas_of(e)
        for e, lst in (f._deltas_index or {}).items():
            pos = [(d.get("seq"), d.get("sidx")) for d in lst]
            self.assertEqual(len(pos), len(set(pos)), e)
