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


# -- the detached seat's HA access (2026-10-12): a token HA POSTed over the serve,
#    written to hass.env and read per turn ---------------------------------------

def test_a_pushed_token_lands_in_the_env_file(monkeypatch, tmp_path):
    p = tmp_path / ".config" / "membrane" / "hass.env"
    monkeypatch.setenv("MEMBRANE_HASS_ENV", str(p))
    assert HB.hass_available() is False                      # nothing yet
    assert HB.save_token("not a url", "t") == {"ok": False, "error": "give url and token"}
    out = HB.save_token("http://192.168.1.159:8123/", "tok-1 ")
    assert out["ok"] is True and out["path"] == str(p) and p.stat().st_mode & 0o777 == 0o600
    got = HB.load_token()
    assert got == {"HASS_URL": "http://192.168.1.159:8123", "HASS_TOKEN": "tok-1"}
    assert HB.hass_available() is True                        # card line on the next build
    assert HB.load_token() is got                             # the mtime cache: one read
    HB.save_token("http://192.168.1.159:8123", "tok-2")      # HA re-pushed after a restart
    assert HB.load_token()["HASS_TOKEN"] == "tok-2" and HB.hass_available()
    line = HB.card_line()
    assert "$HASS_URL" in line and "hass.env" in line          # the seat is told to source the file
    assert HB._env_path() == p


def test_the_env_child_beats_the_file_and_is_not_read_back(monkeypatch, tmp_path,):
    monkeypatch.setenv("MEMBRANE_HASS_ENV", str(tmp_path / "none.env"))
    monkeypatch.setenv("HASS_URL", "http://ha")
    monkeypatch.setenv("HASS_TOKEN", "t")
    assert HB.hass_available() is True
    line = HB.card_line()
    assert "hass.env" not in line                              # a child: the pair is already in the env
