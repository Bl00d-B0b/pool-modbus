"""EMEC LD-series pH / chlorine controllers, such as the LDPHCL (firmware 5.1.4).

The controller is byte-addressed: a value sits at odd wire address
``register - 40001``, and a read from an odd address returns every second
register. ``EmecUnit`` numbers the values 0, 1, 2, ... (``(register - 40002) / 2``)
and maps them to the wire. Register map and formats: docs/devices/emec_ld.md.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, time
from enum import IntEnum
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, PackedBitsField, bits, coil, enum, gauge, integer

from .base import DeviceType, Feature, ImplausibleReadError, Value
from .writing import read_with_retry, write_if_changed

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


COIL_READ = 8
"""Coils are read 8 at a time from a multiple of 8: the controller answers each
such block with its bits in reverse order (coil start + n is bit 7 - n), and a longer
read also shifts each byte's bits up by one and drops the top bit. With this rule every
coil sits at the address the protocol gives (alarm coils: its number - 1)."""
WRITABLE_COILS = frozenset({44, 48, 49, 50, 51, 52, 53, 58, 59, 60, 61, 62, 63, 75, 90})
"""The coils the integration writes; others are refused."""


class EmecUnit:
    """A ``ModbusUnit`` view that maps EMEC value indices to odd wire addresses.

    Coils pass straight through. Values are written one per request (function
    06); coils with function 05, only those in ``WRITABLE_COILS``. Multi-register
    and multi-coil writes are refused.
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
        """Coils by their protocol address, from reads of 8 (see ``COIL_READ``)."""
        wanted = range(address, address + count)
        blocks: dict[int, list[bool]] = {}
        for start in sorted({a - a % COIL_READ for a in wanted}):
            blocks[start] = await self._unit.read_coils(start, COIL_READ)
        return [blocks[a - a % COIL_READ][COIL_READ - 1 - a % COIL_READ] for a in wanted]

    async def write_register(self, address: int, value: int) -> None:
        await self._unit.write_register(wire_address(address), value)

    async def write_registers(self, address: int, values: list[int]) -> None:
        raise NotImplementedError("EMEC LD controllers are written one value per request")

    async def write_coil(self, address: int, value: bool) -> None:
        """Function 05, for the coils in ``WRITABLE_COILS`` only."""
        if address not in WRITABLE_COILS:
            raise NotImplementedError(f"coil {address} is not written by this integration")
        await self._unit.write_coil(address, value)

    async def write_coils(self, address: int, values: list[bool]) -> None:
        raise NotImplementedError("multi-coil writes are not supported on EMEC LD controllers")

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
    """Working mode of a switching relay output (pH relay 40092, Cl relay 40222)."""

    PROPORTIONAL_PWM = 0
    ON_OFF = 1
    FIXED_PWM = 2
    DISABLED = 3

    @property
    def label(self) -> str:
        return {0: "Proportional PWM", 1: "ON/OFF", 2: "Fixed PWM", 3: "Disabled"}[self.value]


DUTY_RANGE = (0, 100)
"""Percent of the 100 s PWM cycle the relay is on (the instruction manual: 100 % = 100 s)."""
PWM_TIME_RANGE = (0, 250)
"""Fixed PWM's Ton and Toff in seconds, as the keypad allows."""


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
MA_TEMP_RANGE = (0.0, 99.9)
"""°C at the mA output's ends, as the keypad allows."""
MA_MODES = ("0/20 mA", "4/20 mA")  # as the keypad writes them
LABEL_WORDS = 14
LABEL_PAD = "-"
"""What the controller keeps in the unused characters (an unset label reads all '-')."""
PIN_LENGTH = 4
PIN_PATTERN = "[0-9]{4}"
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


def _tenths(low: float, high: float):
    def check(value: Any) -> float:
        rounded = round(float(value), 1)
        if not low <= rounded <= high:
            raise ValueError(f"{value} is outside {low} to {high}")
        return rounded

    return check


def _hhmm(value: Any) -> int:
    """hour * 100 + minute, as the controller stores its log times."""
    if value != int(value) or not 0 <= value <= 2359 or value % 100 > 59:
        raise ValueError(f"{value} is not a time as hour * 100 + minute")
    return int(value)


def _hhmm_time(raw: int | None) -> time | None:
    if raw is None or raw > 2359 or raw % 100 > 59:
        return None
    return time(raw // 100, raw % 100)


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

    max_span = 125  # values per read, as EMEC's protocol description allows
    register_ranges = ((0, 919),)  # every value up to 41840 answers a read
    # the alarm block, always read whole, and the configuration coils
    # alarms; dosing alarm actions; out of range; priority; probe failure actions
    # (data logging 48, mA output modes 49-60 and out of range 51-63 as one range, so each
    # block of 8 is read once)
    coil_ranges = ((0, 13), (44, 44), (48, 63), (75, 75), (90, 90))

    # Dosing priority (coil 58, the menu's "Mode"): on = no priority, off = pH first.
    no_ph_priority = coil(58, writable=True)
    # Dosing alarm (the menu's "Dosing alarm"): a time per
    # channel, 0 = off, 1-999 min, and what to do when it runs out, 0 Dose / 1 Stop.
    dosing_alarm_ph_stop = coil(44, writable=True)
    dosing_alarm_cl_stop = coil(59, writable=True)
    # Probe failure (the menu's "Probe failure"): the same, with a time
    # of 0 = off or 100-999 min.
    probe_alarm_ph_stop = coil(75, writable=True)
    probe_alarm_cl_stop = coil(90, writable=True)
    # Out of range alarm (the menu's "Min/Max"): per channel a High and
    # a Low limit, each switched on by its coil, and a time and Dose/Stop shared by both.
    # As the protocol gives them for pH; for Cl it is one off: Low is coil 62 and Dose/Stop
    # coil 63 (it lists 63 and 64), and it lists no Cl time (at 40456). The controller
    # ignores these writes while the Min/Max menu is open on its keypad.
    range_ph_high_on = coil(51, writable=True)
    range_ph_low_on = coil(52, writable=True)
    range_ph_stop = coil(53, writable=True)
    range_cl_high_on = coil(61, writable=True)
    range_cl_low_on = coil(62, writable=True)
    range_cl_stop = coil(63, writable=True)
    # Alarm coils (function 01), numbered 01-14 as in the protocol, at address n - 1
    # (see COIL_READ): 01 pH tank (level 1) empty, 03 Cl tank empty, 04 no flow,
    # 14 standby; the others as the protocol names them.
    alarm_coil_01 = coil(0)  # Ch1 level1: pH tank 1 empty
    alarm_coil_02 = coil(1)  # Ch1 level2: pH tank 2 empty
    alarm_coil_03 = coil(2)  # Ch2 level: Cl tank empty
    alarm_coil_04 = coil(3)  # Flow
    alarm_coil_05 = coil(4)  # out of range alarm 1 (pH)
    alarm_coil_06 = coil(5)  # out of range alarm 2 (Cl)
    alarm_coil_07 = coil(6)  # dosing alarm ch2 (out IS)
    alarm_coil_08 = coil(7)  # dosing alarm ch2 (out relay)
    alarm_coil_09 = coil(8)  # dosing alarm ch1 (out IS1)
    alarm_coil_10 = coil(9)  # dosing alarm ch1 (out relay)
    alarm_coil_11 = coil(10)  # dosing alarm ch1 (out IS2)
    alarm_coil_12 = coil(11)  # probe fail ch1
    alarm_coil_13 = coil(12)  # probe fail ch2
    alarm_coil_14 = coil(13)  # standby

    # mA outputs (the menu's "mA Outputs", the protocol's "mA Setpoint"): per channel the
    # range, 0 = 0/20 mA / 1 = 4/20 mA, and the readings at
    # 20 mA (Max) and at 0 or 4 mA (Min). The protocol labels the temperature channel's
    # Max/Min (40128/40130) "ch2"; the keypad shows them as the temperature's.
    ma_ph_4_20 = coil(49, writable=True)
    # Data logging (the menu's "Log Setup"): on/off, the time logging starts and the
    # interval, both hour * 100 + minute.
    # The protocol's AM/PM coils 54/55 are for the USA format; this library reads the
    # controller in Europe format (24 h), as for its clock.
    log_on = coil(48, writable=True)
    # Label (the instrument name set in "RS485 Setup", 40844-40870): 28 characters, two
    # per value, chr0 in the low byte (the protocol's "chr0-chr1" ...; the display shows
    # 2 lines of 14), unused ones '-'. Read only: the keypad's name editor keeps its own
    # copy and writes it back on leaving, saved or not (see the device notes).
    for _i in range(LABEL_WORDS):
        locals()[f"label_{_i:02d}"] = integer(421 + _i, signed=False)
    del _i
    # Keypad password (40542): a 4-digit PIN, stored as the number (0042 reads 42).
    password = integer(270, signed=False, writable=_whole(0, 9999))
    log_start_raw = integer(264, signed=False, writable=_hhmm)  # 40530
    log_interval_raw = integer(265, signed=False, writable=_hhmm)  # 40532
    ma_cl_4_20 = coil(60, writable=True)
    ma_temp_4_20 = coil(50, writable=True)
    ma_ph_max = gauge(61, 0.01, writable=_hundredths(*PH_RANGE))  # 40124
    ma_ph_min = gauge(62, 0.01, writable=_hundredths(*PH_RANGE))  # 40126
    ma_cl_max = gauge(97, 0.01, writable=_hundredths(*CL_RANGE))  # 40196
    ma_cl_min = gauge(98, 0.01, writable=_hundredths(*CL_RANGE))  # 40198
    ma_temp_max = gauge(63, 0.1, writable=_tenths(*MA_TEMP_RANGE))  # 40128
    ma_temp_min = gauge(64, 0.1, writable=_tenths(*MA_TEMP_RANGE))  # 40130

    # Timing (40520-40526): the measurement filter (tau), the feeding delay before
    # dosing resumes after flow or power returns, the flow sensor's time and mode.
    tau = integer(259, writable=_whole(0, 30))
    feeding_delay = integer(260, unit="min", writable=_whole(0, 60))
    flow_time = integer(261, unit="min", writable=_whole(0, 99))
    dosing_alarm_ph_time = integer(213, unit="min", writable=_whole(0, 999))  # 40428
    dosing_alarm_cl_time = integer(214, unit="min", writable=_whole(0, 999))  # 40430
    probe_alarm_ph_time = integer(221, unit="min", writable=_whole(0, 999))  # 40444
    probe_alarm_cl_time = integer(222, unit="min", writable=_whole(0, 999))  # 40446
    range_ph_high = gauge(218, 0.01, writable=_hundredths(0.1, 14.0))  # 40438
    range_ph_time = integer(219, unit="min", writable=_whole(0, 99))  # 40440
    range_ph_low = gauge(220, 0.01, writable=_hundredths(0.0, 13.9))  # 40442
    range_cl_high = gauge(226, 0.01, writable=_hundredths(0.1, 10.0))  # 40454
    range_cl_time = integer(227, unit="min", writable=_whole(0, 99))  # 40456
    range_cl_low = gauge(228, 0.01, writable=_hundredths(0.0, 9.9))  # 40458
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
    # perc1/perc2: the duty cycles in Proportional PWM, Ton/Toff (0-250) in Fixed PWM.
    # The mode is at 40092, not the documented 40090 (which reads 0 in every mode):
    # 0 Proportional PWM, 1 ON/OFF, 2 Fixed PWM, 3 Off.
    ch1_relay_perc1 = integer(42, writable=_whole(*PWM_TIME_RANGE))
    ch1_relay_perc2 = integer(43, writable=_whole(*PWM_TIME_RANGE))
    ch1_relay_mode = enum(45, RelayMode, writable=RelayMode)
    ch2_relay_val1 = gauge(105, 0.01, writable=_hundredths(*CL_RANGE))
    ch2_relay_val2 = gauge(106, 0.01, writable=_hundredths(*CL_RANGE))
    # The Cl relay follows the same rules: mode at 40222, not 40220.
    ch2_relay_perc1 = integer(107, writable=_whole(*PWM_TIME_RANGE))
    ch2_relay_perc2 = integer(108, writable=_whole(*PWM_TIME_RANGE))
    ch2_relay_mode = enum(110, RelayMode, writable=RelayMode)

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
        return self.alarm_coil_04

    @property
    def cl_tank_empty(self) -> bool | None:
        return self.alarm_coil_03

    @property
    def ph_tank_empty(self) -> bool | None:
        return self.alarm_coil_01

    @property
    def standby(self) -> bool | None:
        """On while the controller is held in standby, e.g. no flow: outputs off."""
        return self.alarm_coil_14

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
    gives a 0 interval 1 minute; Proportional and Off zero the interval. Values already
    held are skipped."""
    f = lambda name: _field(channel, output, name)  # noqa: E731
    if mode is PulseMode.ON_OFF:
        interval = ((f("wait"), 1),) if not wait else ()
        return ((f("perc1"), 100), (f("perc2"), 0), *interval, (f("mode"), mode))
    return ((f("wait"), 0), (f("mode"), mode))  # Proportional and Off


async def set_dosing_mode(device: EmecLD, label: str, channel: int = 1, output: int = 1) -> None:
    """Switch a pulse output to ON/OFF, Proportional or Disabled."""
    mode = next((m for m in PulseMode if m.label == label), None)
    if mode is None:
        raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in PulseMode)}")
    await read_with_retry(device)
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
ALARM_ACTIONS = ("Dose", "Stop")


def _alarm_values(
    alarm: str, name: str, symbol: str, key: str, shortest: int, icon: str
) -> tuple[Value, ...]:
    """A channel's dosing alarm or probe failure: its time (0 = off, else ``shortest``-999
    min) and, while on, Dose or Stop. The keypad offers Dose/Stop only while a time is set,
    and Off keeps the choice."""
    time_field, stop_field = f"{alarm}_alarm_{key}_time", f"{alarm}_alarm_{key}_stop"

    async def set_time(device: EmecLD, minutes: float) -> None:
        if minutes and not shortest <= minutes <= 999:
            raise ValueError(f"{minutes:g} min: 0 (off) or {shortest}-999")
        await write_if_changed(device, time_field, minutes)

    def action(device: EmecLD) -> str | None:
        stop = getattr(device, stop_field)
        return None if stop is None else ALARM_ACTIONS[int(stop)]

    async def set_action(device: EmecLD, label: str) -> None:
        if label not in ALARM_ACTIONS:
            raise ValueError(f"{label!r} is not one of {', '.join(ALARM_ACTIONS)}")
        await write_if_changed(device, stop_field, label == "Stop")

    return (
        Value(
            f"{key}_{alarm}_alarm_time",
            f"{symbol} {name} Time",
            lambda d: getattr(d, time_field),
            "min",
            category="setting",
            device_class="duration",
            feature="dosing_settings",
            write=set_time,
            minimum=0,
            maximum=999,
            step=1,
            number_mode="box",
            icon=icon,
        ),
        Value(
            f"{key}_{alarm}_alarm_action",
            f"{symbol} {name} Action",
            action,
            available=lambda d: bool(getattr(d, time_field)),
            category="setting",
            feature="dosing_settings",
            write=set_action,
            options=ALARM_ACTIONS,
            icon="mdi:alert-octagon-outline",
            icons={"Dose": "mdi:play-circle-outline", "Stop": "mdi:stop-circle-outline"},
        ),
    )


def _range_values(channel: int) -> tuple[Value, ...]:
    """A channel's out of range alarm: High and Low switches with their limits, and while
    either is on the time and Dose/Stop, as the keypad shows them."""
    symbol, key, unit, device_class, _ = _channel(channel)
    high_range, low_range = (
        ((0.1, 14.0), (0.0, 13.9)) if channel == 1 else ((0.1, 10.0), (0.0, 9.9))
    )
    f = lambda name: f"range_{key}_{name}"  # noqa: E731
    high_on = lambda d: getattr(d, f("high_on")) is True  # noqa: E731
    low_on = lambda d: getattr(d, f("low_on")) is True  # noqa: E731
    either = lambda d: high_on(d) or low_on(d)  # noqa: E731
    writer = lambda name: lambda d, v: write_if_changed(d, f(name), v)  # noqa: E731

    def action(device: EmecLD) -> str | None:
        stop = getattr(device, f("stop"))
        return None if stop is None else ALARM_ACTIONS[int(stop)]

    async def set_action(device: EmecLD, label: str) -> None:
        if label not in ALARM_ACTIONS:
            raise ValueError(f"{label!r} is not one of {', '.join(ALARM_ACTIONS)}")
        await write_if_changed(device, f("stop"), label == "Stop")

    def switch(side: str, icon: str) -> Value:
        return Value(
            f"{key}_range_{side}",
            f"{symbol} Range {side.capitalize()}",
            lambda d: getattr(d, f(f"{side}_on")),
            binary=True,
            category="setting",
            feature="dosing_settings",
            write=writer(f"{side}_on"),
            icon=icon,
        )

    def limit(side: str, bounds: tuple[float, float], available, icon: str) -> Value:
        return Value(
            f"{key}_range_{side}_value",
            f"{symbol} Range {side.capitalize()} Value",
            lambda d: getattr(d, f(side)),
            unit,
            available=available,
            category="setting",
            device_class=device_class,
            feature="dosing_settings",
            write=writer(side),
            minimum=bounds[0],
            maximum=bounds[1],
            step=0.01,
            number_mode="box",
            icon=icon,
        )

    return (
        switch("high", "mdi:arrow-collapse-up"),
        limit("high", high_range, high_on, "mdi:arrow-collapse-up"),
        switch("low", "mdi:arrow-collapse-down"),
        limit("low", low_range, low_on, "mdi:arrow-collapse-down"),
        Value(
            f"{key}_range_time",
            f"{symbol} Range Time",
            lambda d: getattr(d, f("time")),
            "min",
            available=either,
            category="setting",
            device_class="duration",
            feature="dosing_settings",
            write=writer("time"),
            minimum=0,
            maximum=99,
            step=1,
            number_mode="box",
            icon="mdi:timer-outline",
        ),
        Value(
            f"{key}_range_action",
            f"{symbol} Range Action",
            action,
            available=either,
            category="setting",
            feature="dosing_settings",
            write=set_action,
            options=ALARM_ACTIONS,
            icon="mdi:alert-octagon-outline",
            icons={"Dose": "mdi:play-circle-outline", "Stop": "mdi:stop-circle-outline"},
        ),
    )


def _ma_values(key: str, symbol: str) -> tuple[Value, ...]:
    """One mA output: its range and the readings at its ends, as the keypad shows them."""
    if key == "temp":
        unit, device_class, bounds, step = "°C", "temperature", MA_TEMP_RANGE, 0.1
    else:
        _, _, unit, device_class, bounds = _channel(1 if key == "ph" else 2)
        step = 0.01
    f = lambda name: f"ma_{key}_{name}"  # noqa: E731

    def mode(device: EmecLD) -> str | None:
        on = getattr(device, f("4_20"))
        return None if on is None else MA_MODES[int(on)]

    async def set_mode(device: EmecLD, label: str) -> None:
        if label not in MA_MODES:
            raise ValueError(f"{label!r} is not one of {', '.join(MA_MODES)}")
        await write_if_changed(device, f("4_20"), label == MA_MODES[1])

    def end(side: str, icon: str) -> Value:
        return Value(
            f"{key}_ma_{side}",
            f"{symbol} mA Output {side.capitalize()}",
            lambda d: getattr(d, f(side)),
            unit,
            category="setting",
            device_class=device_class,
            feature="ma_outputs",
            write=lambda d, v: write_if_changed(d, f(side), v),
            minimum=bounds[0],
            maximum=bounds[1],
            step=step,
            number_mode="box",
            icon=icon,
        )

    return (
        Value(
            f"{key}_ma_mode",
            f"{symbol} mA Output Mode",
            mode,
            category="setting",
            feature="ma_outputs",
            write=set_mode,
            options=MA_MODES,
            icon="mdi:current-dc",
        ),
        end("max", "mdi:arrow-collapse-up"),
        end("min", "mdi:arrow-collapse-down"),
    )


def _log_time(field: str):
    async def write(device: EmecLD, value: time | str) -> None:
        try:
            at = time.fromisoformat(value) if isinstance(value, str) else value
        except ValueError:
            at = None
        if not isinstance(at, time):
            raise ValueError(f"{value!r} is not a time like 08:15")
        await write_if_changed(device, field, at.hour * 100 + at.minute)

    return write


_LOG_VALUES = (
    Value(
        "data_logging",
        "Logging Data",  # sorts next to the two times
        lambda d: d.log_on,
        binary=True,
        category="setting",
        feature="data_logging",
        write=lambda d, on: write_if_changed(d, "log_on", bool(on)),
        icon="mdi:database-clock",
        icon_off="mdi:database-off-outline",
    ),
    Value(
        "log_start_time",
        "Log Start Time",
        lambda d: _hhmm_time(d.log_start_raw),
        available=lambda d: d.log_on is True,  # only while logging is on
        category="setting",
        feature="data_logging",
        write=_log_time("log_start_raw"),
        time_of_day=True,
        icon="mdi:clock-start",
    ),
    Value(
        "log_interval",
        "Log Interval",
        lambda d: _hhmm_time(d.log_interval_raw),
        available=lambda d: d.log_on is True,
        category="setting",
        feature="data_logging",
        write=_log_time("log_interval_raw"),
        time_of_day=True,
        icon="mdi:timer-sync-outline",
    ),
)


def _label(device: EmecLD) -> str | None:
    """The label without the '-' that fill its unused characters."""
    words = [getattr(device, f"label_{i:02d}") for i in range(LABEL_WORDS)]
    if None in words:
        return None
    text = "".join(chr(w & 0xFF) + chr(w >> 8) for w in words)  # chr0 in the low byte
    return text.rstrip(LABEL_PAD)


def _pin(device: EmecLD) -> str | None:
    pin = device.password
    return None if pin is None or pin > 9999 else f"{pin:0{PIN_LENGTH}d}"


async def set_pin(device: EmecLD, text: str) -> None:
    """Write the keypad PIN, four digits "0000"-"9999"."""
    if not re.fullmatch(PIN_PATTERN, text):
        raise ValueError(f"{text!r} is not a PIN of {PIN_LENGTH} digits")
    await write_if_changed(device, "password", int(text))


_LABEL_VALUES = (
    Value(
        "instrument_label",
        "Label",
        _label,
        category="diagnostic",
        feature="label",
        icon="mdi:label-outline",
    ),
    Value(
        "keypad_pin",
        "PIN",
        _pin,
        category="diagnostic",  # the user's choice, though writable
        feature="label",
        write=set_pin,
        text_pattern=PIN_PATTERN,
        minimum=PIN_LENGTH,
        maximum=PIN_LENGTH,
        enabled_by_default=False,
        secret=True,
        icon="mdi:form-textbox-password",
    ),
)


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
    if mode is FlowMode.DISABLED:
        # as the keypad does: Disabled sets the flow time to 0
        await write_if_changed(device, "flow_time", 0)
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
            category="control",
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
            f"{prefix} On Value",
            lambda d: getattr(d, f("val1")),
            unit,
            available=lambda d: mode_of(d) is not PulseMode.DISABLED,
            category="control",
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
            f"{prefix} Off Value",
            lambda d: getattr(d, f("val2")),
            unit,
            available=lambda d: mode_of(d) is not PulseMode.DISABLED,
            category="control",
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
            f"{prefix} On Rate",
            lambda d: getattr(d, f("perc1")),
            "p/min",
            available=lambda d: mode_of(d) is PulseMode.PROPORTIONAL,
            category="control",
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
            f"{prefix} Off Rate",
            lambda d: getattr(d, f("perc2")),
            "p/min",
            available=lambda d: mode_of(d) is PulseMode.PROPORTIONAL,
            category="control",
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
            category="control",
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
    mode. Its on/off state is the "... Relay State" sensor (40032, 40024).

    Fixed PWM's Ton and Toff sit in the same registers as the duty cycles. A duty cycle
    is written as the keypad keeps them (one of the two at a time, the other set to 0),
    and a switch to ON/OFF or Disabled sets perc1 to 100 and perc2 to 0 first, as the
    keypad does."""
    symbol, key, unit, device_class, value_range = _channel(channel)
    prefix = f"{symbol} Relay"
    f = lambda name: f"ch{channel}_relay_{name}"  # noqa: E731
    mode_of = lambda d: getattr(d, f("mode"))  # noqa: E731
    writer = lambda name: lambda d, v: write_if_changed(d, f(name), v)  # noqa: E731

    async def set_mode(device: EmecLD, label: str) -> None:
        mode = next((m for m in RelayMode if m.label == label), None)
        if mode is None:
            raise ValueError(f"{label!r} is not one of {', '.join(m.label for m in RelayMode)}")
        if mode in (RelayMode.ON_OFF, RelayMode.DISABLED):
            # as the keypad does on a switch to ON/OFF or Disabled
            await write_if_changed(device, f("perc1"), 100)
            await write_if_changed(device, f("perc2"), 0)
        await write_if_changed(device, f("mode"), mode)

    def number(suffix, name, field, unit_, minimum, maximum, step, icon, available, dc=None):
        return Value(
            f"{key}_relay_{suffix}",
            f"{prefix} {name}",
            lambda d: getattr(d, f(field)),
            unit_,
            available=available,
            category="control",
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
    fixed = lambda d: mode_of(d) is RelayMode.FIXED_PWM  # noqa: E731

    def duty(own: str, other: str):
        async def write(device: EmecLD, value: float) -> None:
            if not 0 <= value <= DUTY_RANGE[1]:
                raise ValueError(f"{value} is not within {DUTY_RANGE[0]}-{DUTY_RANGE[1]} %")
            await write_if_changed(device, f(own), value)
            if value:
                await write_if_changed(device, f(other), 0)

        return write

    # The instruction manual's Ton ("ON period, during output activity") and Toff
    # ("OFF period"), in seconds.
    times = (
        number(
            "ton",
            "On Period",
            "perc1",
            "s",
            *PWM_TIME_RANGE,
            1,
            "mdi:timer-play-outline",
            fixed,
            "duration",
        ),
        number(
            "toff",
            "Off Period",
            "perc2",
            "s",
            *PWM_TIME_RANGE,
            1,
            "mdi:timer-pause-outline",
            fixed,
            "duration",
        ),
    )
    return (
        Value(
            f"{key}_relay_mode",
            f"{prefix} Dosing Mode",
            mode_of,
            category="control",
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
        # val1 is where the relay switches on, val2 where it switches off (the keypad's
        # ON/OFF rows, e.g. pH 6.55 on / 6.50 off, Cl 2.45 on / 2.50 off).
        number(
            "on",
            "On Value",
            "val1",
            unit,
            *value_range,
            0.01,
            "mdi:toggle-switch-outline",
            in_use,
            device_class,
        ),
        number(
            "off",
            "Off Value",
            "val2",
            unit,
            *value_range,
            0.01,
            "mdi:toggle-switch-off-outline",
            in_use,
            device_class,
        ),
        replace(
            number(
                "max_duty", "Max Duty Cycle", "perc1", "%", *DUTY_RANGE, 1, "mdi:speedometer", pwm
            ),
            write=duty("perc1", "perc2"),
        ),
        replace(
            number(
                "min_duty",
                "Min Duty Cycle",
                "perc2",
                "%",
                *DUTY_RANGE,
                1,
                "mdi:speedometer-slow",
                pwm,
            ),
            write=duty("perc2", "perc1"),
        ),
        *times,
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
        category="measurement",
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
        category="measurement",
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
        enabled_by_default=False,
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
        category="diagnostic",
        scan_group="fast",  # read with the states
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
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:cup-off-outline",
        icon_off="mdi:cup-outline",
    ),
    Value(
        "ph_tank_1_empty",
        "pH Tank 1 Empty",
        lambda d: d.ph_tank_empty,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:cup-off-outline",
        icon_off="mdi:cup-outline",
    ),
    # The protocol's other alarm coils (see the alarm coil fields).
    Value(  # protocol coil 02 Ch1 level2
        "ph_tank_2_empty",
        "pH Tank 2 Empty",
        lambda d: d.alarm_coil_02,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:cup-off-outline",
        icon_off="mdi:cup-outline",
    ),
    Value(  # protocol coil 05 OUT OF RANGE ALARM1
        "ph_out_of_range",
        "pH Out of Range",
        lambda d: d.alarm_coil_05,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:alert-circle-outline",
        icon_off="mdi:check-circle-outline",
    ),
    Value(  # protocol coil 06 OUT OF RANGE ALARM2
        "cl_out_of_range",
        "Cl Out of Range",
        lambda d: d.alarm_coil_06,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:alert-circle-outline",
        icon_off="mdi:check-circle-outline",
    ),
    Value(  # protocol coil 07 Dosing Alarm ch2 (out IS)
        "cl_pulse_dosing_alarm",
        "Cl Pulse Dosing Alarm",
        lambda d: d.alarm_coil_07,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:timer-alert-outline",
        icon_off="mdi:timer-check-outline",
    ),
    Value(  # protocol coil 08 Dosing Alarm ch2 (out relè)
        "cl_relay_dosing_alarm",
        "Cl Relay Dosing Alarm",
        lambda d: d.alarm_coil_08,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:timer-alert-outline",
        icon_off="mdi:timer-check-outline",
    ),
    Value(  # protocol coil 09 Dosing Alarm ch1 (out IS1)
        "ph_pulse_1_dosing_alarm",
        "pH Pulse 1 Dosing Alarm",
        lambda d: d.alarm_coil_09,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:timer-alert-outline",
        icon_off="mdi:timer-check-outline",
    ),
    Value(  # protocol coil 10 Dosing Alarm ch1 (out relè)
        "ph_relay_dosing_alarm",
        "pH Relay Dosing Alarm",
        lambda d: d.alarm_coil_10,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:timer-alert-outline",
        icon_off="mdi:timer-check-outline",
    ),
    Value(  # protocol coil 11 Dosing Alarm ch1 (out IS2)
        "ph_pulse_2_dosing_alarm",
        "pH Pulse 2 Dosing Alarm",
        lambda d: d.alarm_coil_11,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:timer-alert-outline",
        icon_off="mdi:timer-check-outline",
    ),
    Value(  # protocol coil 12 Probe fail ch1
        "ph_probe_fail",
        "pH Probe Fail",
        lambda d: d.alarm_coil_12,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:flask-off-outline",
        icon_off="mdi:flask-outline",
    ),
    Value(  # protocol coil 13 Probe Fail ch2
        "cl_probe_fail",
        "Cl Probe Fail",
        lambda d: d.alarm_coil_13,
        binary=True,
        category="diagnostic",
        scan_group="fast",  # read with the states
        device_class="problem",
        feature="alarms",
        icon="mdi:flask-off-outline",
        icon_off="mdi:flask-outline",
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
        # the keypad shows the time only for Reverse and Direct
        available=lambda d: d.flow_mode not in (None, FlowMode.DISABLED),
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
    *_alarm_values("dosing", "Dosing Alarm", "pH", "ph", 1, "mdi:timer-alert-outline"),
    *_alarm_values("dosing", "Dosing Alarm", "Cl", "cl", 1, "mdi:timer-alert-outline"),
    *_alarm_values("probe", "Probe Failure", "pH", "ph", 100, "mdi:flask-off-outline"),
    *_range_values(1),
    *_range_values(2),
    *_alarm_values("probe", "Probe Failure", "Cl", "cl", 100, "mdi:flask-off-outline"),
    *_ma_values("ph", "pH"),
    *_ma_values("cl", "Cl"),
    *_ma_values("temp", "Temperature"),
    *_LOG_VALUES,
    *_LABEL_VALUES,
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
        Feature("alarms", "Alarms and standby"),
        Feature("ma_outputs", "mA outputs"),
        Feature("data_logging", "Data logging"),
        Feature("label", "Label and PIN"),
        Feature("probe_voltages", "Probe voltages"),
        Feature("clock", "Clock"),
    ),
    identify=_identify,
    default_unit_id=1,
    message_spacing=0.1,  # the protocol notes ask for at least 100 ms between requests
    check_fields=("ch1_divisor", "ch2_divisor"),
    private_addresses=(wire_address(270),),  # the keypad PIN (40542)
    address_step=2,
)
