"""THE GRAPH'S KEYS ARE ADDRESSABLE (issue #99's "sibling spellings
unreachable"): the graph reader returns FIELD ids ('f:x.py'); the entity
tools address the STORE's key ('membrane::Component:files:x.py', the
class-embedded pool spelling). A file entity minted by entity_add lands
class-embedded, so the graph's 'f:x.py' could never reach it -- the row
the graph printed was not the row entity_remove could find.

The fix: the graph's rows (and their neighbours) carry the true stored
eid alongside the field id, so whatever the graph returns is exactly what
entity_edit/entity_remove address."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def _field():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                             text="membrane/router.py picks the free model.")],
                   session="s")
    f.process()
    return f


def _est():
    est = EntityStore(path=None, shared=False)
    # the class-embedded spelling ONLY -- the one entity_add mints, and the
    # one the field short-kind 'f:x' did not reach (no legacy files: doc).
    est.upsert(QuantumEntity(
        eid=MEMBRANE_NS + "Component:files:membrane/router.py",
        cls="Component", properties={"name": "router.py"}), embedder=None)
    return est


class TestAddressable(unittest.TestCase):
    def test_graph_rows_carry_the_addressable_store_eid(self):
        f = _field()
        est = _est()
        rows = EB.annotate_store_keys(EB.related(f, "membrane/router.py"), est)
        self.assertEqual(rows[0]["key"], "f:membrane/router.py")       # field id
        self.assertEqual(rows[0]["eid"], MEMBRANE_NS + "Component:files:membrane/router.py")
        # the field id alone did NOT address the class-embedded spelling:
        self.assertIsNone(est.resolve("f:membrane/router.py"))
        # the annotated eid DOES:
        self.assertIsNotNone(est.resolve(rows[0]["eid"]))

    def test_neighbours_carry_the_store_eid_too(self):
        est = _est()
        est.upsert(QuantumEntity(
            eid=MEMBRANE_NS + "Component:ha:light.x",
            cls="Component", properties={"name": "light.x"}), embedder=None)
        rows = [{"key": "f:membrane/router.py",
                 "neighbors": [{"key": "ha:light.x", "shared": 1}]}]
        EB.annotate_store_keys(rows, est)
        self.assertEqual(rows[0]["neighbors"][0]["eid"],
                         MEMBRANE_NS + "Component:ha:light.x")
        # a neighbour with no stored entity keeps just its field id:
        rows = [{"key": "f:membrane/router.py",
                 "neighbors": [{"key": "tool:edit", "shared": 1}]}]
        EB.annotate_store_keys(rows, est)
        self.assertNotIn("eid", rows[0]["neighbors"][0])


if __name__ == "__main__":
    unittest.main()
