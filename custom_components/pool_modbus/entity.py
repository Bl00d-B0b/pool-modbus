"""Base entity: one user-facing value of a device."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum

from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from modbus_connection import ModbusError

from .coordinator import PoolModbusCoordinator, PoolModbusData
from .library import Value

UNITS: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "mV": UnitOfElectricPotential.MILLIVOLT,
    "min": UnitOfTime.MINUTES,
    "s": UnitOfTime.SECONDS,
    "ppm": UnitOfRatio.PARTS_PER_MILLION,
}


async def async_write(
    data: PoolModbusData,
    coordinator: PoolModbusCoordinator,
    what: str,
    write: Callable[[], Awaitable[None]],
) -> None:
    """Write to the device, then read every group so all entities show the result.

    Writes to one device run one at a time, so two commands cannot interleave
    their read-modify-writes of a shared register.
    """
    try:
        async with data.write_lock:
            await write()
    except ValueError as err:
        raise ServiceValidationError(f"{what}: {err}") from err
    except (ModbusError, OSError, TimeoutError) as err:
        raise HomeAssistantError(
            f"{coordinator.config_entry.title} did not accept {what}: {err}"
        ) from err
    await data.async_refresh_all()


class PoolModbusEntity(CoordinatorEntity[PoolModbusCoordinator]):
    """An entity showing one of a device type's values, updated with its scan group."""

    _attr_has_entity_name = True

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data.coordinators[value.group])
        self.data = data
        # Not ``self.value``: NumberEntity already has a ``value`` property.
        self.device_value = value
        entry = self.coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{value.key}"
        self._attr_name = value.name
        self._attr_device_info = data.device_info
        if value.category == "setting":
            self._attr_entity_category = (
                EntityCategory.CONFIG if value.writable else EntityCategory.DIAGNOSTIC
            )
        elif value.category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def icon(self) -> str | None:
        """The value's icon: by its text for a value with a few states, or its own
        icon while off for an on/off value."""
        value = self.device_value
        raw = value.get(self.coordinator.device)
        if value.icons is not None and raw is not None:
            text = getattr(raw, "label", raw.name) if isinstance(raw, Enum) else str(raw)
            if text in value.icons:
                return value.icons[text]
        if value.icon_off is not None and not raw:
            return value.icon_off
        return value.icon

    @property
    def available(self) -> bool:
        """Unavailable when the device did not answer, or the value does not apply."""
        if not super().available:
            return False
        return self.device_value.available is None or bool(
            self.device_value.available(self.coordinator.device)
        )

    async def async_write_value(self, new: object) -> None:
        write = self.device_value.write
        assert write is not None
        device = self.coordinator.device
        await async_write(
            self.data, self.coordinator, self.device_value.name, lambda: write(device, new)
        )
