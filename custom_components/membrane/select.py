"""#283: select.membrane_seat -- the house's active seat, one option per seat
of the zoo; choosing one switches the relay, the sensors, Assist and speech
(zoo.switch) without a restart. Automations can move the house with it."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from . import zoo
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([MembraneSeatSelect(hass, entry)])


class MembraneSeatSelect(SelectEntity):
    _attr_has_entity_name = False
    _attr_icon = "mdi:account-switch"

    def __init__(self, hass, entry) -> None:
        self.hass, self._entry = hass, entry
        self._attr_name = "Membrane seat"                # -> select.membrane_seat
        self._attr_unique_id = f"{entry.entry_id}_seat"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    # the options and the choice are read live (the list may change under it)
    def _seats(self) -> list:
        return list((self._entry.options or {}).get(zoo.SEATS) or [])

    @property
    def options(self) -> list[str]:
        return zoo.names(self._seats())

    @property
    def current_option(self) -> str | None:
        seats = self._seats()
        comp = self.hass.data.get(DOMAIN) or {}
        for s, n in zip(seats, zoo.names(seats)):
            if s.get("id") == comp.get("active_seat"):
                return n
        return None

    async def async_select_option(self, option: str) -> None:
        seats = self._seats()
        for s, n in zip(seats, zoo.names(seats)):
            if n == option:
                zoo.switch(self.hass, self._entry, s.get("id"))
                break
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        comp = self.hass.data.get(DOMAIN) or {}
        cbs = comp.setdefault("on_switch", [])
        cbs.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self.async_write_ha_state in cbs and cbs.remove(self.async_write_ha_state))
