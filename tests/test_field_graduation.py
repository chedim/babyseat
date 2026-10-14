"""Regression: graduation must persist state to the store (issue #10 family).

Pre-patch, _materialize computed EntityState in RAM and ended with one _dirty.add(eid) and no flush at the birth site: every mutating maintenance op ends with its own flush (purge_ghosts, apply_pending, trim_cache, relabel_roles, forget, reembed, changes_since) but graduation was never one of them. Direct _materialize calls persisted only if some later maintenance op happened to run before a restart - the night-window dirty-then-discard shape.

Fix under test: self.flush() at the end of _materialize (membrane/efield.py).

The drive is the direct-caller shape: _materialize on a nursery state, with no apply_pending (it flushes at its own end, efield 933) and no backfill_audit (calls process + trim_cache) anywhere near. Assertions read the store side: an entity_states doc exists at birth, parses back through EntityState.from_doc, and hydrates for a fresh field over the same backend.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class TestGraduationPersist(unittest.TestCase):
    def test_direct_materialize_persists_state_doc(self):
        st = EF.EntityState(eid="f:pkg/gw.py", kind="file", surface="pkg/gw.py")
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        self.assertEqual(st.tier, "nursery")
        f._states[st.eid] = st  # house recipe: register before graduation (census: recompute L1347)
        f._materialize(st)  # the night-path analog: direct call, no ambient flush
        self.assertEqual(st.tier, "live")
        docs = list(f.b.scan("entity_states"))
        self.assertTrue(docs, "no entity_states doc after materialize: dirty-then-discard, #10")
        for _, d in docs:  # scan yields (key, doc) pairs -- house unpacks the same way (efield L676, L683)
            self.assertIn("eid", d)
            EF.EntityState.from_doc(d)
        f2 = EF.EntityField(f.b, FakeEmbedder(), "seat@host")  # fresh process
        for _, d in docs:
            self.assertIsNotNone(f2.b.get("entity_states", f2._state_key(d["eid"])))


if __name__ == "__main__":
    unittest.main()
