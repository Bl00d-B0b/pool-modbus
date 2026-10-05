"""Sensors: every value of a device that is not on/off."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator
from .entity import PoolModbusEntity
from .library import Value

_UNITS: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "mV": UnitOfElectricPotential.MILLIVOLT,
    "min": UnitOfTime.MINUTES,
    "ppm": CONCENTRATION_PARTS_PER_MILLION,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PoolModbusSensor(coordinator, value)
        for value in coordinator.device_type.values
        if not value.binary
    )


class PoolModbusSensor(PoolModbusEntity, SensorEntity):
    """A measurement, state or setting shown as text or a number."""

    def __init__(self, coordinator: PoolModbusCoordinator, value: Value) -> None:
        super().__init__(coordinator, value)
        device_class = SensorDeviceClass(value.device_class) if value.device_class else None
        self._attr_device_class = device_class
        # Home Assistant's pH device class has no unit.
        if device_class is not SensorDeviceClass.PH and value.unit is not None:
            self._attr_native_unit_of_measurement = _UNITS.get(value.unit, value.unit)
        if value.category == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        raw = self.value.get(self.coordinator.device)
        if isinstance(raw, Enum):
            return getattr(raw, "label", raw.name)
        if isinstance(raw, datetime):
            # Device clocks run on local wall time.
            return raw.replace(tzinfo=dt_util.get_default_time_zone())
        return raw
