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
from modbus_connection.model import Component, PackedBitsField, bits, coil, enum, gauge, integer

from .base import DeviceType, Feature, ImplausibleReadError, Value
from .reading import update_or_keep
from .writing import write_if_changed

# Values the controller sometimes reads as 0 for a single poll although they are
# not (see EmecLD._verify_read). Pulse rates are left out: they drop to 0 whenever
# dosing stops.
_GLITCH_FIELDS = (
    "ch1_reading",
    "ch2_reading",
    "temperature_raw",
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


COIL_BLOCK = 16
"""Alarm coils are read as one block of 16 from address 0 (see EmecUnit.read_coils)."""

CONFIG_COILS = {58: 62}
"""Configuration coils by write address, with the position the tested controller
shows them at in a read of 128 coils from 0 (found 2026-10-14 by switching each on
the keypad: coil 58, the dosing priority, reads at 62). Only these are read or
written above the alarm block. The units-format coils (45 AM at 43, 46 PM at 42, 47 USA
at 41) are documented in docs/devices/emec_ld.md but not used: the library reads the
controller in Europe format only."""
WRITABLE_COILS = frozenset({58})
"""Coils whose writes were tested and are used."""

CONFIG_BLOCK = 128
CONFIG_SPAN = (min(CONFIG_COILS), max(CONFIG_COILS) + 1)
"""The configuration coils are read as one span, so in one read of 128."""


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
        """The tested controller answers a coil read with bits that shift with the
        start address and the count. The alarm coils 0-15 are taken from a read of
        16 from 0, the configuration coils from a read of 128 from 0 at the position
        found for each (``CONFIG_COILS``); any other coil is refused."""
        wanted = range(address, address + count)
        outside = [a for a in wanted if COIL_BLOCK <= a < CONFIG_SPAN[0] or a >= CONFIG_SPAN[1]]
        if outside:
            raise ValueError(f"coils {outside}: only 0-{COIL_BLOCK - 1} and {CONFIG_SPAN}")
        alarms = await self._unit.read_coils(0, COIL_BLOCK) if wanted[0] < COIL_BLOCK else None
        config = await self._unit.read_coils(0, CONFIG_BLOCK) if wanted[-1] >= COIL_BLOCK else None
        # Configuration coils with no known read position read as off; no value uses them.
        return [
            alarms[a] if a < COIL_BLOCK else bool(a in CONFIG_COILS and config[CONFIG_COILS[a]])
            for a in wanted
        ]

    async def write_register(self, address: int, value: int) -> None:
        await self._unit.write_register(wire_address(address), value)

    async def write_registers(self, address: int, values: list[int]) -> None:
        raise NotImplementedError("EMEC LD controllers are written one value per request")

    async def write_coil(self, address: int, value: bool) -> None:
        """Function 05; tested for the coils in ``WRITABLE_COILS`` only."""
        if address not in WRITABLE_COILS:
            raise NotImplementedError(f"coil {address}: only {sorted(WRITABLE_COILS)} were tested")
        await self._unit.write_coil(address, value)

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


class RelayMode(IntEnum):
    """Working mode of a switching relay output (40090, 40220)."""

    PROPORTIONAL_PWM = 0
    ON_OFF = 1
    FIXED_PWM = 2
    DISABLED = 3

    @property
    def label(self) -> str:
        return {0: "Proportional PWM", 1: "ON/OFF", 2: "Fixed PWM", 3: "Disabled"}[self.value]


DUTY_RANGE = (0, 100)
"""Percent of the 100 s PWM cycle the relay is on (the instruction manual: 100 % = 100 s)."""


class FlowMode(IntEnum):
    """How the flow sensor input is used (40526)."""

    DISABLED = 0
    REVERSE = 1
    DIRECT = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()


PH_RANGE = (0.0, 14.0)
CL_RANGE = (0.0, 10.0)
"""mg/l, the chlorine working range in the controller's instruction manual."""
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

    On an LDPHCL, channel 1 is pH and channel 2 is free chlorine in mg/l (Cl₂ on its display).
    """

    max_span = 125  # values per read, as EMEC's protocol description allows; tested on an LDPHCL
    register_ranges = ((0, 919),)  # every value up to 41840 answers a read
    # the alarm block, always read whole, and the configuration coils
    coil_ranges = ((0, COIL_BLOCK - 1), (CONFIG_SPAN[0], CONFIG_SPAN[1] - 1))

    # Dosing priority (coil 58, the menu's "Mode"): on = no priority, off = pH first.
    no_ph_priority = coil(58, writable=True)
    # Alarm and state coils (function 01), coils 0-15 in one read, numbered from 1
    # as in the protocol description. Seen live on 2026-10-14: coil 11 while the
    # filtration pump was off (standby); coil 6 whenever the display says "no flow":
    # for the flow time after flow came back, and once while the filtration ran.
    # The tank coils follow the user's notes, read as counting from 0 (their
    # "5 = no flow" is coil 6): 7 = Cl tank empty, 8 = pH tank empty.
    alarm_coil_01 = coil(0)
    alarm_coil_02 = coil(1)
    alarm_coil_03 = coil(2)
    alarm_coil_04 = coil(3)
    alarm_coil_05 = coil(4)
    alarm_coil_06 = coil(5)  # no flow
    alarm_coil_07 = coil(6)  # Cl tank empty (user's note)
    alarm_coil_08 = coil(7)  # pH tank empty (user's note)
    alarm_coil_09 = coil(8)
    alarm_coil_10 = coil(9)
    alarm_coil_11 = coil(10)  # standby (no flow)
    alarm_coil_12 = coil(11)
    alarm_coil_13 = coil(12)
    alarm_coil_14 = coil(13)
    alarm_coil_15 = coil(14)
    alarm_coil_16 = coil(15)

    # Timing (40520-40526): the measurement filter (tau), the feeding delay before
    # dosing resumes after flow or power returns, the flow sensor's time and mode.
    tau = integer(259, writable=_whole(0, 30))
    feeding_delay = integer(260, unit="min", writable=_whole(0, 60))
    flow_time = integer(261, unit="min", writable=_whole(0, 99))
    flow_mode = enum(262, FlowMode, writable=FlowMode)

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

    temperature_raw = integer(25)  # 40052: °C × 10 (Europe format, the only one read)
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

    # Switching relays "CH1 PH RELAY" (40082-40090) and "CH2 CL2 RELAY" (40212-40220):
    # val1, val2, the duty cycle (%) at each, mode. Ton/Toff of Fixed PWM are not mapped.
    ch1_relay_val1 = gauge(40, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_relay_val2 = gauge(41, 0.01, writable=_hundredths(*PH_RANGE))
    ch1_relay_perc1 = integer(42, unit="%", writable=_whole(*DUTY_RANGE))
    ch1_relay_perc2 = integer(43, unit="%", writable=_whole(*DUTY_RANGE))
    ch1_relay_mode = enum(44, RelayMode, writable=RelayMode)
    ch2_relay_val1 = gauge(105, 0.01, writable=_hundredths(*CL_RANGE))
    ch2_relay_val2 = gauge(106, 0.01, writable=_hundredths(*CL_RANGE))
    ch2_relay_perc1 = integer(107, unit="%", writable=_whole(*DUTY_RANGE))
    ch2_relay_perc2 = integer(108, unit="%", writable=_whole(*DUTY_RANGE))
    ch2_relay_mode = enum(109, RelayMode, writable=RelayMode)

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
        """Channel 2 measurement (free chlorine in mg/l on an LDPHCL)."""
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
    def no_flow(self) -> bool | None:
        """On while the controller sees no flow and holds dosing: the outputs stay off,
        also for the flow time (40524) after the flow contact closes again."""
        return self.alarm_coil_06

    @property
    def cl_tank_empty(self) -> bool | None:
        return self.alarm_coil_07

    @property
    def ph_tank_empty(self) -> bool | None:
        return self.alarm_coil_08

    @property
    def standby(self) -> bool | None:
        """On while the controller is held in standby, e.g. no flow: outputs off."""
        return self.alarm_coil_11

    @property
    def active_alarm_coils(self) -> str | None:
        """The set coils as "6, 11", or "None"; for coils not named yet."""
        states = [getattr(self, f"alarm_coil_{n:02d}") for n in range(1, COIL_BLOCK + 1)]
        if any(state is None for state in states):
            return None
        active = [str(n) for n, state in enumerate(states, start=1) if state]
        return ", ".join(active) if active else "None"

    @property
    def temperature(self) -> float | None:
        """Water temperature in °C (the controller in Europe format)."""
        if self.temperature_raw is None:
            return None
        return self.temperature_raw / 10

    @property
    def clock(self) -> datetime | None:
        """The controller's clock in Europe format, without time zone; None if unread
        or invalid."""
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


def _mode_steps(
    channel: int, output: int, mode: PulseMode, wait: int | None
) -> tuple[tuple[str, Any], ...]:
    """The writes the keypad makes for a mode: ON/OFF forces the top rate to 100 and
    gives a 0 interval 1 minute; Proportional and Off zero the interval (seen on the
    keypad 2026-10-14: ON/OFF with 3 min -> Off left 0). Values already held are skipped."""
    f = lambda name: _field(channel, output, name)  # noqa: E731
    if mode is PulseMode.ON_OFF:
        interval = ((f("wait"), 1),) if not wait else ()
        return ((f("perc1"), 100), (f("perc2"), 0), *interval, (f("mode"), mode))
    if mode is PulseMode.PROPORTIONAL:
        return ((f("wait"), 0), (f("mode"), mode))
    return ((f("wait"), 0), (f("mode"), mode))


async def set_dosing_mode(device: EmecLD, label: str, channel: int = 1, output: int = 1) -> None:
    """Switch a pulse output to ON/OFF, Proportional or Disabled."""
    mode = next((m for m in PulseMode if m.label == label), None)
    if mode is None:
        raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in PulseMode)}")
    await update_or_keep(device)
    if getattr(device, _field(channel, output, "mode")) is mode:
        return
    wait = getattr(device, _field(channel, output, "wait"))
    for field, value in _mode_steps(channel, output, mode, wait):
        await write_if_changed(device, field, value)


async def set_max_rate(device: EmecLD, rate: float, channel: int = 1, output: int = 1) -> None:
    """The rate at the output's Max Value; the rate at its Min Value goes to 0."""
    await write_if_changed(device, _field(channel, output, "perc1"), rate)
    await write_if_changed(device, _field(channel, output, "perc2"), 0)


async def set_min_rate(device: EmecLD, rate: float, channel: int = 1, output: int = 1) -> None:
    """The rate at the output's Min Value; the rate at its Max Value goes to 0."""
    await write_if_changed(device, _field(channel, output, "perc2"), rate)
    await write_if_changed(device, _field(channel, output, "perc1"), 0)


PRIORITY_OPTIONS = ("No priority", "pH priority")


def _priority(device: EmecLD) -> str | None:
    if device.no_ph_priority is None:
        return None
    return PRIORITY_OPTIONS[0] if device.no_ph_priority else PRIORITY_OPTIONS[1]


async def set_priority(device: EmecLD, label: str) -> None:
    """The dosing priority, as in the controller's menu: No priority or pH priority."""
    if label not in PRIORITY_OPTIONS:
        raise ValueError(f"{label!r} is not one of {', '.join(PRIORITY_OPTIONS)}")
    await write_if_changed(device, "no_ph_priority", label == PRIORITY_OPTIONS[0])


async def set_flow_mode(device: EmecLD, label: str) -> None:
    """How the flow contact is read (40526): Disabled, Reverse (N.O.) or Direct (N.C.)."""
    mode = next((m for m in FlowMode if m.label == label), None)
    if mode is None:
        raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in FlowMode)}")
    await write_if_changed(device, "flow_mode", mode)


_RATE_FIELDS = {(1, 1): "pulse_rate_ch1", (1, 2): "pulse_rate_ch1_2", (2, 1): "pulse_rate_ch2"}


def _channel(channel: int) -> tuple[str, str, str, str | None, tuple[float, float]]:
    """Symbol, key, unit, device class and value range of a measuring channel."""
    if channel == 1:
        return "pH", "ph", "pH", "ph", PH_RANGE
    return "Cl", "cl", "mg/L", None, CL_RANGE


def _output_values(channel: int, output: int) -> tuple[Value, ...]:
    """The pulse rate and the dosing settings of one pulse output, named as on the
    controller: "pH Pulse 1", "pH Pulse 2" ("CH1 PH PULSE 1/2") and "Cl Pulse" ("CH2 CL2
    PULSE"). The pump only uses each setting in some working modes."""
    symbol, key, unit, device_class, value_range = _channel(channel)
    prefix = f"{symbol} Pulse {output}" if channel == 1 else f"{symbol} Pulse"  # Cl has one
    f = lambda name: _field(channel, output, name)  # noqa: E731
    mode_of = lambda d: getattr(d, f("mode"))  # noqa: E731
    writer = lambda name: lambda d, v: write_if_changed(d, f(name), v)  # noqa: E731
    return (
        Value(
            f"pulse_rate_{key}_{output}",
            f"{prefix} Rate",
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
            f"{prefix} Max Rate",
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
            f"{prefix} Min Rate",
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


def _relay_values(channel: int) -> tuple[Value, ...]:
    """The settings of a switching relay, "pH Relay" ("CH1 PH RELAY") or "Cl Relay"
    ("CH2 CL2 RELAY"): the two values, the duty cycle at each (Proportional PWM) and the
    mode. Its on/off state is the "... Relay State" sensor (40032, 40024)."""
    symbol, key, unit, device_class, value_range = _channel(channel)
    prefix = f"{symbol} Relay"
    f = lambda name: f"ch{channel}_relay_{name}"  # noqa: E731
    mode_of = lambda d: getattr(d, f("mode"))  # noqa: E731
    writer = lambda name: lambda d, v: write_if_changed(d, f(name), v)  # noqa: E731

    async def set_mode(device: EmecLD, label: str) -> None:
        mode = next((m for m in RelayMode if m.label == label), None)
        if mode is None:
            raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in RelayMode)}")
        await write_if_changed(device, f("mode"), mode)

    def number(suffix, name, field, unit_, minimum, maximum, step, icon, available, dc=None):
        return Value(
            f"{key}_relay_{suffix}",
            f"{prefix} {name}",
            lambda d: getattr(d, f(field)),
            unit_,
            available=available,
            category="setting",
            device_class=dc,
            feature="dosing_settings",
            write=writer(field),
            minimum=minimum,
            maximum=maximum,
            step=step,
            number_mode="box",
            icon=icon,
        )

    in_use = lambda d: mode_of(d) is not RelayMode.DISABLED  # noqa: E731
    pwm = lambda d: mode_of(d) is RelayMode.PROPORTIONAL_PWM  # noqa: E731
    return (
        Value(
            f"{key}_relay_mode",
            f"{prefix} Dosing Mode",
            mode_of,
            category="setting",
            feature="dosing_settings",
            write=set_mode,
            options=tuple(mode.label for mode in RelayMode),
            icon="mdi:tune-variant",
            icons={
                "Proportional PWM": "mdi:chart-line-variant",
                "ON/OFF": "mdi:toggle-switch-outline",
                "Fixed PWM": "mdi:square-wave",
                "Disabled": "mdi:cancel",
            },
        ),
        number(
            "max",
            "Max Value",
            "val1",
            unit,
            *value_range,
            0.01,
            "mdi:gauge-full",
            in_use,
            device_class,
        ),
        number(
            "min",
            "Min Value",
            "val2",
            unit,
            *value_range,
            0.01,
            "mdi:gauge-empty",
            in_use,
            device_class,
        ),
        number("max_duty", "Max Duty Cycle", "perc1", "%", *DUTY_RANGE, 1, "mdi:speedometer", pwm),
        number(
            "min_duty", "Min Duty Cycle", "perc2", "%", *DUTY_RANGE, 1, "mdi:speedometer-slow", pwm
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
        "mg/L",
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
        "pH Relay State",
        lambda d: d.relay_ch1,
        device_class="enum",
        options=("On", "Off", "Disabled"),
        icons={"On": "mdi:pump", "Off": "mdi:pump-off", "Disabled": "mdi:pump-off"},
    ),
    Value(
        "relay_cl",
        "Cl Relay State",
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
    *_relay_values(1),
    *_relay_values(2),
    # Alarm and state coils; the live-confirmed ones first, then the user's notes.
    Value(
        "standby",
        "Standby",
        lambda d: d.standby,
        binary=True,
        feature="alarms",
        icon="mdi:pause-circle",
        icon_off="mdi:play-circle",
    ),
    Value(
        "no_flow",
        "No Flow",
        lambda d: d.no_flow,
        binary=True,
        device_class="problem",
        feature="alarms",
        icon="mdi:water-off",
        icon_off="mdi:water-check",
    ),
    Value(
        "cl_tank_empty",
        "Cl Tank Empty",
        lambda d: d.cl_tank_empty,
        binary=True,
        device_class="problem",
        feature="alarms",
        icon="mdi:cup-off-outline",
        icon_off="mdi:cup-outline",
    ),
    Value(
        "ph_tank_empty",
        "pH Tank Empty",
        lambda d: d.ph_tank_empty,
        binary=True,
        device_class="problem",
        feature="alarms",
        icon="mdi:cup-off-outline",
        icon_off="mdi:cup-outline",
    ),
    Value(
        "alarm_coils",
        "Alarm Coils",
        lambda d: d.active_alarm_coils,
        category="diagnostic",
        scan_group="fast",
        feature="alarms",
        icon="mdi:alert-circle-outline",
    ),
    Value(
        "feeding_delay",
        "Feeding Delay",
        lambda d: d.feeding_delay,
        "min",
        category="setting",
        device_class="duration",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "feeding_delay", v),
        minimum=0,
        maximum=60,
        step=1,
        number_mode="box",
        icon="mdi:timer-cog-outline",
    ),
    Value(
        "tau",
        "Tau",
        lambda d: d.tau,
        category="setting",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "tau", v),
        minimum=0,
        maximum=30,
        step=1,
        number_mode="box",
        icon="mdi:chart-bell-curve-cumulative",
    ),
    Value(
        "flow_time",
        "Flow Time",
        lambda d: d.flow_time,
        "min",
        category="setting",
        device_class="duration",
        feature="dosing_settings",
        write=lambda d, v: write_if_changed(d, "flow_time", v),
        minimum=0,
        maximum=99,
        step=1,
        number_mode="box",
        icon="mdi:timer-sand-complete",
    ),
    Value(
        "flow_mode",
        "Flow Mode",
        lambda d: d.flow_mode,
        category="setting",
        feature="dosing_settings",
        write=set_flow_mode,
        options=tuple(mode.label for mode in FlowMode),
        icon="mdi:waves-arrow-right",
        icons={
            "Disabled": "mdi:cancel",
            "Reverse": "mdi:swap-horizontal",
            "Direct": "mdi:waves-arrow-right",
        },
    ),
    Value(
        "priority",
        "Priority",
        _priority,
        category="setting",
        feature="dosing_settings",
        write=set_priority,
        options=PRIORITY_OPTIONS,
        icon="mdi:sort-variant",
        icons={"No priority": "mdi:equal", "pH priority": "mdi:sort-variant"},
    ),
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
        Feature("dosing_settings", "Dosing settings (pH and Cl pulse outputs, timing)"),
        Feature("alarms", "Alarms and standby (coils 1-16)"),
        Feature("probe_voltages", "Probe voltages"),
        Feature("clock", "Clock"),
    ),
    identify=_identify,
    default_unit_id=1,
    message_spacing=0.1,  # the protocol notes ask for at least 100 ms between requests
    check_fields=("ch1_divisor", "ch2_divisor"),
)
