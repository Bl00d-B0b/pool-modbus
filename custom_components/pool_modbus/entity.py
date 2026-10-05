"""Base entity: one user-facing value of a device."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from modbus_connection import ModbusError

from .coordinator import PoolModbusCoordinator
from .library import Value

UNITS: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "mV": UnitOfElectricPotential.MILLIVOLT,
    "min": UnitOfTime.MINUTES,
    "ppm": CONCENTRATION_PARTS_PER_MILLION,
}


async def async_write(
    coordinator: PoolModbusCoordinator, what: str, write: Callable[[], Awaitable[None]]
) -> None:
    """Write to the device, then read it back so every entity shows the result."""
    try:
        await write()
    except ValueError as err:
        raise ServiceValidationError(f"{what}: {err}") from err
    except (ModbusError, OSError, TimeoutError) as err:
        raise HomeAssistantError(f"{coordinator.name} did not accept {what}: {err}") from err
    await coordinator.async_refresh()


class PoolModbusEntity(CoordinatorEntity[PoolModbusCoordinator]):
    """An entity showing one of a device type's values."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolModbusCoordinator, value: Value) -> None:
        super().__init__(coordinator)
        self.value = value
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{value.key}"
        self._attr_name = value.name
        self._attr_device_info = coordinator.device_info
        if value.category == "setting":
            self._attr_entity_category = (
                EntityCategory.CONFIG if value.writable else EntityCategory.DIAGNOSTIC
            )
        elif value.category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Unavailable when the device did not answer, or the value does not apply."""
        if not super().available:
            return False
        return self.value.available is None or bool(self.value.available(self.coordinator.device))

    async def async_write_value(self, new: object) -> None:
        write = self.value.write
        assert write is not None
        await async_write(
            self.coordinator, self.value.name, lambda: write(self.coordinator.device, new)
        )
