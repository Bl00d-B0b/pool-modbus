"""What every device type provides."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from modbus_connection import ModbusError, ModbusUnit
from modbus_connection.model import Component

type Category = Literal["measurement", "status", "setting", "diagnostic"]
"""What kind of value it is: a measured quantity, a state, a device setting, or
information about the device itself."""

type ScanGroup = Literal["fast", "medium", "slow"]
"""How often a value is read. Each group has its own interval, set per device."""

SCAN_GROUPS: tuple[ScanGroup, ...] = ("fast", "medium", "slow")

_DEFAULT_GROUP: dict[Category, ScanGroup] = {
    "status": "fast",
    "measurement": "medium",
    "setting": "slow",
    "diagnostic": "slow",
}


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

    scan_group: ScanGroup | None = None
    """How often it is read; None picks by category: status fast, measurements
    medium, settings and diagnostics slow."""

    feature: str | None = None
    """The optional part of the device type it belongs to; None for a value every
    device has."""

    fields: tuple[str, ...] = ()
    """Model fields to read for this value beyond those ``get`` and ``available``
    use, e.g. a field only written."""

    icon: str | None = None
    """Material Design icon, e.g. "mdi:timer-sand"; for an on/off value, the one while on."""

    icon_off: str | None = None
    """The icon of an on/off value while off; None keeps ``icon``."""

    icons: Mapping[str, str] | None = None
    """Icons by the value's text, for a value with a few states (e.g. a mode)."""

    options: tuple[str, ...] | None = None
    """The choices of a writable value picked from a list (Home Assistant shows a
    select), or the states of a read-only value with device class "enum"."""

    light: bool = False
    """A writable on/off value that switches a light; Home Assistant shows a light."""

    time_of_day: bool = False
    """A writable time of day (``datetime.time``); Home Assistant shows a time entity."""

    @property
    def writable(self) -> bool:
        return self.write is not None

    @property
    def group(self) -> ScanGroup:
        return self.scan_group or _DEFAULT_GROUP[self.category]


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
    scan_group: ScanGroup = "fast"
    attributes: Callable[[Any], dict[str, Any]] | None = None
    """More of the thermostat's state, shown as attributes of the climate entity."""

    icon: str | None = None
    icon_off: str | None = None
    """Icons while the thermostat is on (any mode but off) and while it is off."""


@dataclass(frozen=True)
class Action:
    """Something a device does when told, with no state of its own; Home Assistant
    shows a button."""

    key: str
    name: str
    press: Callable[[Any, datetime], Awaitable[None]]
    """Do it; the second argument is the local time now, for a device that is
    told the time. Raises ``ValueError`` when it cannot be done now."""

    available: Callable[[Any], bool] | None = None
    category: Category = "status"
    scan_group: ScanGroup = "fast"
    """The group whose model it uses; that model reads ``fields``."""

    fields: tuple[str, ...] = ()
    """Model fields it writes or checks."""

    feature: str | None = None
    icon: str | None = None


@dataclass(frozen=True)
class Cover:
    """A cover that opens and closes on command; Home Assistant shows a cover."""

    key: str
    name: str
    is_closed: Callable[[Any], bool | None]
    open: Callable[[Any], Awaitable[None]]
    close: Callable[[Any], Awaitable[None]]
    device_class: str | None = None
    scan_group: ScanGroup = "fast"
    fields: tuple[str, ...] = ()
    """Model fields it writes, beyond those ``is_closed`` reads."""

    icon: str | None = None
    icon_closed: str | None = None


@dataclass(frozen=True)
class Feature:
    """An optional part of a device type, switched on or off per device.

    Its values only exist while it is on, and their registers are only read then.
    """

    key: str
    name: str
    default: bool = True


@dataclass(frozen=True)
class Variant:
    """A firmware or protocol edition that needs a different register map."""

    key: str
    name: str


class ImplausibleReadError(ModbusError):
    """The device answered a read with values it cannot hold, such as a block of zeros."""


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
    cover: Cover | None = None
    actions: tuple[Action, ...] = ()

    features: tuple[Feature, ...] = ()
    """Optional parts, each switched on or off per device."""

    identify: Callable[[Any], bool] | None = None
    """Whether an updated model looks like this device type; None accepts any."""

    software_version: Callable[[Any], str | None] | None = None
    """The device's software version from an updated model, if it reports one."""

    variants: tuple[Variant, ...] = ()
    default_unit_id: int = 1
    message_spacing: float = 0.0
    """Seconds to keep the bus quiet around this device's requests."""

    default_name: str = ""
    """The name offered for a device of this type when it is added (the type's
    ``name`` if empty), e.g. "Thermostat"."""

    entity_id_base: str = ""
    """The start of every entity id of this type, before a device's prefix
    (``pool_thermostat`` gives ``number.pool_thermostat_temperature_offset``);
    the key if empty."""

    check_fields: tuple[str, ...] = ()
    """Fields every scan group reads, for the model's check of each read
    (``_verify_read``, which raises ``ImplausibleReadError``)."""

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

    def default_features(self) -> frozenset[str]:
        return frozenset(f.key for f in self.features if f.default)

    def enabled_values(self, features: Iterable[str]) -> tuple[Value, ...]:
        """The values that exist with ``features`` switched on."""
        on = set(features)
        return tuple(v for v in self.values if v.feature is None or v.feature in on)

    def enabled_actions(self, features: Iterable[str]) -> tuple[Action, ...]:
        on = set(features)
        return tuple(a for a in self.actions if a.feature is None or a.feature in on)

    def scan_groups(self) -> tuple[ScanGroup, ...]:
        """The scan groups this type's values use, with every optional part on."""
        used = {value.group for value in self.values}
        used |= {action.scan_group for action in self.actions}
        for spec in (self.thermostat, self.cover):
            if spec is not None:
                used.add(spec.scan_group)
        return tuple(group for group in SCAN_GROUPS if group in used)
