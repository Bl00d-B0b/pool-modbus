"""Numbers: every numeric value a device lets you change."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

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
        PoolModbusNumber(data, value)
        for value in data.values
        if value.writable and not value.binary
    )


class PoolModbusNumber(PoolModbusEntity, NumberEntity):
    """A numeric setting within the range the device accepts."""

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data, value)
        if value.device_class:
            self._attr_device_class = NumberDeviceClass(value.device_class)
        if value.unit is not None:
            self._attr_native_unit_of_measurement = UNITS.get(value.unit, value.unit)
        assert value.minimum is not None and value.maximum is not None and value.step is not None
        self._attr_native_min_value = value.minimum
        self._attr_native_max_value = value.maximum
        self._attr_native_step = value.step
        if value.number_mode is not None:
            self._attr_mode = NumberMode(value.number_mode)

    @property
    def native_value(self) -> float | None:
        return self.device_value.get(self.coordinator.device)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_write_value(value)
