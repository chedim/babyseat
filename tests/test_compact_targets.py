"""THE METRIC-TARGETED AUTO-COMPACT (the operator's ask): the compactor
runs when a METRIC crosses a target, not only on growth -- (1) the size
target (--compact-max-field: the field exceeds the budget -> compact
every interval), (2) the raw-ratio target (--compact-max-raw-frac:
un-distilled canon over the fraction -> compact; the marker is the
compactor's own outcome stamp compacted@<c>)."""

import unittest

from membrane.field import Exemplar
from membrane.rag import Compactor


class _Mem:
    def __init__(self, exemplars):
        self.store = _Store(exemplars)

    def __len__(self):
        return len(self.store.exemplars)


class _Store:
    def __init__(self, exemplars):
        self.exemplars = exemplars

    def __len__(self):
        return len(self.exemplars)


class _Srv:
    def __init__(self, exemplars, **kw):
        self.membrane = _Mem(exemplars)
        self.compact_min_field = kw.get("compact_min_field", 4)
        self.compact_min_cluster = kw.get("compact_min_cluster", 2)
        self.compact_growth_frac = kw.get("compact_growth_frac", 0.25)
        self.compact_max_field = kw.get("compact_max_field", 0)
        self.compact_max_raw_frac = kw.get("compact_max_raw_frac", 0.0)
        self.compact_max_cluster = kw.get("compact_max_cluster", 4)
        self.compact_cluster_sim = kw.get("compact_cluster_sim", 0.6)
        self.compact_mem_chars = 200
        self.compact_cpu_target = 0.0


def _ex(i, compacted=False, ephemeral=False, safe=True):
    from membrane.field import Exemplar
    ex = Exemplar(situation=f"situation {i}", action="answer",
                  outcome=(f"compacted@1" if False else "done"),
                  vec=[float(i % 5), 1.0, 0.0])
    ex.safe = True
    return ex


class TestMetricTargets(unittest.TestCase):
    def _compactor(self, exemplars, **kw):
        srv = _Srv(exemplars, **kw)
        c = Compactor.__new__(Compactor)
        c.srv = srv
        # no growth since the last note (the metric targets are under
        # test -- the growth trigger must stay out of the picture)
        c._n_at_compact = len(exemplars)
        return c

    def test_size_target_fires_over_budget(self):
        """The field over --compact-max-field -> the trigger fires every
        interval (the compactor holds the field at a size)."""
        exs = [_ex(i) for i in range(10)]
        c = self._compactor(exs, compact_max_field=8)
        self.assertTrue(c._should_run())
        c2 = self._compactor(exs, compact_max_field=100)
        self.assertFalse(c2._should_run())   # under budget, no growth

    def test_raw_ratio_fires_when_undistilled(self):
        """Raw canon over the raw-frac -> compact; compacted rows don't
        count (the marker is outcome compacted@<c>)."""
        exs = [_ex(i) for i in range(10)]
        exs[0].outcome = "compacted@1"      # 1/10 distilled
        c = self._compactor(exs, compact_max_raw_frac=0.5)
        # raw = 9/10 = 0.9 > 0.25 -> fires (no growth: the METRIC fired)
        self.assertTrue(c._should_run())
        # now 8 of 10 compacted: raw = 2/10 = 0.2 <= 0.25 -> no fire
        for e in exs[2:10]:
            e.outcome = "compacted@1"
        self.assertFalse(c._should_run())

    def test_min_field_floor_holds(self):
        """The floor: a tiny field never compacts (any target)."""
        exs = [_ex(i) for i in range(3)]
        c = self._compactor(exs, compact_max_field=2,
                            compact_max_raw_frac=0.5)
        self.assertFalse(c._should_run())


if __name__ == "__main__":
    unittest.main()
