"""What every device type provides."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from modbus_connection import ModbusUnit
from modbus_connection.model import Component

type Category = Literal["measurement", "status", "setting", "diagnostic"]
"""What kind of value it is: a measured quantity, a state, a device setting, or
information about the device itself."""


@dataclass(frozen=True)
class Value:
    """One user-facing value of a device: what Home Assistant shows as an entity."""

    key: str
    name: str
    get: Callable[[Any], Any]
    """Read the value from an updated device model; None when unknown."""

    unit: str | None = None
    binary: bool = False
    """An on/off value."""

    available: Callable[[Any], bool] | None = None
    """When the value applies, e.g. a setting only used in one working mode."""

    category: Category = "status"
    device_class: str | None = None
    """Home Assistant device class, e.g. "temperature", "ph", "problem", "running"."""

    write: Callable[[Any, Any], Awaitable[None]] | None = None
    """Write a new value to the device; None for a read-only value. Raises
    ``ValueError`` for a value the device does not accept."""

    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    """The range and resolution a writable number accepts."""

    number_mode: Literal["box", "slider"] | None = None
    """How Home Assistant shows a writable number: an input box or a slider; None lets it pick."""

    enabled_default: bool = True
    """False for a value most installations do not need: Home Assistant adds it disabled."""

    @property
    def writable(self) -> bool:
        return self.write is not None


@dataclass(frozen=True)
class Thermostat:
    """A device that works as a thermostat; Home Assistant shows it as a climate entity."""

    key: str
    name: str
    current_temperature: Callable[[Any], float | None]
    target_temperature: Callable[[Any], float | None]
    mode: Callable[[Any], str | None]
    """One of ``modes``."""

    action: Callable[[Any], str | None]
    """What it is doing: "heating", "idle" or "off"."""

    set_target_temperature: Callable[[Any, float], Awaitable[None]]
    set_mode: Callable[[Any, str], Awaitable[None]]
    minimum: float
    maximum: float
    step: float
    modes: tuple[str, ...] = ("heat", "off")


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

    values: tuple[Value, ...] = ()
    """User-facing values, in display order."""

    thermostat: Thermostat | None = None

    identify: Callable[[Any], bool] | None = None
    """Whether an updated model looks like this device type; None accepts any."""

    software_version: Callable[[Any], str | None] | None = None
    """The device's software version from an updated model, if it reports one."""

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

    def matches(self, device: Component) -> bool:
        """Whether an updated model is really this device type."""
        return True if self.identify is None else bool(self.identify(device))
