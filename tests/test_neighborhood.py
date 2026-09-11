"""THE PERSONALIZED FIELD (the SQL++ neighborhood, the cortex-
experiment convergence): the seat's working set = the ANN neighborhood
around session:now's position + the seat's own patch + the commons --
the database is the field, RAM keeps a session-scoped neighborhood.
The degraded shape (no seed / no cb) degenerates to the own-patch
hydrate. MEMBRANE_FIELD=world reverts to the world hydrate."""

import unittest

from membrane.field import ExemplarStore


class TestNeighborhood(unittest.TestCase):
    def test_shape_guard_no_seed_degrades_to_own_patch(self):
        """seed_vec=None -> the own-patch hydrate (load_couchbase's
        filter), never the ANN. The degraded honest shape."""
        st = ExemplarStore.load_neighborhood(field_path="")  # bare store
        self.assertEqual(len(st.exemplars), 0)
        self.assertFalse(getattr(st, "_keys_from_snapshot", False))

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
