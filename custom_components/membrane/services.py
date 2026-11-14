"""The services of the component (slice 2): `membrane.send_message` (hand the seat a message, do not wait for the answer) and `membrane.ask` (answered: the seat's reply is the
service's response data). Both are turns of the same seat sessions Assist uses (title `hass-<session>`; `session` is a name, default `service`: the same name continues the same
seat session), carry the caller's name as the speaker line when Home Assistant knows one, and say why when the seat could not answer. Described in services.yaml."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import speaker_name
from .seat_client import SeatAuthError, SeatError

SERVICE_SEND = "send_message"
SERVICE_ASK = "ask"
DEFAULT_SESSION = "service"

SCHEMA = vol.Schema({vol.Required("text"): str, vol.Optional("session"): str})


def register(hass, entry) -> None:
    async def run(call: ServiceCall, wait: bool):
        data = entry.runtime_data
        key = call.data.get("session") or DEFAULT_SESSION
        text = str(call.data.get("text") or "").strip()
        if not text:
            raise HomeAssistantError("text is empty")
        try:
            answer = await data.say(key, text, await speaker_name(hass, getattr(call.context, "user_id", None)), wait=wait)
        except SeatAuthError as e:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(f"The seat refused Home Assistant's token ({e}). Open the Membrane integration and paste a new token.") from e
        except SeatError as e:
            raise HomeAssistantError(f"The seat could not answer: {e}") from e
        return {"answer": answer, "session": key} if wait else None

    async def send_message(call: ServiceCall) -> None:
        await run(call, wait=False)

    async def ask(call: ServiceCall) -> dict:
        return await run(call, wait=True)

    hass.services.async_register(DOMAIN, SERVICE_SEND, send_message, schema=SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ASK, ask, schema=SCHEMA, supports_response=SupportsResponse.ONLY)


def unregister(hass) -> None:
    for name in (SERVICE_SEND, SERVICE_ASK):
        hass.services.async_remove(DOMAIN, name)
