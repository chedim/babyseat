"""Home Assistant's states, live in the field (ha_bridge.py): the seat marks its
entities with ha_entity; HA's pushed changes land on them."""
from membrane import ha_bridge as HB


class _Ent:
    def __init__(self, eid, pp):
        self.eid, self.properties = eid, pp


class _Store:
    def __init__(self, ents):
        self.docs = {e.eid: e for e in ents}
        self.upserts = []

    def upsert(self, ent, embedder=None):
        self.upserts.append(ent.eid)


class _Srv:
    def __init__(self, ents):
        self.entity_store = _Store(ents)


def test_marked_entities_are_the_subscriptions():
    srv = _Srv([_Ent("a", {"ha_entity": "light.kitchen"}), _Ent("b", {}), _Ent("c", {"ha_entity": "sensor.t"})])
    assert HB.subscriptions(srv) == ["light.kitchen", "sensor.t"]


def test_a_pushed_change_lands_once():
    k = _Ent("a", {"ha_entity": "light.kitchen"})
    srv = _Srv([k])
    row = {"entity_id": "light.kitchen", "state": "on", "attributes": {"brightness": 200}, "last_changed": "t1"}
    assert HB.apply(srv, [row, {"entity_id": "light.unknown", "state": "off"}]) == {"updated": 1, "unknown": 1}
    assert k.properties["ha_state"] == "on" and k.properties["ha_attributes"] == {"brightness": 200}
    assert HB.apply(srv, [row])["updated"] == 0                 # the same state again: no write, no watch fires
    assert srv.entity_store.upserts == ["a"]


def test_huge_attributes_are_trimmed():
    k = _Ent("a", {"ha_entity": "media_player.tv"})
    HB.apply(_Srv([k]), [{"entity_id": "media_player.tv", "state": "playing",
                          "attributes": {"title": "x", "art": "y" * 9000}}])
    assert k.properties["ha_attributes"] == {"title": "x"}
