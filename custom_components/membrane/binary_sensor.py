"""Whether the seat answers (from the health poll), and the seat's own binary rows (is a turn running)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import device_info
from .sensor import row_of


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coord = entry.runtime_data.health
    async_add_entities([SeatReachable(coord, entry)] + [SeatFlag(coord, entry, r) for r in (coord.data or {}).get("rows") or []
                                                         if r.get("platform") == "binary_sensor"])


class SeatReachable(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coord, entry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_reachable"
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        return True                      # the point of it is to say no

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success)


class SeatFlag(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord, entry, row: dict) -> None:
        super().__init__(coord)
        self._key = row["key"]
        self._attr_name = row.get("name") or row["key"]
        self._attr_unique_id = f"{entry.entry_id}_{row['key']}"
        self._attr_icon = row.get("icon")
        if row.get("device_class") == "running":
            self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) and row_of(self.coordinator, self._key) is not None

    @property
    def is_on(self):
        v = (row_of(self.coordinator, self._key) or {}).get("value")
        return None if v is None else bool(v)
