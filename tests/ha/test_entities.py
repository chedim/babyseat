"""Slice 2 (#1143), B: the few entities the component makes for the seat, from what the seat really knows (`GET /membrane/ha/metrics`, `GET /membrane/options`, the health poll), the select
of the seat's model (a write through the owner-only `/membrane/options`), and the migration that removes what the old component left in the entity registry. A REAL cliffvm seat (fake claude);
Home Assistant is the fake of fakeha.py."""
import asyncio
import datetime
import importlib

import aiohttp
import pytest

from . import fakeha


def _added(module, entry):
    got = []
    return got, module.async_setup_entry(None, entry, got.extend)


async def _loaded(pkg, hass, seat, token=None):
    s = aiohttp.ClientSession()
    hass.session = s
    entry = fakeha.FakeConfigEntry(data={"serve_url": seat.url, "serve_user": "opencode", "serve_pass": token or seat.owner_token()})
    hass.config_entries.entries.append(entry)
    await pkg.async_setup_entry(hass, entry)
    for t in entry.tasks:
        t.cancel()
    return s, entry


async def _platform(name, hass, entry):
    mod = importlib.import_module(f"cc_membrane.{name}")
    got = []
    await mod.async_setup_entry(hass, entry, got.extend)
    return got


def _by_name(ents):
    return {e._attr_name: e for e in ents}


def test_the_seats_rows_are_sensors_with_values_the_seat_really_knows(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            data = entry.runtime_data
            sid = await data.client.create_session("e")
            await data.client.message(sid, "hello")
            await data.health.async_refresh()
            sensors = _by_name(await _platform("sensor", hass, entry))
            flags = _by_name(await _platform("binary_sensor", hass, entry))
            ok = {n: (e.native_value, e.available) for n, e in sensors.items()}
            with pytest.raises(importlib.import_module("cc_membrane.seat_client").SeatTurnError):
                await data.client.message(sid, "this will fail")
            await data.health.async_refresh()
            return ok, {n: e.native_value for n, e in sensors.items()}, sensors, flags
        finally:
            await s.close()
    first, second, sensors, flags = asyncio.run(go())
    assert sorted(sensors) == ["Last answer", "Last error", "Model", "Turns today"]
    assert first["Model"] == ("claude", True) and first["Turns today"] == (1, True) and first["Last error"] == (None, True)
    answered = first["Last answer"][0]
    assert isinstance(answered, datetime.datetime) and answered.tzinfo is not None and abs(datetime.datetime.now(datetime.timezone.utc) - answered).total_seconds() < 120
    assert second["Turns today"] == 2 and "not logged in" in second["Last error"]
    assert sensors["Last error"].extra_state_attributes["at"] > 1.0e9
    assert sensors["Turns today"]._attr_native_unit_of_measurement == "turns"
    assert sorted(flags) == ["Busy", "Reachable"] and flags["Busy"].is_on is False and flags["Reachable"].is_on is True
    assert flags["Busy"]._attr_device_class == "running" and flags["Reachable"]._attr_device_class == "connectivity"
    assert len({e._attr_unique_id for e in list(sensors.values()) + list(flags.values())}) == 6, "one unique id each"


def test_a_state_never_exceeds_home_assistants_255_characters(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            ent = (_by_name(await _platform("sensor", hass, entry)))["Last error"]
            entry.runtime_data.health.data["rows"] = [{"key": "last_error", "platform": "sensor", "name": "Last error", "value": "x" * 900}]
            return ent.native_value
        finally:
            await s.close()
    assert len(asyncio.run(go())) == 255


def test_reachable_goes_off_when_the_seat_stops_answering_and_the_others_become_unavailable(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            flags = _by_name(await _platform("binary_sensor", hass, entry))
            sensors = _by_name(await _platform("sensor", hass, entry))
            real_seat.child.kill()
            real_seat.child.wait()
            await entry.runtime_data.health.async_refresh()
            return flags["Reachable"].is_on, flags["Reachable"].available, sensors["Model"].available
        finally:
            await s.close()
    assert asyncio.run(go()) == (False, True, False)


def test_a_token_that_is_not_the_owners_gets_the_reachable_sensor_and_the_model_list_but_no_rows_and_no_write(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat, token=real_seat.mint())
        try:
            sensors = await _platform("sensor", hass, entry)
            flags = _by_name(await _platform("binary_sensor", hass, entry))
            select = (await _platform("select", hass, entry))[0]
            err = None
            try:
                await select.async_select_option("claude-agent:sonnet")
            except fakeha.HomeAssistantError as e:
                err = str(e)
            return sensors, sorted(flags), select.options, err
        finally:
            await s.close()
    sensors, flags, options, err = asyncio.run(go())
    assert sensors == [] and flags == ["Reachable"]
    assert options == ["claude", "claude-agent:sonnet"]
    assert err is not None and "owner" in err


def test_the_select_lists_the_seats_models_shows_the_one_it_runs_and_a_choice_is_written_to_the_seat(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            select = (await _platform("select", hass, entry))[0]
            before = (select.options, select.current_option, select.available)
            await select.async_select_option("claude-agent:sonnet")
            seat_says = await entry.runtime_data.client.options()
            after = select.current_option
            sensors = _by_name(await _platform("sensor", hass, entry))
            return before, seat_says["background"]["loops"], after, sensors["Model"].native_value
        finally:
            await s.close()
    before, seat_says, after, model_sensor = asyncio.run(go())
    assert before == (["claude", "claude-agent:sonnet"], "claude", True)
    assert seat_says == "claude-agent:sonnet" and after == "claude-agent:sonnet" and model_sensor == "claude-agent:sonnet"


def test_a_select_on_a_seat_with_no_minds_is_unavailable(ha, real_seat):
    pkg, hass = ha

    async def go():
        s, entry = await _loaded(pkg, hass, real_seat)
        try:
            select = (await _platform("select", hass, entry))[0]
            entry.runtime_data.health.data["options"] = {"background": None}
            return select.available, select.options, select.current_option
        finally:
            await s.close()
    assert asyncio.run(go()) == (False, [], None)


def test_the_migration_removes_the_old_sensors_binary_sensor_and_select_and_keeps_the_conversation_and_other_entries(ha):
    pkg, hass = ha
    old = ["field_live", "events_applied", "eff_rank", "loops_busy", "ambient"]
    for k in old:
        hass.registry.add(f"sensor.membrane_{k}", f"e1_{k}", "e1")
    hass.registry.add("binary_sensor.membrane_paused", "e1_paused", "e1")
    hass.registry.add("select.membrane_seat", "e1_seat", "e1")
    hass.registry.add("conversation.membrane", "e1", "e1")
    hass.registry.add("sensor.someone_elses", "x_1", "other-entry")
    e = fakeha.FakeConfigEntry(minor_version=3, data={"serve_url": "http://x", "serve_user": "opencode", "serve_pass": "t"})
    assert asyncio.run(pkg.async_migrate_entry(hass, e)) is True
    assert sorted(hass.registry.entries) == ["conversation.membrane", "sensor.someone_elses"]
    assert e.minor_version == 4
    # and once: a second run finds nothing to do and leaves the registry alone
    hass.registry.add("sensor.membrane_model", "e1_model", "e1")
    asyncio.run(pkg.async_migrate_entry(hass, e))
    assert "sensor.membrane_model" in hass.registry.entries


def test_an_entry_from_before_the_connect_only_move_is_migrated_through_both_steps(ha):
    pkg, hass = ha
    hass.registry.add("sensor.membrane_field_live", "e1_field_live", "e1")
    e = fakeha.FakeConfigEntry(minor_version=2, data={"mode": "connect", "serve_url": "http://a.lan:4096", "serve_user": "u", "serve_pass": "p"},
                               options={"seats": [], "ha_access": True})
    asyncio.run(pkg.async_migrate_entry(hass, e))
    assert e.minor_version == 4 and e.data["serve_url"] == "http://a.lan:4096" and hass.registry.entries == {}
