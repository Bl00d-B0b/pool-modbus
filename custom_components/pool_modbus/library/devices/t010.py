"""T010 pool thermostat by Optika ir technologija (baseinai.lt), standard Modbus addressing.

Register map from the device firmware (software version 1.7); see
docs/devices/t010.md. The firmware answers function 03 for addresses 0-9, at
most 10 registers per read, and function 06 for addresses 5-9.
"""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, bit, bits, gauge, integer

from .base import DeviceType, Value

INSTRUMENT_TYPE = 0x15
"""What register 0's high byte holds on a T010."""


class T010(Component):
    """T010 thermostat. Addresses are the on-wire register numbers."""

    max_span = 10  # firmware limit
    register_ranges = ((0, 9),)

    instrument_type = bits(0, 8, 8)
    software_version_raw = bits(0, 0, 8)  # 17 means 1.7
    temperature = gauge(1, 0.1, unit="°C")  # offset already applied
    heating_power = integer(2, signed=False, unit="%")  # relay: 0 or 100; PWM: steps of 20
    delay_minutes = bits(3, 8, 8)  # heating delay still to run
    delay_seconds = bits(3, 0, 8)
    setpoint = gauge(5, 0.1, unit="°C")  # 5.0-40.0
    offset = gauge(6, 0.1, unit="°C")  # -3.1 to +3.1
    set_delay = integer(7, signed=False, unit="min")  # 0-59
    heating_blocked = bit(8, 8)
    delaying = bit(8, 9)
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


def _on_off(flag: bool | None) -> str | None:
    return None if flag is None else ("On" if flag else "Off")


VALUES = (
    Value("hvac_mode", "Pool Thermostat", lambda d: d.hvac_mode),
    Value("hvac_action", "Thermostat Action", lambda d: d.hvac_action),
    Value(
        "temperature",
        "Pool Temperature",
        lambda d: d.temperature,
        "°C",
        category="measurement",
        device_class="temperature",
    ),
    Value("heating_enabled", "Pool Heating", lambda d: d.heating_enabled, binary=True),
    Value("heating", "Heating Mode", lambda d: _on_off(d.heating)),
    Value(
        "heating_power",
        "Heating Power",
        lambda d: d.heating_power,
        "%",
        category="measurement",
    ),
    Value("delaying", "Pool Delaying", lambda d: d.delaying, binary=True),
    Value("delay_remaining", "Delay Time", lambda d: d.delay_remaining),
    Value(
        "setpoint",
        "Set Temperature",
        lambda d: d.setpoint,
        "°C",
        category="setting",
        device_class="temperature",
    ),
    Value("offset", "Offset Temperature", lambda d: d.offset, "°C", category="setting"),
    Value(
        "set_delay",
        "Set Delay Time",
        lambda d: d.set_delay,
        "min",
        category="setting",
        device_class="duration",
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
    identify=lambda d: d.is_t010,
    software_version=lambda d: d.software_version,
    default_unit_id=1,  # factory default in the firmware
)
