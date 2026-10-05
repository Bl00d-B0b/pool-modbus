"""Pool controller: filtration, cover, light and water level. Standard addressing.

Register map from the controller's register sheet, checked against live data;
see docs/devices/pool_controller.md.
"""

from __future__ import annotations

from datetime import datetime

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, bit, bits, integer

from .base import DeviceType, Feature, Value

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class PoolController(Component):
    """Pool controller. Addresses are the on-wire register numbers."""

    max_span = 24
    register_ranges = ((16, 39),)

    # 16: switches and command pulses; the device keeps the switch bits.
    filtration_enabled = bit(16, 0)
    filling_blocked = bit(16, 5)

    # 17-19: backwash schedule as last written. The controller only keeps it
    # after the "save backwash timers" pulse (register 16, bit 15).
    backwash_days_written = integer(17, signed=False)  # bit 0 Monday ... bit 6 Sunday
    backwash_hour_written = integer(18, signed=False)
    backwash_minute_written = integer(19, signed=False)

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

    # 36-39: clock. Registers 32-35 hold the same time in the layout used to set it.
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
    def cover(self) -> str | None:
        return None if self.pool_open is None else ("open" if self.pool_open else "closed")

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


VALUES = (
    Value("filtration_enabled", "Pool Filtration", lambda d: d.filtration_enabled, binary=True),
    Value("filling_blocked", "Block Filling Up", lambda d: d.filling_blocked, binary=True),
    Value("filtration_mode", "Filtration Mode", lambda d: d.filtration_mode),
    Value(
        "pump_running",
        "Filter Pump Running",
        lambda d: d.pump_running,
        binary=True,
        device_class="running",
    ),
    Value("water_level", "Water Level", lambda d: d.water_level),
    Value(
        "water_level_monitoring",
        "Water Level Monitoring",
        lambda d: d.water_level_monitoring,
        binary=True,
    ),
    Value(
        "filling_up",
        "Pool Filling Up",
        lambda d: d.filling_up,
        binary=True,
        device_class="running",
    ),
    Value("cover", "Pool Cover", lambda d: d.cover),
    Value("light_on", "Pool Light", lambda d: d.light_on, binary=True, device_class="light"),
    Value(
        "room_flooding_alarm",
        "Room Flooding Alarm",
        lambda d: d.room_flooding_alarm,
        binary=True,
        device_class="moisture",
    ),
    Value(
        "backwash_day",
        "Backwash Day",
        lambda d: d.backwash_day_written,
        category="setting",
        feature="backwash_schedule",
    ),
    Value(
        "backwash_time",
        "Backwash Time",
        lambda d: d.backwash_time_written,
        category="setting",
        feature="backwash_schedule",
    ),
    Value(
        "saved_backwash_schedule",
        "Saved Backwash Schedule",
        lambda d: d.saved_backwash_schedule,
        category="setting",
        feature="backwash_schedule",
    ),
    Value(
        "clock",
        "Controller Last Update",
        lambda d: d.clock,
        category="diagnostic",
        device_class="timestamp",
        feature="clock",
    ),
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
    features=(
        Feature("backwash_schedule", "Backwash schedule"),
        Feature("clock", "Clock"),
    ),
    default_unit_id=1,
)
