"""Release verification (#1143, docs/release-verification.md, item 4): the Home Assistant component end to end against a REAL seat started by the RELEASE LAUNCHER (`scripts/seat`: the
shipped barest packs, the strict door, a fake `claude`, loopback, a free port, its own data folder). Home Assistant is the fake of fakeha.py (no real one is contacted). The same calls
are made by tests/ha/test_house.py and test_entities.py on a seat of a hand-picked pack list; here the seat is the one a user runs."""
import asyncio
import json

import aiohttp
import pytest

from . import fakeha
from .test_entities import _by_name, _loaded, _platform
from .test_house import LIGHT, _allow, _ask, _house, _response, _state


def test_connect_with_a_minted_owner_token_and_the_health_check_passes_and_a_foreign_token_is_refused(ha, release_seat):
    pkg, hass = ha
    owner = release_seat.mint(sub="owner")                                  # `cliffvm token mint --sub owner`, the way docs/home-assistant.md says

    async def go(token):
        s, entry = await _loaded(pkg, hass, release_seat, token=token)
        try:
            return entry.runtime_data.health.data, entry
        finally:
            await s.close()
    data, entry = asyncio.run(go(owner))
    assert data and entry.runtime_data.health.last_update_success is not False
    # a token of another secret: the setup is a reauth, not a working entry
    other = release_seat.mint_at(600)
    bad = other[:-4] + ("AAAA" if not other.endswith("AAAA") else "BBBB")
    with pytest.raises(Exception) as e:
        asyncio.run(go(bad))
    assert "auth" in type(e.value).__name__.lower() or "auth" in str(e.value).lower() or "401" in str(e.value)


def test_a_call_the_allow_list_permits_is_pulled_run_and_answered_and_one_it_denies_never_reaches_home_assistant(ha, release_seat):
    pkg, hass = ha
    _allow(release_seat)
    _ask(release_seat, "ok1", LIGHT)
    _ask(release_seat, "no1", {"domain": "lock", "service": "unlock", "target": {"entity_id": "lock.front"}})
    assert _state(release_seat, "ok1") == "pending"
    assert _state(release_seat, "no1") == "error", "denied by default: it is an error at once, the component never sees it"
    assert "not allowed" in _response(release_seat, "no1")

    async def go():
        s, entry, house = await _house(pkg, hass, release_seat, token=release_seat.mint(sub="owner"))
        try:
            return await house.pump_once()
        finally:
            await s.close()
    assert asyncio.run(go()) == 1, "only the allowed call is offered"
    assert [c["service"] for c in hass.services.calls] == ["turn_on"], "the denied unlock never reached Home Assistant"
    assert _state(release_seat, "ok1") == "done"
    assert json.loads(_response(release_seat, "ok1")) == {"called": "light.turn_on"}


def test_a_token_that_is_not_the_owners_cannot_pull_the_calls(ha, release_seat):
    pkg, hass = ha
    _allow(release_seat)
    _ask(release_seat, "ok1", LIGHT)

    async def go():
        s, entry, house = await _house(pkg, hass, release_seat, token=release_seat.mint(sub="ha"))
        try:
            return await house.pump_once()
        finally:
            await s.close()
    try:
        pulled = asyncio.run(go())
    except Exception:                                                        # a refusal may surface as an error: either way nothing ran
        pulled = 0
    assert not pulled and hass.services.calls == []
    assert _state(release_seat, "ok1") == "pending", "and the call is still waiting for the owner's token"


def test_the_model_select_and_the_metrics_work_on_the_release_launchers_seat(ha, release_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, release_seat, token=release_seat.mint(sub="owner"))
        try:
            data = entry.runtime_data
            sid = await data.client.create_session("rv")
            await data.client.message(sid, "hello")
            await data.health.async_refresh()
            sensors = _by_name(await _platform("sensor", hass, entry))
            flags = _by_name(await _platform("binary_sensor", hass, entry))
            select = (await _platform("select", hass, entry))[0]
            return {n: e.native_value for n, e in sensors.items()}, {n: e.native_value if hasattr(e, "native_value") else e.is_on for n, e in flags.items()}, select.options, select.current_option, select.available
        finally:
            await s.close()
    sensors, flags, options, current, available = asyncio.run(go())
    assert sensors["Model"] == "claude" and sensors["Turns today"] == 1, sensors
    assert flags["Reachable"] is True and flags["Busy"] is False, flags
    assert "claude" in options, "the claude rung is listed (a machine that runs an Ollama lists its models too: the launcher's backend is the default address)"
    assert current == "claude" and available is True
