"""EMEC LD-series pH / chlorine controllers. Tested on an LDPHCL, firmware 5.1.4.

The controller is byte-addressed: a value sits at odd wire address
``register - 40001``, and a read from an odd address returns every second
register. ``EmecUnit`` numbers the values 0, 1, 2, ... (``(register - 40002) / 2``)
and maps them to the wire. Register map and formats: docs/devices/emec_ld.md.
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

    Coils pass straight through. Values are written one per request (function
    06); multi-register and coil writes are refused as untested.
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
        raise NotImplementedError("coil writes to EMEC LD controllers are untested")

    async def write_coils(self, address: int, values: list[bool]) -> None:
        raise NotImplementedError("coil writes to EMEC LD controllers are untested")

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
CL_RANGE = (0.0, 20.0)
"""ppm; wider than any pool needs, the controller's own limit is not documented."""
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

    # Channel 1 dosing, pulse outputs 1 (40068-40078) and 2 (40140-40150), the
    # same layout; written one value at a time.
    ch1_pulse1_val1 = gauge(33, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse1_val2 = gauge(34, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse1_perc1 = integer(35, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse1_perc2 = integer(36, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse1_wait = integer(37, unit="min", writable=_whole(*SPEED_RANGE))
    ch1_pulse1_mode = enum(38, PulseMode, writable=PulseMode)

    ch1_pulse2_val1 = gauge(69, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse2_val2 = gauge(70, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_pulse2_perc1 = integer(71, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse2_perc2 = integer(72, unit="p/min", writable=_whole(*RATE_RANGE))
    ch1_pulse2_wait = integer(73, unit="min", writable=_whole(*SPEED_RANGE))
    ch1_pulse2_mode = enum(74, PulseMode, writable=PulseMode)

    # Channel 2 dosing, its pulse output (40154-40164), the same layout.
    ch2_pulse1_val1 = gauge(76, 0.01, writable=_hundredths(*CL_RANGE))
    ch2_pulse1_val2 = gauge(77, 0.01, writable=_hundredths(*CL_RANGE))
    ch2_pulse1_perc1 = integer(78, unit="p/min", writable=_whole(*RATE_RANGE))
    ch2_pulse1_perc2 = integer(79, unit="p/min", writable=_whole(*RATE_RANGE))
    ch2_pulse1_wait = integer(80, unit="min", writable=_whole(*SPEED_RANGE))
    ch2_pulse1_mode = enum(81, PulseMode, writable=PulseMode)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_accepted: dict[str, Any] = {}
        self._dropped: frozenset[str] = frozenset()

    def _verify_read(self) -> None:
        """Refuse a read with zeros the controller cannot hold.

        The LDPHCL sometimes answers with zeros for one poll: all values, or some
        (both probe voltages, both relays). A divisor is never 0, so a read with
        a divisor at 0 is refused; a value that drops to 0 is refused once and
        accepted when the next read agrees (a real 0 shows a poll later).
        """
        for name in ("ch1_divisor", "ch2_divisor"):
            if name in self.resolved_fields and not getattr(self, name):
                raise ImplausibleReadError(f"the controller answered with zeros ({name} is 0)")
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
# Proportional mode runs from 0 p/min at one pH end to the set rate at the other,
# so one end's rate zeroes the other's; a mode is entered in a fixed write order.
# The pulse outputs share the code: channel 1 has outputs 1 and 2, channel 2 output 1.


def _field(channel: int, output: int, name: str) -> str:
    return f"ch{channel}_pulse{output}_{name}"


def _mode_steps(channel: int, output: int, mode: PulseMode) -> tuple[tuple[str, Any], ...]:
    f = lambda name: _field(channel, output, name)  # noqa: E731
    if mode is PulseMode.ON_OFF:
        return ((f("perc1"), 100), (f("perc2"), 0), (f("wait"), 1), (f("mode"), mode))
    if mode is PulseMode.PROPORTIONAL:
        return ((f("wait"), 0), (f("mode"), mode))
    return ((f("mode"), mode),)


async def set_dosing_mode(device: EmecLD, label: str, channel: int = 1, output: int = 1) -> None:
    """Switch a pulse output to ON/OFF, Proportional or Disabled."""
    mode = next((m for m in PulseMode if m.label == label), None)
    if mode is None:
        raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in PulseMode)}")
    await update_or_keep(device)
    if getattr(device, _field(channel, output, "mode")) is mode:
        return
    for field, value in _mode_steps(channel, output, mode):
        await write_if_changed(device, field, value)


async def set_max_rate(device: EmecLD, rate: float, channel: int = 1, output: int = 1) -> None:
    """The rate at the output's Max Value; the rate at its Min Value goes to 0."""
    await write_if_changed(device, _field(channel, output, "perc1"), rate)
    await write_if_changed(device, _field(channel, output, "perc2"), 0)


async def set_min_rate(device: EmecLD, rate: float, channel: int = 1, output: int = 1) -> None:
    """The rate at the output's Min Value; the rate at its Max Value goes to 0."""
    await write_if_changed(device, _field(channel, output, "perc2"), rate)
    await write_if_changed(device, _field(channel, output, "perc1"), 0)


_RATE_FIELDS = {(1, 1): "pulse_rate_ch1", (1, 2): "pulse_rate_ch1_2", (2, 1): "pulse_rate_ch2"}


def _output_values(channel: int, output: int) -> tuple[Value, ...]:
    """The pulse rate and the dosing settings of one pulse output, named "pH Relay 1",
    "pH Relay 2" and "Cl Relay" as the user calls the dosing pumps on them.
    The pump only uses each setting in some working modes."""
    symbol, key, unit, device_class, value_range = (
        ("pH", "ph", "pH", "ph", PH_RANGE) if channel == 1 else ("Cl", "cl", "ppm", None, CL_RANGE)
    )
    prefix = f"{symbol} Relay {output}" if channel == 1 else f"{symbol} Relay"  # Cl has one
    f = lambda name: _field(channel, output, name)  # noqa: E731
    mode_of = lambda d: getattr(d, f("mode"))  # noqa: E731
    writer = lambda name: lambda d, v: write_if_changed(d, f(name), v)  # noqa: E731
    return (
        Value(
            f"pulse_rate_{key}_{output}",
            f"{prefix} Pulse Rate",
            lambda d: getattr(d, _RATE_FIELDS[channel, output]),
            "p/min",
            category="measurement",
            icon="mdi:pulse",
        ),
        Value(
            f"{key}{output}_mode",
            f"{prefix} Dosing Mode",
            mode_of,
            category="setting",
            feature="dosing_settings",
            write=lambda d, label: set_dosing_mode(d, label, channel, output),
            options=tuple(mode.label for mode in PulseMode),
            fields=(f("perc1"), f("perc2"), f("wait")),
            icon="mdi:tune-variant",
            icons={
                "ON/OFF": "mdi:toggle-switch-outline",
                "Proportional": "mdi:chart-line-variant",
                "Disabled": "mdi:cancel",
            },
        ),
        Value(
            f"{key}{output}_max",
            f"{prefix} Max Value",
            lambda d: getattr(d, f("val1")),
            unit,
            available=lambda d: mode_of(d) is not PulseMode.DISABLED,
            category="setting",
            device_class=device_class,
            feature="dosing_settings",
            write=writer("val1"),
            minimum=value_range[0],
            maximum=value_range[1],
            step=0.01,
            number_mode="box",
            icon="mdi:gauge-full",
        ),
        Value(
            f"{key}{output}_min",
            f"{prefix} Min Value",
            lambda d: getattr(d, f("val2")),
            unit,
            available=lambda d: mode_of(d) is not PulseMode.DISABLED,
            category="setting",
            device_class=device_class,
            feature="dosing_settings",
            write=writer("val2"),
            minimum=value_range[0],
            maximum=value_range[1],
            step=0.01,
            number_mode="box",
            icon="mdi:gauge-empty",
        ),
        Value(
            f"{key}{output}_max_rate",
            f"{prefix} Max Pulse Rate",
            lambda d: getattr(d, f("perc1")),
            "p/min",
            available=lambda d: mode_of(d) is PulseMode.PROPORTIONAL,
            category="setting",
            feature="dosing_settings",
            write=lambda d, v: set_max_rate(d, v, channel, output),
            minimum=RATE_RANGE[0],
            maximum=RATE_RANGE[1],
            step=1,
            number_mode="box",
            fields=(f("perc2"),),
            icon="mdi:speedometer",
        ),
        Value(
            f"{key}{output}_min_rate",
            f"{prefix} Min Pulse Rate",
            lambda d: getattr(d, f("perc2")),
            "p/min",
            available=lambda d: mode_of(d) is PulseMode.PROPORTIONAL,
            category="setting",
            feature="dosing_settings",
            write=lambda d, v: set_min_rate(d, v, channel, output),
            minimum=RATE_RANGE[0],
            maximum=RATE_RANGE[1],
            step=1,
            number_mode="box",
            fields=(f("perc1"),),
            icon="mdi:speedometer-slow",
        ),
        Value(
            f"{key}{output}_pulse_speed",
            f"{prefix} Pulse Speed",
            lambda d: getattr(d, f("wait")),
            "min",
            available=lambda d: mode_of(d) is PulseMode.ON_OFF,
            category="setting",
            device_class="duration",
            feature="dosing_settings",
            write=writer("wait"),
            minimum=SPEED_RANGE[0],
            maximum=SPEED_RANGE[1],
            step=1,
            number_mode="box",
            icon="mdi:timer-cog-outline",
        ),
    )


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
        "probe_ph",
        "pH Probe Voltage",
        lambda d: d.probe_mv_ch1,
        "mV",
        category="diagnostic",
        scan_group="medium",
        device_class="voltage",
        feature="probe_voltages",
        icon="mdi:sine-wave",
    ),
    Value(
        "probe_cl",
        "Cl Probe Voltage",
        lambda d: d.probe_mv_ch2,
        "mV",
        category="diagnostic",
        scan_group="medium",
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
        icon="mdi:clock-outline",
    ),
    *_output_values(1, 1),
    *_output_values(1, 2),
    *_output_values(2, 1),
)

_DIVISORS = frozenset({1, 10, 100, 1000})


def _identify(device: EmecLD) -> bool:
    """The controller has no ID register; valid measurement divisors are a good sign."""
    return device.ch1_divisor in _DIVISORS and device.ch2_divisor in _DIVISORS


DEVICE_TYPE = DeviceType(
    key="emec_ld",
    default_name="Dosing pump",
    entity_id_base="dosing_pump",
    name="EMEC LD series pH/Cl controller",
    manufacturer="EMEC",
    models=("LDPHCL",),
    create=_create,
    values=VALUES,
    features=(
        Feature("dosing_settings", "Dosing settings (pH and Cl pulse outputs)"),
        Feature("probe_voltages", "Probe voltages"),
        Feature("clock", "Clock"),
    ),
    identify=_identify,
    default_unit_id=1,
    message_spacing=0.1,  # the protocol notes ask for at least 100 ms between requests
    check_fields=("ch1_divisor", "ch2_divisor"),
)
