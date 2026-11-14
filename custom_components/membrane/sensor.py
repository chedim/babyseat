"""The seat's rows as sensors (slice 2): the seat's own `GET /membrane/ha/metrics` says which rows exist (membrane/packs/ha.c1f); a row of platform `sensor` is one sensor."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import STATE_MAX
from .entity import device_info


def row_of(coordinator, key: str) -> dict | None:
    return next((r for r in (coordinator.data or {}).get("rows") or [] if r.get("key") == key), None)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coord = entry.runtime_data.health
    async_add_entities([SeatSensor(coord, entry, r) for r in (coord.data or {}).get("rows") or [] if r.get("platform") == "sensor"])


class SeatSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord, entry, row: dict) -> None:
        super().__init__(coord)
        self._key = row["key"]
        self._attr_name = row.get("name") or row["key"]
        self._attr_unique_id = f"{entry.entry_id}_{row['key']}"
        self._attr_icon = row.get("icon")
        self._attr_native_unit_of_measurement = row.get("unit")
        if row.get("device_class") == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) and row_of(self.coordinator, self._key) is not None

    @property
    def native_value(self):
        v = (row_of(self.coordinator, self._key) or {}).get("value")
        if v is None:
            return None
        if getattr(self, "_attr_device_class", None) == SensorDeviceClass.TIMESTAMP:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        return v[:STATE_MAX] if isinstance(v, str) else v

    @property
    def extra_state_attributes(self):
        return (row_of(self.coordinator, self._key) or {}).get("attributes") or None
