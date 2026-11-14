"""The seat's model as a select: the models the seat lists (`GET /membrane/options`, background.models) and the one it runs on (background.loops); choosing one is the owner's write
`POST /membrane/options {loops: <model>}`, the page's own post (docs/home-assistant.md)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import device_info
from .seat_client import SeatAuthError, SeatError


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([SeatModel(entry.runtime_data.health, entry)])


class SeatModel(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Model"
    _attr_icon = "mdi:robot"

    def __init__(self, coord, entry) -> None:
        super().__init__(coord)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_model_select"
        self._attr_device_info = device_info(entry)

    def _bg(self) -> dict | None:
        bg = ((self.coordinator.data or {}).get("options") or {}).get("background")
        return bg if isinstance(bg, dict) else None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) and self._bg() is not None

    @property
    def options(self) -> list[str]:
        return [str(m) for m in (self._bg() or {}).get("models") or []]

    @property
    def current_option(self) -> str | None:
        return (self._bg() or {}).get("loops") or None

    async def async_select_option(self, option: str) -> None:
        try:
            await self._entry.runtime_data.client.set_model(option)
        except SeatAuthError:
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError("the seat refused Home Assistant's token") from None
        except SeatError as e:
            raise HomeAssistantError(str(e)) from e
        await self.coordinator.async_request_refresh()
