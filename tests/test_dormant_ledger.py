"""#171: the dormant inventory must not count as live watches.

paint_dormant mirrored the unarmed proximity ops onto membrane::dormant under
the key `watch_ops` -- the key real watches use. The attention ledger counted
the mirror as live watches (14 live / 12, all age 0s, after a hot update),
and the next boot's inventory scanned the mirror as unarmed ops again, so it
grew at every restart.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import attention as AT  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DORMANT = MEMBRANE_NS + "dormant"


class _Srv:
    def __init__(self):
        self.entity_store = EntityStore(path=None, shared=False)


def _seat_with_one_unarmed_op():
    srv = _Srv()
    e = QuantumEntity(eid=MEMBRANE_NS + "operator", cls="Person", properties={
        "name": "operator", "watch_ops": [{"op": "near", "rel": "*", "z": 0.12, "ts": 1.0}]})
    srv.entity_store.docs[e.eid] = e
    return srv


def test_the_inventory_is_not_a_live_watch():
    srv = _seat_with_one_unarmed_op()
    WA.paint_dormant(srv)
    live = AT.inventory(srv.entity_store, None)
    assert [w["id"] for w in live] == [MEMBRANE_NS + "operator#op0"]


def test_the_inventory_does_not_feed_itself_across_boots():
    srv = _seat_with_one_unarmed_op()
    for _ in range(5):                               # five restarts / hot updates
        inv = WA.paint_dormant(srv)
    assert len(inv["watch_ops"]) == 1
    assert len(AT.inventory(srv.entity_store, None)) == 1


def test_a_seat_carrying_the_old_mirror_is_cleaned_at_the_next_boot():
    srv = _seat_with_one_unarmed_op()
    srv.entity_store.docs[DORMANT] = QuantumEntity(eid=DORMANT, cls="Config", properties={
        "name": "dormant",
        "watch_ops": [{"entity": MEMBRANE_NS + "operator", "op": "near", "rel": "*", "z": 0.12}] * 13})
    assert len(AT.inventory(srv.entity_store, None)) == 14   # the live seat's #171
    inv = WA.paint_dormant(srv)
    assert len(inv["watch_ops"]) == 1
    assert len(AT.inventory(srv.entity_store, None)) == 1
    props = srv.entity_store.docs[DORMANT].properties
    assert "watch_ops" not in props and len(props["dormant_ops"]) == 1
