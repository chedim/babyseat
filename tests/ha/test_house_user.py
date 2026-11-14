"""#1143, the seat's use of the house runs AS a Home Assistant user: the hidden system user "Membrane" (non-admin, local-only, group USER -- the old component's own), so Home
Assistant's permission model, which the model cannot write, decides what a call may touch. The component still signs in to the seat with the owner's token (Home Assistant is NOT
a seat person). Against the fake Home Assistant (fakeha.py) and a real seat."""
import asyncio
import json

import aiohttp

from . import fakeha
from .test_house import LIGHT, _allow, _ask, _house, _response, _state


def _run(pkg, hass, seat, steps):
    async def go():
        s, entry, house = await _house(pkg, hass, seat)
        try:
            return await steps(entry, house)
        finally:
            await s.close()
    return asyncio.run(go())


def test_every_call_runs_with_the_membrane_users_context_and_the_user_is_hidden_non_admin_and_local_only(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "k1", LIGHT)
    _ask(real_seat, "k2", {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.kitchen"}})

    async def steps(entry, house):
        return await house.pump_once()
    assert _run(pkg, hass, real_seat, steps) == 2
    assert hass.created_users == [("Membrane", ["system-users"], True)], "a system user, group USER (not admin), local only"
    user = next(iter(hass.users.values()))
    assert user.system_generated and not user.is_admin
    assert [c.user_id for c in hass.services.contexts] == [user.id, user.id], "both calls carry the user's context"


def test_the_user_is_created_once_and_found_again_by_the_id_in_the_entry_and_by_its_name(ha, real_seat):
    pkg, hass = ha

    async def steps(entry, house):
        first = entry.data.get("ha_user_id")
        entry2 = fakeha.FakeConfigEntry(data=dict(entry.data, ha_user_id=None), entry_id="e2")
        await pkg.async_setup_entry(hass, entry2)               # an entry without the id: found by its name
        for t in entry2.tasks:
            t.cancel()
        return first, entry2.data.get("ha_user_id")
    first, second = _run(pkg, hass, real_seat, steps)
    assert len(hass.created_users) == 1, "one user, however many setups"
    assert first and first == second == next(iter(hass.users)), "the entry keeps the id"


def test_a_user_removed_in_home_assistant_is_made_again_on_the_next_call(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat)
    _ask(real_seat, "k1", LIGHT)

    async def steps(entry, house):
        old = entry.data["ha_user_id"]
        hass.users.pop(old)                                      # an admin deleted it in Settings -> People
        n = await house.pump_once()
        return old, n
    old, n = _run(pkg, hass, real_seat, steps)
    assert n == 1 and _state(real_seat, "k1") == "done"
    assert len(hass.created_users) == 2
    assert hass.services.contexts[0].user_id in hass.users and hass.services.contexts[0].user_id != old


def test_a_call_home_assistants_permissions_refuse_is_an_error_receipt_with_its_reason(ha, real_seat):
    pkg, hass = ha
    _allow(real_seat, services=("light.*", "states.read"), entities=("light.*",))
    _ask(real_seat, "p1", LIGHT)

    def refuse(user_id, domain, service, data):
        raise fakeha.Unauthorized(user_id=user_id, entity_id=data.get("entity_id"), perm_category="entities", permission="control")

    async def steps(entry, house):
        hass.services.policy = refuse
        return await house.pump_once()
    assert _run(pkg, hass, real_seat, steps) == 1
    assert _state(real_seat, "p1") == "error", "not silent"
    why = _response(real_seat, "p1")
    assert "refused" in why and "light.turn_on" in why and "light.kitchen" in why and "control" in why and "Membrane" in why
    assert hass.services.contexts == []


def test_the_user_goes_with_the_integration_and_a_removal_without_one_is_quiet(ha, real_seat):
    pkg, hass = ha

    async def steps(entry, house):
        assert len(hass.users) == 1
        await pkg.async_remove_entry(hass, entry)
        gone = len(hass.users)
        await pkg.async_remove_entry(hass, entry)               # again: nothing to remove, no error
        return gone
    assert _run(pkg, hass, real_seat, steps) == 0
