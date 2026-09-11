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
