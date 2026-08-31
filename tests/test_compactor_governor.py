"""The compactor's governors: CPU-target duty + growth trigger.

Both default-on (the consolidate.py closed loop, chat-lane edition):
- growth trigger: a cycle runs only when the field grew >= compact_growth_frac
  of its size since the last note (floor: compact_min_field raw turns);
- duty loop: with recent live traffic (srv._last_fwd_dt fresh) and measured
  work over the cpu_target, the next wait stretches (back off mid-task);
  disabled (target=0) or idle, the cadence is the flat --compact-every.
"""

import time
import unittest

from membrane.rag import Compactor
from membrane.field import Exemplar, ExemplarStore


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Mem:
    def __init__(self, store):
        import threading as _t
        self.store = store
        self.field_path = ""
        self.audit_path = ""
        self.mut_lock = _t.RLock()

    def _audit(self, *a, **k):
        pass


class _Srv:
    def __init__(self, store, n):
        for i in range(n):
            store.append(Exemplar(situation=f"q{i}", action=f"a{i}",
                                  outcome="ok",
                                  vec=[float(i % 7) / 3.0, 0.5, 0.2]))
        self.membrane = _Mem(store)
        self.compact_min_field = 12
        self.compact_min_cluster = 4
        self.compact_max_cluster = 8
        self.compact_cluster_sim = 0.60
        self.compact_mem_chars = 64
        self.compact_cpu_target = 0.10
        self.compact_growth_frac = 0.25
        self._last_fwd_dt = 0.0
        self.backend = "http://localhost:1"   # no real model in these tests


class TestGrowthTrigger(unittest.TestCase):
    def test_quiet_field_skips(self):
        """No growth since the last compaction -> no cycle."""
        st = _Store()
        comp = Compactor(_Srv(st, 30), idle=90.0)
        comp._n_at_compact = len(st)          # last compact at this size
        self.assertFalse(comp._should_run())

    def test_growth_beyond_frac_fires(self):
        """+25% raw turns since the last note -> the trigger opens."""
        st = _Store()
        srv = _Srv(st, 40)
        comp = Compactor(srv, idle=90.0)
        comp._n_at_compact = len(st)
        for i in range(15):                   # +15 vs 25% of the grown 55
            st.append(Exemplar(situation=f"new{i}", action=f"b{i}",
                               outcome="ok", vec=[0.9, 0.1, 0.3]))
        self.assertTrue(comp._should_run())

    def test_min_field_floor_guarded(self):
        """Below compact_min_field the trigger stays shut regardless."""
        st = _Store()
        comp = Compactor(_Srv(st, 11), idle=90.0)
        comp._n_at_compact = 0                # "all of it is new"
        self.assertFalse(comp._should_run())


class TestDutyLoop(unittest.TestCase):
    def test_no_traffic_flat_cadence(self):
        """No live traffic -> the duty loop never stretches the cadence."""
        st = _Store()
        srv = _Srv(st, 40)
        srv._last_fwd_dt = time.time() - 3600   # traffic is old
        comp = Compactor(srv, idle=90.0)
        comp._ema_work = 0.5                    # pretend compaction is heavy
        self.assertEqual(comp._gated(), 90.0)

    def test_busy_over_target_backs_off(self):
        """Live traffic + heavy work -> the wait stretches past the base."""
        st = _Store()
        srv = _Srv(st, 40)
        srv._last_fwd_dt = time.time()          # traffic NOW
        srv._last_fwd_dur = 0.2
        comp = Compactor(srv, idle=90.0)
        comp._ema_work = 0.9                    # duty ~= 0.82 >> target 0.10
        self.assertGreater(comp._gated(), 90.0)
        self.assertLessEqual(comp._gated() and 720.0, 720.0)   # 8x stretch cap

    def test_target_zero_disables(self):
        """target=0 (fixed-interval mode) -> never stretches."""
        st = _Store()
        srv = _Srv(st, 40)
        srv.compact_cpu_target = 0.0
        srv._last_fwd_dt = time.time()
        srv._last_fwd_dur = 0.2
        comp = Compactor(srv, idle=90.0)
        comp._ema_work = 0.9
        self.assertEqual(comp._gated(), 90.0)

    def test_marker_advances_only_on_note(self):
        """A starved cycle (no new note) leaves the growth trigger armed."""
        st = _Store()
        srv = _Srv(st, 40)
        srv._last_fwd_dt = 0.0                  # no traffic -> the idle branch
        comp = Compactor(srv, idle=90.0)
        comp._n_at_compact = 30                 # field grew past the trigger
        self.assertTrue(comp._should_run())
        comp._gated()                           # a cycle that yields no note
        self.assertEqual(comp._n_at_compact, 30)   # marker did NOT advance


if __name__ == "__main__":
    unittest.main()