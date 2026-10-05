"""What every device type provides."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from modbus_connection import ModbusUnit
from modbus_connection.model import Component


@dataclass(frozen=True)
class Variant:
    """A firmware or protocol edition that needs a different register map."""

    key: str
    name: str


@dataclass(frozen=True)
class DeviceType:
    """One kind of device: its identity, defaults, and how to model it on a unit."""

    key: str
    name: str
    manufacturer: str
    models: tuple[str, ...]
    """Models this type has been tested with."""

    create: Callable[[ModbusUnit, str | None], Component]
    """Build the device model on a unit; the second argument is the variant key."""

    variants: tuple[Variant, ...] = ()
    default_unit_id: int = 1
    message_spacing: float = 0.0
    """Seconds to keep the bus quiet around this device's requests."""

    def model(self, unit: ModbusUnit, variant: str | None = None) -> Component:
        """Apply this type's bus requirements to ``unit`` and return its model.

        Raises ``ValueError`` for a variant this type does not define.
        """
        if variant is not None and variant not in {v.key for v in self.variants}:
            raise ValueError(f"unknown variant {variant!r} for device type {self.key!r}")
        if self.message_spacing:
            unit.set_message_spacing(self.message_spacing)
        return self.create(unit, variant)
