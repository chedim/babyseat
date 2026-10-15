"""THE WORLDLINES (2026-10-15): an entity is a process, not a point.

The gate (two seats' real fields, held-out recognition -- which entity is a new
mention about?): the fast `state` (gain 0.5) recognized worst of the tested shapes
(MRR 0.646 vs the mean's 0.724); entities under ~40 mentions are recognized best by
their MEAN; entities past ~40 by their best-matching EPISODE -- the centroid of a
contiguous run of 5 mentions -- +0.061 MRR over the mean on one seat, +0.083 on
another (pre-registered). Old entities live several phases and come back to them; a
mean blurs the phases; nothing drifts (fast trackers lost everywhere).

So each entity keeps its episodes (runs of EP_RUN mentions, capped at EP_MAX by
merging the most alike NEIGHBOURS, so distinct old phases survive), and recall()
scores a young entity by its mean and a mature one by its best episode, both
centered on the field's background -- and finds a mature entity through an old
episode even when its current state sits elsewhere."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

A = ["bilge", "pump", "float", "switch", "relay", "wiring", "breaker", "hose"]
B = ["chart", "legend", "colors", "palette", "axis", "tooltip", "gridline", "series"]
C = ["weather", "forecast", "wind", "rain", "tide", "fog", "gust", "swell"]
OLD, YOUNG = "membrane/old.py", "membrane/young.py"


def _lines(path, words, n, tag, seed):
    rng = np.random.default_rng(seed)
    return [f"{path} " + " ".join(rng.choice(words, 4, replace=False)) + f" {tag}{i}" for i in range(n)]


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _say(f, texts, t0=1000.0):
    evs = [ST.Event(stream="discourse", role="user", text=t, ts=t0 + i) for i, t in enumerate(texts)]
    f.write_events(evs, session="s1")
    f.process()


def _world():
    f = _field()
    # the old entity: a long phase about the pump, then a long phase about charts
    _say(f, _lines(OLD, A, 25, "pa", 1) + _lines(OLD, B, 25, "pb", 2))
    # a young one: some pump talk mixed with weather talk
    _say(f, _lines(YOUNG, A, 4, "ya", 3) + _lines(YOUNG, C, 6, "yc", 4), t0=5000.0)
    return f


def _q(words):
    return FakeEmbedder().embed([" ".join(words)])[0]


class TestTheEpisodes(unittest.TestCase):
    def test_a_life_is_kept_as_runs_of_mentions(self):
        f = _world()
        st = f.get("f:" + OLD)
        self.assertEqual(st.n, 50)
        self.assertEqual(sum(n for _, n in st.episodes), 50)
        self.assertEqual(len(st.episodes), 50 // EF.EP_RUN)
        self.assertEqual(st.ep_k, 0)

    def test_the_first_episodes_remember_the_first_phase(self):
        f = _world()
        st = f.get("f:" + OLD)
        a, b = EF.unit(_q(A)), EF.unit(_q(B))
        first, last = EF.unit(st.episodes[0][0]), EF.unit(st.episodes[-1][0])
        self.assertGreater(first @ a, first @ b)
        self.assertGreater(last @ b, last @ a)

    def test_a_reload_and_a_rebuild_keep_every_episode(self):
        f = _world()
        st = f.get("f:" + OLD)
        back = EF.EntityState.from_doc(st.to_doc(f.origin))
        self.assertEqual(len(back.episodes), len(st.episodes))
        for (v1, n1), (v2, n2) in zip(st.episodes, back.episodes):
            self.assertEqual(n1, n2)
            np.testing.assert_array_equal(v1, v2)
        before = [(v.copy(), n) for v, n in st.episodes]
        f.rebuild()
        again = f.get("f:" + OLD).episodes
        self.assertEqual([n for _, n in again], [n for _, n in before])
        for (v1, _), (v2, _) in zip(before, again):
            np.testing.assert_allclose(v1, v2, atol=1e-3)

    def test_the_cap_merges_the_most_alike_neighbours(self):
        with mock.patch.object(EF, "EP_MAX", 4):
            f = _world()
        st = f.get("f:" + OLD)
        self.assertLessEqual(len(st.episodes), 4)
        self.assertEqual(sum(n for _, n in st.episodes), 50)
        a, b = EF.unit(_q(A)), EF.unit(_q(B))
        # the two phases were never merged into one blur
        self.assertGreater(EF.unit(st.episodes[0][0]) @ a, EF.unit(st.episodes[0][0]) @ b)
        self.assertGreater(EF.unit(st.episodes[-1][0]) @ b, EF.unit(st.episodes[-1][0]) @ a)

    def test_words_keep_no_episodes(self):
        f = _world()
        for d in f.b.scan("entity_states", {"origin": f.origin}):
            eid = d[1].get("eid") or ""
            if eid.startswith(("w:", "p:")):
                self.assertFalse(d[1].get("episodes"), eid)


class TestRecallReadsTheWorldline(unittest.TestCase):
    def test_a_mature_entity_is_found_by_its_old_phase(self):
        f = _world()
        got = [e for e, _ in f.recall(_q(A), k=3, kinds=["f"])]
        self.assertEqual(got[0], "f:" + OLD, got)

    def test_a_young_entity_is_read_by_its_mean_not_its_last_mention(self):
        f = _field()
        _say(f, _lines(YOUNG, A, 8, "ya", 3) + _lines(YOUNG, C, 2, "yc", 4))
        _say(f, _lines("membrane/other.py", C, 8, "oc", 5), t0=3000.0)
        got = [e for e, _ in f.recall(_q(A), k=2, kinds=["f"])]
        self.assertEqual(got[0], "f:" + YOUNG, got)

    def test_the_kill_switch_ranks_by_state_again(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_WORLDLINES": "0"}):
            f = _world()
            got = [e for e, _ in f.recall(_q(A), k=3, kinds=["f"])]
        self.assertNotEqual(got[0], "f:" + OLD, got)


if __name__ == "__main__":
    unittest.main()
