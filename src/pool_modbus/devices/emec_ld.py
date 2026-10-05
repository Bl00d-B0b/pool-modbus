"""EMEC LD-series pH / chlorine controllers.

Tested on an LDPHCL (firmware 5.1.4) behind a Modbus TCP-to-RS-485 gateway.
Register map: docs/devices/emec_ld.md.

Addressing. The controller is byte-addressed: every 16-bit value takes two
Modbus addresses and sits at an odd wire address, ``register - 40001``. A read
of N registers from an odd address returns the N values at a, a+2, a+4, ...;
reads must start at an odd address. ``EmecUnit`` numbers the values 0, 1, 2,
... (``index = (register - 40002) / 2``) and sends each read to wire address
``2 * index + 1``, so the model's read planner works unchanged.

Formats. Most values are plain 16-bit words. Output states and pulse rates are
one byte in the high half of the word (low byte 0). The clock packs two bytes
into each word.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, PackedBitsField, bits, enum, gauge, integer

from .base import DeviceType, Value

# A pulse rate reads 0xFF for a single poll while it sits at zero.
_PULSE_RATE_TRANSIENT = 0xFF


def wire_address(index: int) -> int:
    """Wire address of value ``index``, where ``index = (register - 40002) / 2``."""
    return 2 * index + 1


class EmecUnit:
    """A ``ModbusUnit`` view that maps EMEC value indices to odd wire addresses.

    Coils use normal bit addressing and pass straight through. Writes are
    refused until write support is implemented and tested.
    """

    def __init__(self, unit: ModbusUnit) -> None:
        self._unit = unit

    @property
    def connected(self) -> bool:
        return self._unit.connected

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._unit.read_holding_registers(wire_address(address), count)

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        return await self._unit.read_input_registers(wire_address(address), count)

    async def read_coils(self, address: int, count: int) -> list[bool]:
        return await self._unit.read_coils(address, count)

    async def write_register(self, address: int, value: int) -> None:
        raise NotImplementedError("writing to EMEC LD controllers is not supported yet")

    async def write_registers(self, address: int, values: list[int]) -> None:
        raise NotImplementedError("writing to EMEC LD controllers is not supported yet")

    async def write_coil(self, address: int, value: bool) -> None:
        raise NotImplementedError("writing to EMEC LD controllers is not supported yet")

    async def write_coils(self, address: int, values: list[bool]) -> None:
        raise NotImplementedError("writing to EMEC LD controllers is not supported yet")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._unit, name)


class OutputState(IntEnum):
    """State of a relay output (40024, 40032)."""

    DISABLED = 0
    ON = 1
    OFF = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()


class PulseMode(IntEnum):
    """Working mode of a pulse output (40078, 40150, 40164)."""

    ON_OFF = 0
    PROPORTIONAL = 1
    DISABLED = 2

    @property
    def label(self) -> str:
        return "ON/OFF" if self is PulseMode.ON_OFF else self.name.capitalize()


def high_byte(index: int) -> PackedBitsField:
    """A value stored in the high byte of the word at ``index``."""
    return bits(index, 8, 8)


class EmecLD(Component):
    """EMEC LD-series controller. Addresses are value indices, see ``EmecUnit``.

    On an LDPHCL, channel 1 is pH and channel 2 is free chlorine in ppm.
    """

    max_span = 125  # values per read, as EMEC's protocol description allows; tested on an LDPHCL
    register_ranges = ((0, 919),)  # every value up to 41840 answers a read

    # Measurements (40002-40008); the value is reading / divisor.
    ch1_reading = integer(0)
    ch1_divisor = integer(1, signed=False)  # 1, 10, 100 or 1000
    ch2_reading = integer(2)
    ch2_divisor = integer(3, signed=False)

    # Outputs (40024-40032), one byte in the high half of the word.
    relay_ch2_raw = high_byte(11)  # 40024 relay output, channel 2 (Cl)
    pulse_rate_ch1_raw = high_byte(12)  # 40026 pulse rate, channel 1 output IS1
    pulse_rate_ch1_2_raw = high_byte(13)  # 40028 pulse rate, channel 1 output IS2
    pulse_rate_ch2_raw = high_byte(14)  # 40030 pulse rate, channel 2
    relay_ch1_raw = high_byte(15)  # 40032 relay output, channel 1 (pH)

    # Clock (40044-40048), two bytes per word.
    clock_month = bits(21, 8, 8)
    clock_day = bits(21, 0, 8)
    clock_hour = bits(22, 8, 8)
    clock_year = bits(22, 0, 8)
    clock_minute = bits(23, 0, 8)

    temperature = gauge(25, 0.1, unit="°C")  # 40052
    probe_mv_ch1 = integer(27, unit="mV")  # 40056
    probe_mv_ch2 = integer(28, unit="mV")  # 40058

    # Channel 1 proportional dosing, pulse output 1 (40068-40078).
    ch1_pulse_val1 = gauge(33, 0.01)
    ch1_pulse_val2 = gauge(34, 0.01)
    ch1_pulse_perc1 = integer(35, unit="p/min")
    ch1_pulse_perc2 = integer(36, unit="p/min")
    ch1_pulse_wait = integer(37, unit="min")
    ch1_pulse_mode = enum(38, PulseMode)

    # Channel 2 proportional dosing (40154-40164).
    ch2_pulse_val1 = gauge(76, 0.01)
    ch2_pulse_val2 = gauge(77, 0.01)
    ch2_pulse_perc1 = integer(78, unit="p/min")
    ch2_pulse_perc2 = integer(79, unit="p/min")
    ch2_pulse_wait = integer(80, unit="min")
    ch2_pulse_mode = enum(81, PulseMode)

    @property
    def ch1_value(self) -> float | None:
        """Channel 1 measurement (pH on an LDPHCL)."""
        return _ratio(self.ch1_reading, self.ch1_divisor)

    @property
    def ch2_value(self) -> float | None:
        """Channel 2 measurement (free chlorine in ppm on an LDPHCL)."""
        return _ratio(self.ch2_reading, self.ch2_divisor)

    @property
    def relay_ch1(self) -> OutputState | None:
        return _output_state(self.relay_ch1_raw)

    @property
    def relay_ch2(self) -> OutputState | None:
        return _output_state(self.relay_ch2_raw)

    @property
    def pulse_rate_ch1(self) -> int | None:
        """Pulses per minute of channel 1's pulse output 1."""
        return _pulse_rate(self.pulse_rate_ch1_raw)

    @property
    def pulse_rate_ch1_2(self) -> int | None:
        """Pulses per minute of channel 1's pulse output 2."""
        return _pulse_rate(self.pulse_rate_ch1_2_raw)

    @property
    def pulse_rate_ch2(self) -> int | None:
        """Pulses per minute of channel 2's pulse output."""
        return _pulse_rate(self.pulse_rate_ch2_raw)

    @property
    def clock(self) -> datetime | None:
        """The controller's clock, without time zone; None if unread or invalid."""
        parts = (self.clock_year, self.clock_month, self.clock_day, self.clock_hour)
        if None in parts or self.clock_minute is None:
            return None
        try:
            return datetime(
                2000 + self.clock_year,
                self.clock_month,
                self.clock_day,
                self.clock_hour,
                self.clock_minute,
            )
        except ValueError:
            return None


def _ratio(reading: int | None, divisor: int | None) -> float | None:
    if reading is None or not divisor:
        return None
    return reading / divisor


def _output_state(raw: int | None) -> OutputState | None:
    if raw is None:
        return None
    try:
        return OutputState(raw)
    except ValueError:
        return None


def _pulse_rate(raw: int | None) -> int | None:
    if raw is None:
        return None
    return 0 if raw == _PULSE_RATE_TRANSIENT else raw


def _create(unit: ModbusUnit, variant: str | None) -> EmecLD:
    return EmecLD(EmecUnit(unit))


# Named as on an LDPHCL: channel 1 is pH, channel 2 is free chlorine.
VALUES = (
    Value(
        "ph",
        "Pool pH Level",
        lambda d: d.ch1_value,
        "pH",
        category="measurement",
        device_class="ph",
    ),
    Value("chlorine", "Pool Cl Level", lambda d: d.ch2_value, "ppm", category="measurement"),
    Value(
        "temperature",
        "Dispenser Temperature",
        lambda d: d.temperature,
        "°C",
        category="measurement",
        device_class="temperature",
    ),
    Value("relay_ph", "Out Relay pH", lambda d: d.relay_ch1),
    Value("relay_cl", "Out Relay Cl", lambda d: d.relay_ch2),
    Value(
        "pulse_rate_ph",
        "pH Pump Pulse Rate",
        lambda d: d.pulse_rate_ch1,
        "p/min",
        category="measurement",
    ),
    Value(
        "pulse_rate_cl",
        "Cl Pump Pulse Rate",
        lambda d: d.pulse_rate_ch2,
        "p/min",
        category="measurement",
    ),
    Value(
        "probe_ph",
        "pH Probe Voltage",
        lambda d: d.probe_mv_ch1,
        "mV",
        category="measurement",
        device_class="voltage",
    ),
    Value(
        "probe_cl",
        "Cl Probe Voltage",
        lambda d: d.probe_mv_ch2,
        "mV",
        category="measurement",
        device_class="voltage",
    ),
    Value(
        "clock",
        "Dispenser Last Update",
        lambda d: d.clock,
        category="diagnostic",
        device_class="timestamp",
    ),
    Value("ph_mode", "Ch1 pH pulse1 Mode", lambda d: d.ch1_pulse_mode, category="setting"),
    # The pump only uses each dosing setting in some working modes.
    Value(
        "ph_max",
        "pH Max Value",
        lambda d: d.ch1_pulse_val1,
        "pH",
        available=lambda d: d.ch1_pulse_mode is not PulseMode.DISABLED,
        category="setting",
        device_class="ph",
    ),
    Value(
        "ph_min",
        "pH Min Value",
        lambda d: d.ch1_pulse_val2,
        "pH",
        available=lambda d: d.ch1_pulse_mode is not PulseMode.DISABLED,
        category="setting",
        device_class="ph",
    ),
    Value(
        "ph_max_rate",
        "Max Pulse Rate",
        lambda d: d.ch1_pulse_perc1,
        "p/min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.PROPORTIONAL,
        category="setting",
    ),
    Value(
        "ph_min_rate",
        "Min Pulse Rate",
        lambda d: d.ch1_pulse_perc2,
        "p/min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.PROPORTIONAL,
        category="setting",
    ),
    Value(
        "ph_pulse_speed",
        "Pulse Speed",
        lambda d: d.ch1_pulse_wait,
        "min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.ON_OFF,
        category="setting",
        device_class="duration",
    ),
)

_DIVISORS = frozenset({1, 10, 100, 1000})


def _identify(device: EmecLD) -> bool:
    """The controller has no ID register; valid measurement divisors are a good sign."""
    return device.ch1_divisor in _DIVISORS and device.ch2_divisor in _DIVISORS


DEVICE_TYPE = DeviceType(
    key="emec_ld",
    name="EMEC LD series pH/Cl controller",
    manufacturer="EMEC",
    models=("LDPHCL",),
    create=_create,
    values=VALUES,
    identify=_identify,
    default_unit_id=1,
    message_spacing=0.1,  # the protocol notes ask for at least 100 ms between requests
)
