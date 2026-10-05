"""Base entity: one user-facing value of a device."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PoolModbusCoordinator
from .library import Value


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
        if value.category in ("setting", "diagnostic"):
            # Read-only for now: device settings are diagnostic until they become writable.
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Unavailable when the device did not answer, or the value does not apply."""
        if not super().available:
            return False
        return self.value.available is None or bool(self.value.available(self.coordinator.device))
