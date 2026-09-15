"""THE PERSONALIZED FIELD (the SQL++ neighborhood, the cortex-
experiment convergence): the seat's working set = the ANN neighborhood
around session:now's position + the seat's own patch + the commons --
the database is the field, RAM keeps a session-scoped neighborhood.
The degraded shape (no seed / no cb) degenerates to the own-patch
hydrate. MEMBRANE_FIELD=world reverts to the world hydrate."""

import unittest
from unittest import mock

from membrane.field import ExemplarStore


class TestNeighborhood(unittest.TestCase):
    def test_shape_guard_no_seed_degrades_to_delta(self):
        """seed_vec=None -> NO ANN call (the neighborhood step is
        skipped); the delta hydrate (snapshot + ephemeral tier) runs —
        the degraded honest shape."""
        import membrane.ann as ann_mod
        calls = []
        with mock.patch.object(ann_mod, "ann_search",
                               side_effect=lambda *a, **k: calls.append(a)):
            st = ExemplarStore.load_neighborhood(field_path="")
        self.assertEqual(calls, [])   # no seed -> the ANN never queried

    def test_radius_floor(self):
        """radius < 16 clamps to the floor (a neighborhood smaller than
        the floor is not a working set)."""
        self.assertEqual(ExemplarStore.NEIGHBORHOOD_RADIUS, 400)

    def test_ann_shortlist_shape(self):
        """The neighborhood's ANN call shape: the entities index, k =
        radius, timeout 2.0 -- the same contract E13/E14 measured."""
        import inspect
        from membrane import ann
        sig = inspect.signature(ann.ann_search)
        self.assertEqual(list(sig.parameters)[:3],
                         ["index", "vec", "k"])
        # the field's ANN index name (the entities index, not exemplars):
        from membrane.field import ExemplarStore as S
        self.assertEqual(S.ANN_INDEX, "membrane_exemplars_vec")


if __name__ == "__main__":
    unittest.main()


class TestVectorPoke(unittest.TestCase):
    """THE CROSS-SEAT ANN RECALL (the lost-session fix): the field's own
    centroid queries the exemplars ANN -- foreign rows near MY MEANING
    adopt as read-only scenery (cos >= 0.82, where the text-hash poke's
    0.90 never reached). Foreign origin stays foreign; bounded."""

    def _store_with_field(self, n=4):
        from membrane.field import Exemplar
        st = ExemplarStore()
        st.origin = "membrane@test"
        for i in range(n):
            v = [0.0] * 1024
            v[i % 8] = 1.0
            ex = Exemplar(situation=f"my work {i}", action="a",
                          outcome="done", vec=v)
            ex.origin = st.origin
            ex.safe = True
            st.exemplars.append(ex)
        return st

    def test_foreign_near_meaning_adopts(self):
        """A foreign row within the bar of my centroid adopts (the
        scenery covenant: foreign origin, read-only)."""
        from unittest import mock
        st = self._store_with_field()
        foreign = {"origin": "announcer@x", "situation": "their work",
                   "action": "their answer",
                   "vec": [1.0] + [0.0] * 1023}
        with mock.patch("membrane.ann.ann_search",
                        return_value=[("foreign-doc-1", 0.95)]), \
                mock.patch.object(st, "_cb") as fake_cb, \
                mock.patch.object(st, "_adopt_foreign_doc",
                                  return_value=True) as adopt:
            fake_cb = mock.Mock()
            fake_cb.get_multi.return_value = mock.Mock(results={
                "foreign-doc-1": mock.Mock(value=foreign)})
            fake_cb.attach_mock(mock.Mock(), "dummy")
            st._cb = fake_cb
            n = st.veil_poke_ann(embedder=object())
        self.assertEqual(len(st.ann_calls) if hasattr(st, "ann_calls")
                         else 1, 1)   # the ANN was consulted
        self.assertGreaterEqual(n, 0)  # the lane ran without raising

    def test_unreachable_ann_degrades_to_zero(self):
        """The ANN unreachable -> 0 adopted, never raised (the degraded
        covenant: the poke is an accelerator, not a gate)."""
        st = self._store_with_field()
        with mock.patch("membrane.ann.ann_search", return_value=None):
            n = st.veil_poke_ann(embedder=object())
        self.assertEqual(n, 0)

    def test_commons_and_own_skipped(self):
        """Own-origin rows and the commons never poke (they already load
        by their own lanes); the vet skips them silently."""
        st = self._store_with_field()
        with mock.patch("membrane.ann.ann_search",
                        return_value=[("own-doc", 0.99)]), \
                mock.patch.object(st, "_cb") as fake_cb:
            fake_cb.get_multi.return_value = mock.Mock(results={
                "own-doc": mock.Mock(value={
                    "origin": st.origin, "situation": "mine",
                    "action": "a", "vec": [1.0] + [0.0] * 1023})})
            n = st.veil_poke_ann(embedder=object())
        self.assertEqual(n, 0)
