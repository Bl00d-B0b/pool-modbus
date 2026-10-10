"""Pool controller model against a recorded snapshot."""

from __future__ import annotations

from datetime import datetime, time

import pytest
from conftest import FakeUnit

from pool_modbus.devices import get_device_type, pool_controller, writing
from pool_modbus.devices.pool_controller import (
    COVER,
    PoolController,
    backwash,
    reset_alarms,
    set_backwash_day,
    set_backwash_time,
    set_light,
    sync_clock,
)

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
    assert device.cover_closed is True
    assert device.light_on is False
    assert device.room_flooding_alarm is False
    assert device.backwash_day_written == "Friday"
    assert device.backwash_time_written == time(6, 0)
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
    assert device.cover_closed is False
    assert device.room_flooding_alarm is True
    assert device.light_on is True


async def test_unsaved_backwash_change(make_unit, controller_snapshot) -> None:
    registers = dict(controller_snapshot)
    registers[17], registers[18], registers[19] = 1 << 0, 7, 30  # written, not saved
    device = await read(make_unit(registers))
    assert (device.backwash_day_written, device.backwash_time_written) == ("Monday", time(7, 30))
    assert device.saved_backwash_schedule == "Friday 06:00"


async def test_backwash_off(make_unit, controller_snapshot) -> None:
    registers = dict(controller_snapshot)
    registers[27] = 0
    device = await read(make_unit(registers))
    assert device.saved_backwash_schedule == "Off"


# -- writes --------------------------------------------------------------------
# The snapshot: register 16 = 1 (filtration on), schedule Friday 06:00 written and
# saved, status 24 = 0x0103 (pump running, filtering, level normal; cover closed).

VALUES = {value.key: value for value in get_device_type("pool_controller").values}
COMMAND = 16
OPEN = 1 << 3
LIGHT = 1 << 11


class FakeController(FakeUnit):
    """Acts on the command bits like the controller: the save pulse keeps the written
    schedule, the light pulse toggles the light while the cover is open, and the
    clock registers 32-35 set the clock read at 36-39."""

    async def write_register(self, address: int, value: int) -> None:
        before = self.registers.get(address, 0)
        await super().write_register(address, value)
        if address != COMMAND:
            return
        rising = value & ~before
        if rising & 1 << 15:
            for written, saved in ((17, 27), (18, 28), (19, 29)):
                self.registers[saved] = self.registers.get(written, 0)
        if rising & 1 << 4 and self.registers[STATUS] & OPEN:
            self.registers[STATUS] ^= LIGHT

    async def write_registers(self, address: int, values: list[int]) -> None:
        await super().write_registers(address, values)
        if address == 32:
            sec_wd, hour_min, month_day, cent_year = values
            self.registers[36] = (sec_wd >> 8) << 8 | hour_min & 0xFF
            self.registers[37] = (hour_min >> 8) << 8 | sec_wd & 0xFF
            self.registers[38] = (month_day & 0xFF) << 8 | month_day >> 8
            self.registers[39] = (cent_year & 0xFF) << 8 | cent_year >> 8


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(pool_controller, "LONG_PULSE", 0)
    monkeypatch.setattr(pool_controller, "SHORT_PULSE", 0)
    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(writing, "CONFIRM_TIMEOUT", 0.05)


async def controller(controller_snapshot, status: int | None = None):
    registers = dict(controller_snapshot)
    if status is not None:
        registers[STATUS] = status
    unit = FakeController(registers)
    return unit, await read(unit)


@pytest.mark.parametrize(
    ("key", "on", "word"),
    [("filtration_enabled", False, 0x0000), ("filling_blocked", True, 0x0021)],
)
async def test_switches_change_only_their_bit(controller_snapshot, key, on, word) -> None:
    unit, device = await controller(controller_snapshot)
    await VALUES[key].write(device, on)
    assert unit.writes == [(COMMAND, word)]


async def test_switch_already_as_asked_is_not_written(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await VALUES["filtration_enabled"].write(device, True)
    assert unit.writes == []


async def test_light_toggle_the_controller_ignores_is_no_error(
    controller_snapshot, monkeypatch
) -> None:
    from pool_modbus.devices import pool_controller

    monkeypatch.setattr(pool_controller, "LIGHT_WAIT", 0.05)
    unit, device = await controller(controller_snapshot)  # cover closed: it ignores the toggle
    await set_light(device, True)
    assert unit.writes == [(COMMAND, 0x0011), (COMMAND, 0x0001)]  # the pulse went out
    assert device.light_on is False  # and the light shows as it is


async def test_light_toggles_once_and_is_confirmed(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot, 0x0103 | OPEN)
    await set_light(device, True)
    assert unit.writes == [(COMMAND, 0x0011), (COMMAND, 0x0001)]  # pulse; filtration kept
    assert device.light_on is True

    await set_light(device, True)  # already on
    assert len(unit.writes) == 2


async def test_light_that_does_not_change_shows_as_it_is(controller_snapshot, monkeypatch) -> None:
    from pool_modbus.devices import pool_controller

    monkeypatch.setattr(pool_controller, "LIGHT_WAIT", 0.05)
    unit = FakeUnit({**controller_snapshot, STATUS: 0x0103 | OPEN})  # ignores the toggle
    device = await read(unit)
    await set_light(device, True)
    assert device.light_on is False


@pytest.mark.parametrize(
    ("status", "move", "words"),
    [
        (0x0103, COVER.open, [(COMMAND, 0x0005), (COMMAND, 0x0001)]),
        (0x0103 | OPEN, COVER.open, []),  # already open
        (0x0103 | OPEN, COVER.close, [(COMMAND, 0x0009), (COMMAND, 0x0001)]),
        (0x0103, COVER.close, []),  # already closed
    ],
)
async def test_cover(controller_snapshot, status, move, words) -> None:
    unit, device = await controller(controller_snapshot, status)
    await move(device)
    assert unit.writes == words


async def test_backwash_and_alarm_reset_are_pulses(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await backwash(device, datetime(2026, 10, 5))
    await reset_alarms(device, datetime(2026, 10, 5))
    assert unit.writes == [
        (COMMAND, 0x0003),
        (COMMAND, 0x0001),
        (COMMAND, 0x4001),
        (COMMAND, 0x0001),
    ]


async def test_backwash_day_is_written_then_saved(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await set_backwash_day(device, "Monday")
    assert unit.writes == [(17, 1), (COMMAND, 0x8001), (COMMAND, 0x0001)]
    assert device.saved_backwash_schedule == "Monday 06:00"


async def test_a_command_after_another_starts_from_the_current_word(
    controller_snapshot, monkeypatch
) -> None:
    from pool_modbus.devices import pool_controller, writing

    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(pool_controller, "LIGHT_WAIT", 0.05)
    # The gateway answers reads from a cache: reads lag a write by two polls.
    unit = FakeController(dict(controller_snapshot), stale_reads=2)
    device = await read(unit)

    await set_backwash_time(device, "06:05")  # ends with the save pulse
    await set_light(device, True)  # its pulse must not set the save bit again

    assert unit.registers[COMMAND] == 0x0001


async def test_a_pulse_clears_its_bit_even_if_the_set_is_not_confirmed(
    controller_snapshot, monkeypatch
) -> None:
    from pool_modbus.devices import writing

    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(writing, "CONFIRM_TIMEOUT", 0.02)
    unit = FakeController(dict(controller_snapshot), stale_reads=10_000)  # reads never catch up
    device = await read(unit)

    with pytest.raises(TimeoutError):
        await reset_alarms(device, datetime(2026, 10, 10, 12, 0))

    assert unit.writes == [(COMMAND, 0x4001), (COMMAND, 0x0001)]  # set, then cleared anyway


async def test_a_bit_found_set_is_cleared_by_the_pulse(controller_snapshot) -> None:
    unit = FakeController({**controller_snapshot, COMMAND: 0x0011})  # the light bit stuck
    device = await read(unit)

    await set_light(device, True)  # cover closed: the toggle is ignored

    assert unit.writes == [(COMMAND, 0x0001)]  # the set is skipped, the clearing write goes out


async def test_backwash_time_takes_any_minute(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await set_backwash_time(device, time(7, 31))
    assert unit.writes[0] == (18, [7, 31])
    assert device.backwash_time_written == time(7, 31)


async def test_backwash_time_is_written_then_saved(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await set_backwash_time(device, "07:30")
    assert unit.writes == [(18, [7, 30]), (COMMAND, 0x8001), (COMMAND, 0x0001)]
    assert device.saved_backwash_schedule == "Friday 07:30"


async def test_the_held_schedule_is_not_written_and_off_works(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    await set_backwash_day(device, "Friday")
    await set_backwash_time(device, "06:00")
    assert unit.writes == []  # already written and saved
    await set_backwash_day(device, "Off")
    assert device.saved_backwash_schedule == "Off"


async def test_schedule_the_controller_does_not_keep_is_reported(controller_snapshot) -> None:
    unit = FakeUnit(dict(controller_snapshot))  # ignores the save pulse
    device = await read(unit)
    with pytest.raises(TimeoutError, match="not saved"):
        await set_backwash_day(device, "Monday")


@pytest.mark.parametrize(
    ("write", "value"), [(set_backwash_day, "Funday"), (set_backwash_time, "25:00")]
)
async def test_bad_schedule_values_are_refused(controller_snapshot, write, value) -> None:
    unit, device = await controller(controller_snapshot)
    with pytest.raises(ValueError):
        await write(device, value)
    assert unit.writes == []


async def test_sync_clock(controller_snapshot) -> None:
    unit, device = await controller(controller_snapshot)
    now = datetime(2026, 10, 5, 14, 30, 15)  # a Monday
    await sync_clock(device, now)
    assert unit.writes == [(32, [15 << 8 | 1, 14 << 8 | 30, 10 << 8 | 5, 20 << 8 | 26])]
    assert device.clock == now
    assert device.clock_weekday == 1
