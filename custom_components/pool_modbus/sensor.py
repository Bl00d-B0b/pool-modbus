"""Sensors: every read-only value of a device that is not on/off."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PoolModbusConfigEntry, PoolModbusData
from .entity import UNITS, PoolModbusEntity
from .library import Value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        PoolModbusSensor(data, value)
        for value in data.values
        if not value.binary and not value.writable
    )


class PoolModbusSensor(PoolModbusEntity, SensorEntity):
    """A measurement, state or setting shown as text or a number."""

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data, value)
        device_class = SensorDeviceClass(value.device_class) if value.device_class else None
        self._attr_device_class = device_class
        # Home Assistant's pH device class has no unit.
        if device_class is not SensorDeviceClass.PH and value.unit is not None:
            self._attr_native_unit_of_measurement = UNITS.get(value.unit, value.unit)
        if value.category == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        raw = self.device_value.get(self.coordinator.device)
        if isinstance(raw, Enum):
            return getattr(raw, "label", raw.name)
        if isinstance(raw, datetime):
            # Device clocks run on local wall time.
            return raw.replace(tzinfo=dt_util.get_default_time_zone())
        return raw
