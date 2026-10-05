"""Pool controller model against a recorded snapshot."""

from __future__ import annotations

from datetime import datetime

import pytest

from pool_modbus.devices import get_device_type
from pool_modbus.devices.pool_controller import PoolController

STATUS = 24


async def read(unit) -> PoolController:
    device = get_device_type("pool_controller").model(unit)
    await device.async_update()
    return device


async def test_one_read_of_registers_16_to_39(make_unit, controller_snapshot) -> None:
    unit = make_unit(controller_snapshot)
    await read(unit)
    assert unit.requests == [("holding", 16, 24)]


async def test_decodes_snapshot(make_unit, controller_snapshot) -> None:
    device = await read(make_unit(controller_snapshot))

    assert device.filtration_enabled is True
    assert device.filling_blocked is False
    assert device.filtration_mode == "Filtering"
    assert device.pump_running is True
    assert device.water_level == "Normal"
    assert device.water_level_monitoring is True
    assert device.filling_up is False
    assert device.cover == "closed"
    assert device.light_on is False
    assert device.room_flooding_alarm is False
    assert device.backwash_day_written == "Friday"
    assert device.backwash_time_written == "06:00"
    assert device.saved_backwash_schedule == "Friday 06:00"
    assert device.clock == datetime(2026, 10, 5, 12, 16, 3)
    assert device.clock_weekday == 1  # Monday


@pytest.mark.parametrize(
    ("bits", "mode"),
    [
        (0, "Off"),
        (1 << 1, "Filtering"),
        (1 << 2, "Backwashing"),
        ((1 << 1) | (1 << 2), "Backwashing"),
    ],
)
async def test_filtration_mode(make_unit, controller_snapshot, bits: int, mode: str) -> None:
    registers = dict(controller_snapshot)
    registers[STATUS] = bits
    device = await read(make_unit(registers))
    assert device.filtration_mode == mode


@pytest.mark.parametrize(
    ("bits", "level"),
    [
        (1 << 10 | 1 << 8, "Off"),  # monitoring off wins
        (1 << 6 | 1 << 8, "Minimum"),
        (1 << 7, "Low"),
        (1 << 8, "Normal"),
        (1 << 9, "Maximum"),
        (0, "Unknown"),
    ],
)
async def test_water_level(make_unit, controller_snapshot, bits: int, level: str) -> None:
    registers = dict(controller_snapshot)
    registers[STATUS] = bits
    device = await read(make_unit(registers))
    assert device.water_level == level


async def test_open_cover_light_and_alarm(make_unit, controller_snapshot) -> None:
    registers = dict(controller_snapshot)
    registers[STATUS] = 1 << 3 | 1 << 5 | 1 << 11
    device = await read(make_unit(registers))
    assert device.cover == "open"
    assert device.room_flooding_alarm is True
    assert device.light_on is True


async def test_unsaved_backwash_change(make_unit, controller_snapshot) -> None:
    registers = dict(controller_snapshot)
    registers[17], registers[18], registers[19] = 1 << 0, 7, 30  # written, not saved
    device = await read(make_unit(registers))
    assert (device.backwash_day_written, device.backwash_time_written) == ("Monday", "07:30")
    assert device.saved_backwash_schedule == "Friday 06:00"


async def test_backwash_off(make_unit, controller_snapshot) -> None:
    registers = dict(controller_snapshot)
    registers[27] = 0
    device = await read(make_unit(registers))
    assert device.saved_backwash_schedule == "Off"
