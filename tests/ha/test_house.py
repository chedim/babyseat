"""Slice 2 (#1143), the seat's use of Home Assistant, from the component's side: the model writes a `HaCall` on a REAL cliffvm seat (the tool `entity_write`, the owner's token, as
the turn's CLI carries it), the component pulls it over its own session, runs it against the FAKE Home Assistant (fakeha.py: no real HA is ever contacted), and the receipt is read
back on the seat. Home Assistant's own side of the line (what is exposed to Assist) is the one the component enforces."""
import asyncio
import importlib
import json

import aiohttp
import pytest

from . import fakeha

LIGHT = {"domain": "light", "service": "turn_on", "data": {"brightness_pct": 40}, "target": {"entity_id": "light.kitchen"}}


def _allow(seat, services=("light.*", "states.read"), entities=("light.*", "sensor.*")):
    seat.tool("entity_write", spec="HaAllow:t", prop="services", value=list(services))
    seat.tool("entity_write", spec="HaAllow:t", prop="entities", value=list(entities))


def _ask(seat, cid, request):
    seat.tool("entity_write", spec=f"HaCall:{cid}", prop="request", value=request)


def _state(seat, cid):
    return seat.tool("entity_read", spec=f"HaCall:{cid}", prop="state").strip('"')


def _response(seat, cid):
    return seat.tool("entity_read", spec=f"HaCall:{cid}", prop="response")


async def _house(pkg, hass, seat, token=None):
    """-> (session, entry, the House of the component) against the real seat, the token the owner's unless one is given."""
    s = aiohttp.ClientSession()
    hass.session = s
    entry = fakeha.FakeConfigEntry(data={"serve_url": seat.url, "serve_user": "opencode", "serve_pass": token or seat.owner_token()})
    hass.config_entries.entries.append(entry)
    hass.states.set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    hass.states.set("sensor.temp", "21.5", {"unit_of_measurement": "C", "friendly_name": "Temperature"})
    hass.states.set("lock.front", "locked", {"friendly_name": "Front door"})
    hass.exposed |= {"light.kitchen", "sensor.temp"}                         # the person exposed these two to Assist; the lock they did not
    hass.services.register("light", "turn_on", lambda call: None)
    hass.services.register("light", "boom", lambda call: (_ for _ in ()).throw(fakeha.HomeAssistantError("the bulb is unplugged")))
    hass.services.register("lock", "unlock", lambda call: None)
    hass.services.register("weather", "get_forecasts", lambda call: {"weather.home": {"forecast": [{"temperature": 12}]}}, fakeha.SupportsResponse.ONLY)
    hass.descriptions = {"light": {"turn_on": {"target": {"entity": {}}, "fields": {"brightness_pct": {}, "color_name": {}}}},
                         "lock": {"unlock": {"target": {"entity": {}}, "fields": {}}}, "notify": {"mobile": {"fields": {"message": {}}}}}
    await pkg.async_setup_entry(hass, entry)
    for t in entry.tasks:                                                    # the tests step the pump by hand: no background loop in the way
        t.cancel()
    return s, entry, entry.runtime_data.house


def test_a_call_the_model_wrote_is_pulled_run_in_home_assistant_and_the_receipt_is_on_the_seat(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "k1", LIGHT)
    assert _state(real_seat, "k1") == "pending"

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 1
    assert hass.services.calls == [{"domain": "light", "service": "turn_on", "data": {"brightness_pct": 40}, "target": {"entity_id": "light.kitchen"},
                                    "blocking": True, "return_response": False}]
    assert _state(real_seat, "k1") == "done"
    assert json.loads(_response(real_seat, "k1")) == {"called": "light.turn_on"}


def test_a_read_answers_the_state_and_its_attributes_and_a_service_with_data_answers_it(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "r1", {"read": "sensor.temp"})
    _ask(real_seat, "w1", {"domain": "weather", "service": "get_forecasts", "target": {"entity_id": "sensor.temp"}})

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 1, "the read is pulled; the denied service call was never offered"
    r = json.loads(_response(real_seat, "r1"))
    assert r["state"] == "21.5" and r["attributes"] == {"unit_of_measurement": "C", "friendly_name": "Temperature"} and r["last_changed"].startswith("2026-11-14T12:00:00")
    # weather says it needs `weather.get_forecasts` on an entry: the allow-list denied it before the component saw it
    assert _state(real_seat, "w1") == "error" and "not allowed" in _response(real_seat, "w1")
    assert [c["service"] for c in hass.services.calls] == [], "a read is not a service call, and the denied one never reached Home Assistant"


def test_a_service_that_returns_data_hands_it_to_the_model(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat, services=("weather.get_forecasts",), entities=())
    _ask(real_seat, "w1", {"domain": "weather", "service": "get_forecasts", "data": {"type": "daily"}})

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            await house.pump_once()
        finally:
            await s.close()
    asyncio.run(go())
    assert hass.services.calls[0]["return_response"] is True, "a service that only answers is called for its answer"
    assert json.loads(_response(real_seat, "w1")) == {"weather.home": {"forecast": [{"temperature": 12}]}}


def test_home_assistants_own_line_an_entity_the_person_did_not_expose_is_refused_by_the_component_though_the_seat_allowed_it(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat, services=("lock.*", "states.read"), entities=("lock.*",))
    _ask(real_seat, "u1", {"domain": "lock", "service": "unlock", "target": {"entity_id": "lock.front"}})
    _ask(real_seat, "u2", {"read": "lock.front"})

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 2
    for cid in ("u1", "u2"):
        assert _state(real_seat, cid) == "error"
        assert "not exposed to Assist" in _response(real_seat, cid) and "lock.front" in _response(real_seat, cid)
    assert hass.services.calls == []


def test_a_failing_service_a_missing_service_and_a_missing_entity_are_errors_on_the_seat_with_the_reason(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat, services=("light.*", "switch.*", "states.read"), entities=("light.*", "switch.*", "sensor.*"))
    _ask(real_seat, "e1", {"domain": "light", "service": "boom", "target": {"entity_id": "light.kitchen"}})
    _ask(real_seat, "e2", {"domain": "switch", "service": "toggle", "target": {"entity_id": "switch.fan"}})
    _ask(real_seat, "e3", {"read": "sensor.gone"})
    hass_exposed = lambda hass: hass.exposed.update({"switch.fan", "sensor.gone"})

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        hass_exposed(hass)
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 3
    assert "the bulb is unplugged" in _response(real_seat, "e1")
    assert "switch.toggle" in _response(real_seat, "e2") and "not found" in _response(real_seat, "e2")
    assert "sensor.gone" in _response(real_seat, "e3") and "no such entity" in _response(real_seat, "e3")
    assert all(_state(real_seat, c) == "error" for c in ("e1", "e2", "e3"))


def test_the_catalog_is_exposed_entities_only_and_every_service_pushed_once_until_it_changes(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            first = await house.push_catalog()
            again = await house.push_catalog()
            hass.exposed.add("lock.front")
            changed = await house.push_catalog()
            return first, again, changed
        finally:
            await s.close()
    first, again, changed = asyncio.run(go())
    assert (first, again, changed) == (True, False, True), "pushed, unchanged, pushed again when what is exposed changed"
    ids = json.loads(real_seat.tool("entity_read", spec="HaCatalog:ha", prop="entity_ids"))
    assert ids == ["light.kitchen", "lock.front", "sensor.temp"]
    sv = json.loads(real_seat.tool("entity_read", spec="HaCatalog:ha", prop="service_ids"))
    assert "light.turn_on" in sv and "notify.mobile" in sv and "lock.unlock" in sv
    rows = json.loads(real_seat.tool("entity_read", spec="HaCatalog:ha", prop="services"))
    assert {"id": "light.turn_on", "target": True, "fields": ["brightness_pct", "color_name"]} in rows
    assert {"id": "notify.mobile", "target": False, "fields": ["message"]} in rows


def test_a_read_of_the_catalog_refreshes_it_and_answers_how_much(ha, real_seat):
    pkg, hass = ha
    _ask(real_seat, "c1", {"read": "catalog"})

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat)
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 1
    assert json.loads(_response(real_seat, "c1")) == {"entities": 2, "services": 3}
    assert json.loads(real_seat.tool("entity_read", spec="HaCatalog:ha", prop="entity_ids")) == ["light.kitchen", "sensor.temp"]


def test_a_token_that_is_not_the_owners_pulls_nothing_and_says_so_while_assist_keeps_working(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "n1", LIGHT)

    async def go():
        s, entry, house = await _house(pkg, hass, real_seat, token=real_seat.mint())          # sub `ha`: a person, not the owner
        try:
            n = await house.pump_once()
            return n, house.forbidden, await entry.runtime_data.client.health()
        finally:
            await s.close()
    n, forbidden, health = asyncio.run(go())
    assert n == 0 and forbidden is True and health["healthy"] is True
    assert _state(real_seat, "n1") == "pending", "nothing was claimed"
    assert hass.services.calls == []


def test_a_down_seat_is_a_quiet_empty_pull_and_the_loop_survives_it(ha):
    pkg, hass = ha

    async def go():
        s = aiohttp.ClientSession()
        hass.session = s
        entry = fakeha.FakeConfigEntry(data={"serve_url": "http://127.0.0.1:9", "serve_user": "opencode", "serve_pass": "x"})
        try:
            client = importlib.import_module("cc_membrane.seat_client").SeatClient(s, entry.data["serve_url"], "x")
            house = importlib.import_module("cc_membrane.house").House(hass, client)
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 0


def test_the_background_loop_pulls_by_itself_and_stops_with_its_task(ha, real_seat, monkeypatch):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "b1", LIGHT)

    async def go():
        s = aiohttp.ClientSession()
        hass.session = s
        entry = fakeha.FakeConfigEntry(data={"serve_url": real_seat.url, "serve_user": "opencode", "serve_pass": real_seat.owner_token()})
        hass.states.set("light.kitchen", "off")
        hass.exposed.add("light.kitchen")
        hass.services.register("light", "turn_on", lambda call: None)
        importlib.import_module("cc_membrane.const").CALL_PULL_S = 0.05
        await pkg.async_setup_entry(hass, entry)
        try:
            for _ in range(200):
                if _state(real_seat, "b1") == "done":
                    return True
                await asyncio.sleep(0.05)
            return False
        finally:
            for t in entry.tasks:
                t.cancel()
            await s.close()
    assert asyncio.run(go()), "the loop that setup started ran the call"
