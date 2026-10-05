"""T010 pool thermostat by Optika ir technologija (baseinai.lt), standard Modbus addressing.

Register map from the device firmware (software version 1.7); see
docs/devices/t010.md. The model reads addresses 0-8 (40001-40009) in one
function-03 request; the firmware allows at most 10 registers per read. Settings
are written with function 06.

Writes: the setpoint, offset, delay setting, and the heating-blocked and
delaying flags. Each is checked against the firmware's range first, skipped
when the device already holds the value because the firmware stores every
write in EEPROM, and confirmed by reading it back (see ``write_if_changed``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, bit, bits, gauge, integer

from .base import DeviceType, Thermostat, Value
from .writing import write_if_changed

INSTRUMENT_TYPE = 0x15
"""What register 0's high byte holds on a T010."""

SETPOINT_RANGE = (5.0, 40.0)
OFFSET_RANGE = (-3.1, 3.1)
DELAY_RANGE = (0, 59)


def _tenths(low: float, high: float) -> Callable[[Any], float]:
    """Accept a temperature in the firmware's range, in its 0.1 °C resolution."""

    def check(value: Any) -> float:
        tenths = round(float(value), 1)
        if not low <= tenths <= high:
            raise ValueError(f"{value} is outside {low} to {high}")
        return tenths

    return check


def _minutes(value: Any) -> int:
    """Accept a whole number of minutes in the firmware's range."""
    low, high = DELAY_RANGE
    if value != int(value) or not low <= value <= high:
        raise ValueError(f"{value} is not a whole number from {low} to {high}")
    return int(value)


class T010(Component):
    """T010 thermostat. Addresses are the on-wire register numbers."""

    max_span = 10  # firmware limit
    register_ranges = ((0, 8),)  # 40001-40009, one request

    instrument_type = bits(0, 8, 8)
    software_version_raw = bits(0, 0, 8)  # 17 means 1.7
    temperature = gauge(1, 0.1, unit="°C")  # offset already applied
    heating_power = integer(2, signed=False, unit="%")  # relay: 0 or 100; PWM: steps of 20
    delay_minutes = bits(3, 8, 8)  # heating delay still to run
    delay_seconds = bits(3, 0, 8)
    # Writes use function 06, one register each; register 8 is read back and merged.
    setpoint = gauge(5, 0.1, unit="°C", writable=_tenths(*SETPOINT_RANGE))
    offset = gauge(6, 0.1, unit="°C", writable=_tenths(*OFFSET_RANGE))
    set_delay = integer(7, signed=False, unit="min", writable=_minutes)
    heating_blocked = bit(8, 8, writable=True)
    delaying = bit(8, 9, writable=True)
    menu_mode = bit(8, 0)
    sensor_disconnected = bit(8, 1)  # raw temperature below 3.1 °C
    sensor_supply_fault = bit(8, 2)  # sensor supply below 6.0 V for about 2 s
    eeprom_fault = bit(8, 3)

    @property
    def is_t010(self) -> bool | None:
        """Whether the device identifies itself as a T010; None before a read."""
        if self.instrument_type is None:
            return None
        return self.instrument_type == INSTRUMENT_TYPE

    @property
    def software_version(self) -> str | None:
        raw = self.software_version_raw
        return None if raw is None else f"{raw // 10}.{raw % 10}"

    @property
    def heating_enabled(self) -> bool | None:
        return None if self.heating_blocked is None else not self.heating_blocked

    @property
    def heating(self) -> bool | None:
        """Whether the heating output is on (any power above 0 %)."""
        return None if self.heating_power is None else self.heating_power > 0

    @property
    def delay_remaining(self) -> str | None:
        """Heating delay still to run, as mm:ss."""
        if self.delay_minutes is None or self.delay_seconds is None:
            return None
        return f"{self.delay_minutes:02d}:{self.delay_seconds:02d}"

    @property
    def hvac_mode(self) -> str | None:
        """Thermostat mode as Home Assistant's climate entity shows it: heat or off."""
        enabled = self.heating_enabled
        return None if enabled is None else ("heat" if enabled else "off")

    @property
    def hvac_action(self) -> str | None:
        """What the thermostat is doing: off, heating or idle."""
        if self.heating_enabled is None or self.heating is None:
            return None
        if not self.heating_enabled:
            return "off"
        return "heating" if self.heating else "idle"


async def _set_mode(device: T010, mode: str) -> None:
    if mode not in ("heat", "off"):
        raise ValueError(f"{mode!r} is not a T010 mode; use heat or off")
    await write_if_changed(device, "heating_blocked", mode == "off")


THERMOSTAT = Thermostat(
    key="thermostat",
    name="Pool Thermostat",
    current_temperature=lambda d: d.temperature,
    target_temperature=lambda d: d.setpoint,
    mode=lambda d: d.hvac_mode,
    action=lambda d: d.hvac_action,
    set_target_temperature=lambda d, t: write_if_changed(d, "setpoint", t),
    set_mode=_set_mode,
    minimum=SETPOINT_RANGE[0],
    maximum=SETPOINT_RANGE[1],
    step=0.5,
    # Heating whenever the power is above 0 %: relay mode 0 or 100, PWM mode in 20 % steps.
    attributes=lambda d: {"heating_power": d.heating_power},
)

# The water temperature, heating on/off, the setpoint, whether it is heating and the
# heating power are all the thermostat's: its current temperature, mode, target,
# action and the heating_power attribute.
VALUES = (
    Value(
        "delaying",
        "Pool Delaying",
        lambda d: d.delaying,
        binary=True,
        write=lambda d, on: write_if_changed(d, "delaying", bool(on)),
    ),
    Value("delay_remaining", "Delay Time", lambda d: d.delay_remaining),
    Value(
        "offset",
        "Offset Temperature",
        lambda d: d.offset,
        "°C",
        category="setting",
        write=lambda d, t: write_if_changed(d, "offset", t),
        minimum=OFFSET_RANGE[0],
        maximum=OFFSET_RANGE[1],
        step=0.1,
        number_mode="box",
    ),
    Value(
        "set_delay",
        "Set Delay Time",
        lambda d: d.set_delay,
        "min",
        category="setting",
        device_class="duration",
        write=lambda d, m: write_if_changed(d, "set_delay", m),
        minimum=DELAY_RANGE[0],
        maximum=DELAY_RANGE[1],
        step=1,
        number_mode="box",
    ),
    Value("menu_mode", "Menu Mode", lambda d: d.menu_mode, binary=True, category="diagnostic"),
    Value(
        "sensor_disconnected",
        "Thermometer Alarm",
        lambda d: d.sensor_disconnected,
        binary=True,
        device_class="problem",
    ),
    Value(
        "sensor_supply_fault",
        "Short Connection Alarm",
        lambda d: d.sensor_supply_fault,
        binary=True,
        device_class="problem",
    ),
    Value(
        "eeprom_fault",
        "EEPROM Alarm",
        lambda d: d.eeprom_fault,
        binary=True,
        device_class="problem",
    ),
    Value(
        "software_version",
        "Thermostat Firmware",
        lambda d: d.software_version,
        category="diagnostic",
    ),
)


def _create(unit: ModbusUnit, variant: str | None) -> T010:
    return T010(unit)


DEVICE_TYPE = DeviceType(
    key="t010",
    name="T010 pool thermostat",
    manufacturer="Optika ir technologija",  # start-up screen: "THERMOSTAT T010", "O&Technologija"
    models=("T010",),
    create=_create,
    values=VALUES,
    thermostat=THERMOSTAT,
    identify=lambda d: d.is_t010,
    software_version=lambda d: d.software_version,
    default_unit_id=1,  # factory default in the firmware
)
