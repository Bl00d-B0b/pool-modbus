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

from .base import DeviceType, Feature, ImplausibleReadError, Value
from .reading import update_or_keep
from .writing import write_if_changed

# Values the controller sometimes reads as 0 for a single poll although they are
# not (see EmecLD._verify_read). Pulse rates are left out: they drop to 0 whenever
# dosing stops.
_GLITCH_FIELDS = (
    "ch1_reading",
    "ch2_reading",
    "temperature",
    "probe_mv_ch1",
    "probe_mv_ch2",
    "relay_ch1_raw",
    "relay_ch2_raw",
)

# A pulse rate reads 0xFF for a single poll while it sits at zero.
_PULSE_RATE_TRANSIENT = 0xFF


def wire_address(index: int) -> int:
    """Wire address of value ``index``, where ``index = (register - 40002) / 2``."""
    return 2 * index + 1


class EmecUnit:
    """A ``ModbusUnit`` view that maps EMEC value indices to odd wire addresses.

    Coils use normal bit addressing and pass straight through. A value is
    written with function 06 at its odd wire address, one value per request;
    writing several values in one request and writing coils are refused, as
    they are untested.
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
        await self._unit.write_register(wire_address(address), value)

    async def write_registers(self, address: int, values: list[int]) -> None:
        raise NotImplementedError("EMEC LD controllers are written one value per request")

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


PH_RANGE = (0.0, 14.0)
RATE_RANGE = (0, 180)
"""Pulses per minute; the controller's maximum."""
SPEED_RANGE = (0, 99)
"""Minutes between pulses in ON/OFF mode."""


def _hundredths(low: float, high: float):
    def check(value: Any) -> float:
        rounded = round(float(value), 2)
        if not low <= rounded <= high:
            raise ValueError(f"{value} is outside {low} to {high}")
        return rounded

    return check


def _whole(low: int, high: int):
    def check(value: Any) -> int:
        if value != int(value) or not low <= value <= high:
            raise ValueError(f"{value} is not a whole number from {low} to {high}")
        return int(value)

    return check


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
    ch1_divisor = integer(1, signed=False)  # 1, 10, 100 or 1000, never 0
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

    # Channel 1 dosing, pulse output 1 (40068-40078); written one value at a time.
    ch1_pulse_val1 = gauge(33, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse_val2 = gauge(34, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse_perc1 = integer(35, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse_perc2 = integer(36, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse_wait = integer(37, unit="min", writable=_whole(*SPEED_RANGE))
    ch1_pulse_mode = enum(38, PulseMode, writable=PulseMode)

    # Channel 2 proportional dosing (40154-40164).
    ch2_pulse_val1 = gauge(76, 0.01)
    ch2_pulse_val2 = gauge(77, 0.01)
    ch2_pulse_perc1 = integer(78, unit="p/min")
    ch2_pulse_perc2 = integer(79, unit="p/min")
    ch2_pulse_wait = integer(80, unit="min")
    ch2_pulse_mode = enum(81, PulseMode)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_accepted: dict[str, Any] = {}
        self._dropped: frozenset[str] = frozenset()

    def _verify_read(self) -> None:
        """Refuse a read with values the controller cannot hold.

        The tested LDPHCL now and then answers with zeros for a few seconds: all
        of a read (divisors 0, temperature 0.0 °C, probe voltages 0 mV, relays
        disabled), or only some values (both probe voltages, or both relays),
        while the next poll reads normally again. A measurement's divisor is never
        0, and every scan group reads channel 1's (``check_fields``), so a read
        with it at 0 is refused. A value that was not 0 and now reads 0 is refused
        once: a real 0 (pH 7.00 gives a probe voltage of about 0 mV) is accepted
        when the next read shows it too, a glitch is gone by then.
        """
        if "ch1_divisor" in self.resolved_fields and not self.ch1_divisor:
            raise ImplausibleReadError("the controller answered with zeros (channel 1 divisor 0)")
        read = {
            name: getattr(self, name) for name in _GLITCH_FIELDS if name in self.resolved_fields
        }
        dropped = frozenset(
            name for name, value in read.items() if value == 0 and self._last_accepted.get(name)
        )
        if dropped and dropped != self._dropped:
            self._dropped = dropped
            raise ImplausibleReadError(
                f"{', '.join(sorted(dropped))} dropped to 0; shown if the next read agrees"
            )
        self._dropped = frozenset()
        self._last_accepted.update(read)

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


# -- writes -------------------------------------------------------------------
# The controller's proportional mode runs from 0 pulses per minute at one pH value
# to the set rate at the other, so setting one end's rate sets the other end's to
# 0, and a working mode is entered by writing its settings in a fixed order.

_MODE_STEPS = {
    PulseMode.ON_OFF: (
        ("ch1_pulse_perc1", 100),
        ("ch1_pulse_perc2", 0),
        ("ch1_pulse_wait", 1),
        ("ch1_pulse_mode", PulseMode.ON_OFF),
    ),
    PulseMode.PROPORTIONAL: (("ch1_pulse_wait", 0), ("ch1_pulse_mode", PulseMode.PROPORTIONAL)),
    PulseMode.DISABLED: (("ch1_pulse_mode", PulseMode.DISABLED),),
}


async def set_ph_mode(device: EmecLD, label: str) -> None:
    """Switch channel 1's pulse output to ON/OFF, Proportional or Disabled."""
    mode = next((m for m in PulseMode if m.label == label), None)
    if mode is None:
        raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in PulseMode)}")
    await update_or_keep(device)
    if device.ch1_pulse_mode is mode:
        return
    for field, value in _MODE_STEPS[mode]:
        await write_if_changed(device, field, value)


async def set_max_rate(device: EmecLD, rate: float) -> None:
    await write_if_changed(device, "ch1_pulse_perc1", rate)
    await write_if_changed(device, "ch1_pulse_perc2", 0)


async def set_min_rate(device: EmecLD, rate: float) -> None:
    await write_if_changed(device, "ch1_pulse_perc2", rate)
    await write_if_changed(device, "ch1_pulse_perc1", 0)


# Named as on an LDPHCL: channel 1 is pH, channel 2 is free chlorine.
VALUES = (
    Value(
        "ph",
        "pH Level",
        lambda d: d.ch1_value,
        "pH",
        category="measurement",
        device_class="ph",
        icon="mdi:ph",
    ),
    Value(
        "chlorine",
        "Cl Level",
        lambda d: d.ch2_value,
        "ppm",
        category="measurement",
        icon="mdi:flask-outline",
    ),
    Value(
        "temperature",
        "Temperature",
        lambda d: d.temperature,
        "°C",
        category="measurement",
        device_class="temperature",
        icon="mdi:thermometer",
    ),
    Value(
        "relay_ph",
        "pH Relay",
        lambda d: d.relay_ch1,
        device_class="enum",
        options=("On", "Off", "Disabled"),
        icons={"On": "mdi:pump", "Off": "mdi:pump-off", "Disabled": "mdi:pump-off"},
    ),
    Value(
        "relay_cl",
        "Cl Relay",
        lambda d: d.relay_ch2,
        device_class="enum",
        options=("On", "Off", "Disabled"),
        icons={"On": "mdi:pump", "Off": "mdi:pump-off", "Disabled": "mdi:pump-off"},
    ),
    Value(
        "pulse_rate_ph",
        "pH Pulse Rate",
        lambda d: d.pulse_rate_ch1,
        "p/min",
        category="measurement",
        icon="mdi:pulse",
    ),
    Value(
        "pulse_rate_cl",
        "Cl Pulse Rate",
        lambda d: d.pulse_rate_ch2,
        "p/min",
        category="measurement",
        icon="mdi:pulse",
    ),
    Value(
        "probe_ph",
        "pH Probe Voltage",
        lambda d: d.probe_mv_ch1,
        "mV",
        category="measurement",
        device_class="voltage",
        feature="probe_voltages",
        icon="mdi:sine-wave",
    ),
    Value(
        "probe_cl",
        "Cl Probe Voltage",
        lambda d: d.probe_mv_ch2,
        "mV",
        category="measurement",
        device_class="voltage",
        feature="probe_voltages",
        icon="mdi:sine-wave",
    ),
    Value(
        "rtc",
        "RTC",
        lambda d: d.clock,
        category="diagnostic",
        device_class="timestamp",
        feature="clock",
        icon="mdi:clock",
    ),
    Value(
        "ph_mode",
        "pH Dosing Mode",
        lambda d: d.ch1_pulse_mode,
        category="setting",
        feature="dosing_settings",
        write=set_ph_mode,
        options=tuple(mode.label for mode in PulseMode),
        fields=("ch1_pulse_perc1", "ch1_pulse_perc2", "ch1_pulse_wait"),
        icon="mdi:tune-variant",
    ),
    # The pump only uses each dosing setting in some working modes.
    Value(
        "ph_max",
        "pH Max Value",
        lambda d: d.ch1_pulse_val1,
        "pH",
        available=lambda d: d.ch1_pulse_mode is not PulseMode.DISABLED,
        category="setting",
        device_class="ph",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "ch1_pulse_val1", v),
        minimum=PH_RANGE[0],
        maximum=PH_RANGE[1],
        step=0.01,
        number_mode="box",
        icon="mdi:ph",
    ),
    Value(
        "ph_min",
        "pH Min Value",
        lambda d: d.ch1_pulse_val2,
        "pH",
        available=lambda d: d.ch1_pulse_mode is not PulseMode.DISABLED,
        category="setting",
        device_class="ph",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "ch1_pulse_val2", v),
        minimum=PH_RANGE[0],
        maximum=PH_RANGE[1],
        step=0.01,
        number_mode="box",
        icon="mdi:ph",
    ),
    Value(
        "ph_max_rate",
        "pH Max Pulse Rate",
        lambda d: d.ch1_pulse_perc1,
        "p/min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.PROPORTIONAL,
        category="setting",
        feature="dosing_settings",
        write=set_max_rate,
        minimum=RATE_RANGE[0],
        maximum=RATE_RANGE[1],
        step=1,
        number_mode="box",
        fields=("ch1_pulse_perc2",),
        icon="mdi:pulse",
    ),
    Value(
        "ph_min_rate",
        "pH Min Pulse Rate",
        lambda d: d.ch1_pulse_perc2,
        "p/min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.PROPORTIONAL,
        category="setting",
        feature="dosing_settings",
        write=set_min_rate,
        minimum=RATE_RANGE[0],
        maximum=RATE_RANGE[1],
        step=1,
        number_mode="box",
        fields=("ch1_pulse_perc1",),
        icon="mdi:pulse",
    ),
    Value(
        "ph_pulse_speed",
        "pH Pulse Speed",
        lambda d: d.ch1_pulse_wait,
        "min",
        available=lambda d: d.ch1_pulse_mode is PulseMode.ON_OFF,
        category="setting",
        device_class="duration",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "ch1_pulse_wait", v),
        minimum=SPEED_RANGE[0],
        maximum=SPEED_RANGE[1],
        step=1,
        number_mode="box",
        icon="mdi:timer-cog-outline",
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
    features=(
        Feature("dosing_settings", "Dosing settings (pH channel)"),
        Feature("probe_voltages", "Probe voltages"),
        Feature("clock", "Clock"),
    ),
    identify=_identify,
    default_unit_id=1,
    message_spacing=0.1,  # the protocol notes ask for at least 100 ms between requests
    check_fields=("ch1_divisor",),
)
