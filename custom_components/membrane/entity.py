"""What the entities and services share: the device, and who is speaking."""
from __future__ import annotations

from .const import DOMAIN


def device_info(entry) -> dict:
    return {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Membrane seat", "manufacturer": "membrane", "model": "field seat"}


async def speaker_name(hass, user_id) -> str:
    """The HA user behind a turn or a service call (none for e.g. a voice satellite or an automation with no user: then no speaker part)."""
    user = await hass.auth.async_get_user(user_id) if user_id else None
    return (user.name or "") if user else ""
