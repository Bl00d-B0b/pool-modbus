"""Binary sensors: every read-only on/off value of a device."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator
from .entity import PoolModbusEntity
from .library import Value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PoolModbusBinarySensor(coordinator, value)
        for value in coordinator.device_type.values
        if value.binary and not value.writable
    )


class PoolModbusBinarySensor(PoolModbusEntity, BinarySensorEntity):
    """An on/off state or alarm."""

    def __init__(self, coordinator: PoolModbusCoordinator, value: Value) -> None:
        super().__init__(coordinator, value)
        if value.device_class:
            self._attr_device_class = BinarySensorDeviceClass(value.device_class)

    @property
    def is_on(self) -> bool | None:
        raw = self.device_value.get(self.coordinator.device)
        return None if raw is None else bool(raw)
