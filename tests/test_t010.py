"""T010 model against a recorded snapshot and the firmware's register layout."""

from __future__ import annotations

import pytest

from pool_modbus.devices import get_device_type
from pool_modbus.devices.t010 import T010


async def read(unit) -> T010:
    device = get_device_type("t010").model(unit)
    await device.async_update()
    return device


async def test_one_read_within_the_firmware_limit(make_unit, t010_snapshot) -> None:
    unit = make_unit(t010_snapshot)
    await read(unit)
    assert unit.requests == [("holding", 0, 9)]  # the firmware allows at most 10


async def test_decodes_snapshot(make_unit, t010_snapshot) -> None:
    device = await read(make_unit(t010_snapshot))

    assert device.is_t010 is True
    assert device.software_version == "1.7"
    assert device.temperature == pytest.approx(19.0)
    assert device.heating_power == 0
    assert device.heating is False
    assert device.delay_remaining == "00:00"
    assert device.setpoint == pytest.approx(20.0)
    assert device.offset == pytest.approx(0.0)
    assert device.set_delay == 3
    assert device.heating_blocked is True
    assert device.heating_enabled is False
    assert device.delaying is False
    assert not any(
        (
            device.menu_mode,
            device.sensor_disconnected,
            device.sensor_supply_fault,
            device.eeprom_fault,
        )
    )


async def test_negative_offset(make_unit, t010_snapshot) -> None:
    registers = dict(t010_snapshot)
    registers[6] = 0xFFFB  # the firmware sends -5 with a 0xFF high byte
    device = await read(make_unit(registers))
    assert device.offset == pytest.approx(-0.5)


async def test_heating_and_delay(make_unit, t010_snapshot) -> None:
    registers = dict(t010_snapshot)
    registers[2] = 60  # PWM mode, 60 %
    registers[3] = (2 << 8) | 30  # 2 min 30 s left
    registers[8] = 1 << 9  # delaying, heating not blocked
    device = await read(make_unit(registers))
    assert device.heating is True
    assert device.delay_remaining == "02:30"
    assert device.delaying is True
    assert device.heating_enabled is True


@pytest.mark.parametrize(
    ("bit", "flag"),
    [
        (0, "menu_mode"),
        (1, "sensor_disconnected"),
        (2, "sensor_supply_fault"),
        (3, "eeprom_fault"),
    ],
)
async def test_status_bits(make_unit, t010_snapshot, bit: int, flag: str) -> None:
    registers = dict(t010_snapshot)
    registers[8] = 1 << bit
    device = await read(make_unit(registers))
    assert getattr(device, flag) is True


async def test_other_device_is_not_a_t010(make_unit, t010_snapshot) -> None:
    registers = dict(t010_snapshot)
    registers[0] = 0x2011
    device = await read(make_unit(registers))
    assert device.is_t010 is False


@pytest.mark.parametrize(
    ("flags", "power", "mode", "action"),
    [
        (1 << 8, 0, "off", "off"),  # heating blocked
        (0, 100, "heat", "heating"),
        (0, 0, "heat", "idle"),
    ],
)
async def test_thermostat_mode_and_action(
    make_unit, t010_snapshot, flags: int, power: int, mode: str, action: str
) -> None:
    registers = dict(t010_snapshot)
    registers[8], registers[2] = flags, power
    device = await read(make_unit(registers))
    assert (device.hvac_mode, device.hvac_action) == (mode, action)
