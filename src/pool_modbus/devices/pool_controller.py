"""Pool controller: filtration, cover, light and water level. Standard addressing.

Register map from the controller's register sheet, checked against live data;
see docs/devices/pool_controller.md.

Writes. Register 16 holds two switches (filtration, block filling up) and the
command bits: a command is a pulse, the bit set, held, then cleared. The cover
and the backwash need more than 3 s, the light, alarm reset and schedule save
about 0.5 s. Every write to register 16 reads it back and merges, so the other
bits stay. A backwash schedule written to 17-19 only counts after the "save
backwash timers" pulse; it is confirmed in 27-29, where the controller keeps it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, bit, bits, integer

from .base import Action, Cover, DeviceType, Feature, Value
from .writing import pulse, wait_until, write_if_changed

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_OPTIONS = ("Off", *DAYS)
TIME_OPTIONS = tuple(f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in range(0, 60, 5))

LONG_PULSE = 3.1
"""Seconds to hold the cover and backwash commands; the controller needs over 3 s."""

SHORT_PULSE = 0.5
"""Seconds to hold the light, alarm reset and schedule save commands."""


def _within(low: int, high: int):
    def check(value: Any) -> int:
        if value != int(value) or not low <= value <= high:
            raise ValueError(f"{value} is not a whole number from {low} to {high}")
        return int(value)

    return check


class PoolController(Component):
    """Pool controller. Addresses are the on-wire register numbers."""

    max_span = 24
    register_ranges = ((16, 39),)

    # 16: switches, which the controller keeps, and command pulses.
    filtration_enabled = bit(16, 0, writable=True)
    backwash_command = bit(16, 1, writable=True)
    open_command = bit(16, 2, writable=True)
    close_command = bit(16, 3, writable=True)
    light_command = bit(16, 4, writable=True)  # toggles the light
    filling_blocked = bit(16, 5, writable=True)
    reset_alarms_command = bit(16, 14, writable=True)
    save_backwash_command = bit(16, 15, writable=True)

    # 17-19: backwash schedule as last written. The controller only keeps it
    # after the "save backwash timers" pulse (register 16, bit 15).
    backwash_days_written = integer(17, signed=False, writable=_within(0, 127))  # bit 0 Monday
    backwash_hour_written = integer(18, signed=False, writable=_within(0, 23))
    backwash_minute_written = integer(19, signed=False, writable=_within(0, 59))

    # 24: status
    pump_running = bit(24, 0)
    filtering = bit(24, 1)
    backwashing = bit(24, 2)
    pool_open = bit(24, 3)
    filling_up = bit(24, 4)
    room_flooding_alarm = bit(24, 5)
    water_level_min = bit(24, 6)
    water_level_low = bit(24, 7)
    water_level_normal = bit(24, 8)
    water_level_max = bit(24, 9)
    water_level_monitoring_off = bit(24, 10)
    light_on = bit(24, 11)

    # 25-26: controller time
    plc_hour = integer(25, signed=False)
    plc_minute = integer(26, signed=False)

    # 27-29: backwash schedule as saved by the controller
    backwash_days = integer(27, signed=False)
    backwash_hour = integer(28, signed=False)
    backwash_minute = integer(29, signed=False)

    # 36-39: clock. Registers 32-35 take the time to set, in another layout.
    clock_second = bits(36, 8, 8)
    clock_minute = bits(36, 0, 8)
    clock_hour = bits(37, 8, 8)
    clock_weekday = bits(37, 0, 8)  # 1 = Monday
    clock_day = bits(38, 8, 8)
    clock_month = bits(38, 0, 8)
    clock_year_low = bits(39, 8, 8)
    clock_century = bits(39, 0, 8)

    @property
    def filtration_mode(self) -> str | None:
        if self.backwashing is None or self.filtering is None:
            return None
        if self.backwashing:
            return "Backwashing"
        return "Filtering" if self.filtering else "Off"

    @property
    def water_level(self) -> str | None:
        """Water level as the controller reports it; monitoring off wins."""
        flags = (
            (self.water_level_monitoring_off, "Off"),
            (self.water_level_min, "Minimum"),
            (self.water_level_low, "Low"),
            (self.water_level_normal, "Normal"),
            (self.water_level_max, "Maximum"),
        )
        if any(flag is None for flag, _ in flags):
            return None
        return next((label for flag, label in flags if flag), "Unknown")

    @property
    def water_level_monitoring(self) -> bool | None:
        off = self.water_level_monitoring_off
        return None if off is None else not off

    @property
    def cover_closed(self) -> bool | None:
        return None if self.pool_open is None else not self.pool_open

    @property
    def backwash_day_written(self) -> str | None:
        return _day(self.backwash_days_written)

    @property
    def backwash_time_written(self) -> str | None:
        return _time(self.backwash_hour_written, self.backwash_minute_written)

    @property
    def saved_backwash_schedule(self) -> str | None:
        """The schedule the controller keeps, e.g. "Friday 06:00", or "Off"."""
        day = _day(self.backwash_days)
        time = _time(self.backwash_hour, self.backwash_minute)
        if day is None or time is None:
            return None
        return day if day == "Off" else f"{day} {time}"

    @property
    def clock(self) -> datetime | None:
        """The controller's clock, without time zone; None if unread or invalid."""
        parts = (
            self.clock_century,
            self.clock_year_low,
            self.clock_month,
            self.clock_day,
            self.clock_hour,
            self.clock_minute,
            self.clock_second,
        )
        if None in parts:
            return None
        century, year, month, day, hour, minute, second = parts
        try:
            return datetime(century * 100 + year, month, day, hour, minute, second)
        except ValueError:
            return None


def _day(bitmap: int | None) -> str | None:
    """The first day set in a Monday-first bitmap, or "Off"."""
    if bitmap is None:
        return None
    return next((name for index, name in enumerate(DAYS) if bitmap & (1 << index)), "Off")


def _time(hour: int | None, minute: int | None) -> str | None:
    if hour is None or minute is None:
        return None
    return f"{hour:02d}:{minute:02d}"


# -- writes -------------------------------------------------------------------


async def set_light(device: PoolController, on: bool) -> None:
    """Toggle the light if it is not already as asked; confirmed in the status."""
    await device.async_update()
    if device.light_on == bool(on):
        return
    if not device.pool_open:
        raise ValueError("the controller only switches the light while the cover is open")
    await pulse(device, "light_command", SHORT_PULSE)
    await wait_until(
        device, lambda d: d.light_on == bool(on), f"the light is still {'off' if on else 'on'}"
    )


async def _move_cover(device: PoolController, command: str, want_open: bool) -> None:
    """Pulse the open or close command unless the cover already is there. The cover
    takes a while to move, so the status is not waited for."""
    await device.async_update()
    if device.pool_open == want_open:
        return
    await pulse(device, command, LONG_PULSE)


async def open_cover(device: PoolController) -> None:
    await _move_cover(device, "open_command", True)


async def close_cover(device: PoolController) -> None:
    await _move_cover(device, "close_command", False)


async def _save_schedule(device: PoolController, done: Any, what: str) -> None:
    """The save pulse, then wait until the controller keeps the schedule."""
    await pulse(device, "save_backwash_command", SHORT_PULSE)
    await wait_until(device, done, f"the controller has not saved {what}")


async def set_backwash_day(device: PoolController, day: str) -> None:
    if day not in DAY_OPTIONS:
        raise ValueError(f"{day!r} is not one of {', '.join(DAY_OPTIONS)}")
    bitmap = 0 if day == "Off" else 1 << DAYS.index(day)
    await device.async_update()
    if device.backwash_days_written == bitmap and device.backwash_days == bitmap:
        return
    await device.write("backwash_days_written", bitmap)
    await _save_schedule(device, lambda d: d.backwash_days == bitmap, f"the backwash day {day}")


async def set_backwash_time(device: PoolController, time: str) -> None:
    if time not in TIME_OPTIONS:
        raise ValueError(f"{time!r} is not a time of day in 5 minute steps, like 06:00")
    hour, minute = (int(part) for part in time.split(":"))
    await device.async_update()
    written = (device.backwash_hour_written, device.backwash_minute_written)
    saved = (device.backwash_hour, device.backwash_minute)
    if written == saved == (hour, minute):
        return
    # One request for both, as the controller's own tools write them.
    await device.modbus_unit.write_registers(18, [hour, minute])
    await _save_schedule(
        device,
        lambda d: (d.backwash_hour, d.backwash_minute) == (hour, minute),
        f"the backwash time {time}",
    )


async def backwash(device: PoolController, now: datetime) -> None:
    await pulse(device, "backwash_command", LONG_PULSE)


async def reset_alarms(device: PoolController, now: datetime) -> None:
    await pulse(device, "reset_alarms_command", SHORT_PULSE)


async def sync_clock(device: PoolController, now: datetime) -> None:
    """Set the controller's clock to ``now`` (local time), in the layout of 32-35,
    then wait until its clock (36-39) reads within a few seconds of it."""
    words = [
        now.second << 8 | now.isoweekday(),  # weekday 1 = Monday
        now.hour << 8 | now.minute,
        now.month << 8 | now.day,
        (now.year // 100) << 8 | now.year % 100,
    ]
    await device.modbus_unit.write_registers(32, words)
    local = now.replace(tzinfo=None)
    await wait_until(
        device,
        lambda d: d.clock is not None and abs((d.clock - local).total_seconds()) < 15,
        "the controller's clock is not set",
    )


# -- values -------------------------------------------------------------------

VALUES = (
    Value(
        "filtration_enabled",
        "Filtration",
        lambda d: d.filtration_enabled,
        binary=True,
        device_class="switch",
        write=lambda d, on: write_if_changed(d, "filtration_enabled", bool(on)),
        icon="mdi:air-filter",
        icon_off="mdi:water-pump-off",
    ),
    Value(
        "filling_blocked",
        "Block Filling",
        lambda d: d.filling_blocked,
        binary=True,
        device_class="switch",
        write=lambda d, on: write_if_changed(d, "filling_blocked", bool(on)),
        icon="mdi:water-off",
        icon_off="mdi:water-plus-outline",
    ),
    Value(
        "filtration_mode",
        "Filtration Mode",
        lambda d: d.filtration_mode,
        device_class="enum",
        icons={
            "Backwashing": "mdi:rotate-left",
            "Filtering": "mdi:air-filter",
            "Off": "mdi:water-pump-off",
        },
        options=("Backwashing", "Filtering", "Off"),
    ),
    Value(
        "pump_running",
        "Filter Pump",
        lambda d: d.pump_running,
        binary=True,
        device_class="running",
        icon="mdi:water-pump",
        icon_off="mdi:water-pump-off",
    ),
    Value(
        "water_level",
        "Water Level",
        lambda d: d.water_level,
        device_class="enum",
        icons={
            "Off": "mdi:water-off",
            "Minimum": "mdi:water-alert",
            "Low": "mdi:water-minus",
            "Normal": "mdi:water-check",
            "Maximum": "mdi:water-plus",
            "Unknown": "mdi:water-off",
        },
        options=("Off", "Minimum", "Low", "Normal", "Maximum", "Unknown"),
    ),
    Value(
        "water_level_monitoring",
        "Water Level Monitoring",
        lambda d: d.water_level_monitoring,
        binary=True,
        icon="mdi:water",
        icon_off="mdi:water-off",
    ),
    Value(
        "filling_up",
        "Filling",
        lambda d: d.filling_up,
        binary=True,
        device_class="running",
        icon="mdi:water-plus",
        icon_off="mdi:water",
    ),
    Value(
        "light_on",
        "Light",
        lambda d: d.light_on,
        binary=True,
        write=set_light,
        light=True,
        fields=("light_command", "pool_open"),
        icon="mdi:lightbulb-on",
        icon_off="mdi:lightbulb-off",
    ),
    Value(
        "room_flooding_alarm",
        "Flooding Alarm",
        lambda d: d.room_flooding_alarm,
        binary=True,
        device_class="moisture",
        icon="mdi:home-flood",
        icon_off="mdi:home",
    ),
    Value(
        "backwash_day",
        "Backwash Day",
        lambda d: d.backwash_day_written,
        category="setting",
        feature="backwash_schedule",
        write=set_backwash_day,
        options=DAY_OPTIONS,
        fields=("backwash_days", "save_backwash_command"),
        icon="mdi:calendar-clock",
    ),
    Value(
        "backwash_time",
        "Backwash Time",
        lambda d: d.backwash_time_written,
        category="setting",
        feature="backwash_schedule",
        write=set_backwash_time,
        options=TIME_OPTIONS,
        fields=("backwash_hour", "backwash_minute", "save_backwash_command"),
        icon="mdi:clock-edit",
    ),
    Value(
        "saved_backwash_schedule",
        "Saved Backwash Schedule",
        lambda d: d.saved_backwash_schedule,
        category="setting",
        feature="backwash_schedule",
        icon="mdi:calendar-check",
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
)

ACTIONS = (
    Action(
        "backwash",
        "Start Backwash",
        backwash,
        fields=("backwash_command",),
        icon="mdi:rotate-left",
    ),
    Action(
        "reset_alarms",
        "Reset Alarms",
        reset_alarms,
        fields=("reset_alarms_command",),
        icon="mdi:restore-alert",
    ),
    Action(
        "sync_rtc",
        "Sync RTC",
        sync_clock,
        category="diagnostic",
        scan_group="slow",
        fields=(
            "clock_second",
            "clock_minute",
            "clock_hour",
            "clock_day",
            "clock_month",
            "clock_year_low",
            "clock_century",
        ),
        feature="clock",
        icon="mdi:home-clock",
    ),
)

COVER = Cover(
    "cover",
    "Cover",
    lambda d: d.cover_closed,
    open_cover,
    close_cover,
    device_class="gate",
    fields=("open_command", "close_command"),
    icon="mdi:pool",
    icon_closed="mdi:gate",
)


def _create(unit: ModbusUnit, variant: str | None) -> PoolController:
    return PoolController(unit)


DEVICE_TYPE = DeviceType(
    key="pool_controller",
    name="Pool controller",
    manufacturer="Unknown",
    models=("Pool controller",),
    create=_create,
    values=VALUES,
    cover=COVER,
    actions=ACTIONS,
    features=(
        Feature("backwash_schedule", "Backwash schedule"),
        Feature("clock", "Clock"),
    ),
    default_unit_id=1,
)
