"""T010 model against a recorded snapshot and the firmware's register layout."""

from __future__ import annotations

import pytest

from pool_modbus.devices import get_device_type
from pool_modbus.devices.t010 import T010, THERMOSTAT
from pool_modbus.reader import thermostat_text

VALUES = {value.key: value for value in get_device_type("t010").values}


async def write(device: T010, key: str, value) -> None:
    """Write a setting the way Home Assistant does: the setpoint through the thermostat."""
    if key == "setpoint":
        await THERMOSTAT.set_target_temperature(device, value)
    else:
        await VALUES[key].write(device, value)


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


# -- writes --------------------------------------------------------------------
# The snapshot: setpoint 20.0 °C, offset 0.0, delay 3 min, heating blocked (0x0100).


@pytest.mark.parametrize(
    ("key", "value", "register", "word"),
    [
        ("setpoint", 21.5, 5, 215),
        ("setpoint", 5.0, 5, 50),
        ("setpoint", 40.0, 5, 400),
        ("offset", -0.5, 6, 0xFFFB),  # signed, as the firmware reads it back
        ("offset", 3.1, 6, 31),
        ("set_delay", 10, 7, 10),
        ("set_delay", 0.0, 7, 0),  # Home Assistant passes numbers as floats
    ],
)
async def test_write_setting(
    make_unit, t010_snapshot, key: str, value: float, register: int, word: int
) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)

    await write(device, key, value)

    assert unit.writes == [(register, word)]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("setpoint", 4.9),
        ("setpoint", 40.1),
        ("offset", -3.2),
        ("offset", 3.2),
        ("set_delay", 60),
        ("set_delay", -1),
        ("set_delay", 2.5),
    ],
)
async def test_value_out_of_range_is_refused_before_any_request(
    make_unit, t010_snapshot, key: str, value: float
) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)
    unit.requests.clear()

    with pytest.raises(ValueError):
        await write(device, key, value)

    assert unit.requests == []


async def test_writing_the_value_the_device_holds_is_skipped(make_unit, t010_snapshot) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)

    await write(device, "setpoint", 20.0)
    await write(device, "set_delay", 3.0)
    await THERMOSTAT.set_mode(device, "off")  # already blocked

    assert unit.writes == []  # every write would go to the firmware's EEPROM


async def test_the_device_is_read_before_comparing(make_unit, t010_snapshot) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)
    unit.registers[5] = 215  # changed on the device since the last poll

    await write(device, "setpoint", 20.0)

    assert unit.writes == [(5, 200)]


async def test_heating_switch_changes_only_its_bit(make_unit, t010_snapshot) -> None:
    registers = dict(t010_snapshot)
    registers[8] = 0x0100 | 0x0200 | 0x0001  # blocked, delaying, menu open
    unit = make_unit(registers)
    device = await read(unit)

    await THERMOSTAT.set_mode(device, "heat")

    assert unit.writes == [(8, 0x0200 | 0x0001)]


async def test_delaying_switch_changes_only_its_bit(make_unit, t010_snapshot) -> None:
    unit = make_unit(dict(t010_snapshot))  # register 8 = 0x0100, heating blocked
    device = await read(unit)

    await VALUES["delaying"].write(device, True)

    assert unit.writes == [(8, 0x0300)]


@pytest.mark.parametrize(("mode", "word"), [("heat", 0x0000), ("off", 0x0100)])
async def test_thermostat_mode(make_unit, t010_snapshot, mode: str, word: int) -> None:
    registers = dict(t010_snapshot)
    registers[8] = 0x0100 if mode == "heat" else 0x0000
    unit = make_unit(registers)
    device = await read(unit)

    await THERMOSTAT.set_mode(device, mode)

    assert unit.writes == [(8, word)]


async def test_thermostat_target_and_state(make_unit, t010_snapshot) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)
    assert (THERMOSTAT.current_temperature(device), THERMOSTAT.target_temperature(device)) == (
        pytest.approx(19.0),
        pytest.approx(20.0),
    )
    assert thermostat_text(THERMOSTAT, device) == "off, target 20.0 °C"

    await THERMOSTAT.set_target_temperature(device, 32.5)
    await THERMOSTAT.set_mode(device, "heat")
    await device.async_update()

    assert unit.writes == [(5, 325), (8, 0x0000)]
    assert device.setpoint == pytest.approx(32.5)
    assert thermostat_text(THERMOSTAT, device) == "heat, idle, target 32.5 °C"


async def test_thermostat_refuses_other_modes(make_unit, t010_snapshot) -> None:
    unit = make_unit(dict(t010_snapshot))
    device = await read(unit)

    with pytest.raises(ValueError):
        await THERMOSTAT.set_mode(device, "cool")

    assert unit.writes == []


# -- confirming writes -----------------------------------------------------------


@pytest.fixture
def quick_confirm(monkeypatch):
    from pool_modbus.devices import writing

    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0.0)
    monkeypatch.setattr(writing, "CONFIRM_TIMEOUT", 0.2)


async def test_write_waits_until_the_device_shows_it(
    make_unit, t010_snapshot, quick_confirm
) -> None:
    unit = make_unit(dict(t010_snapshot), stale_reads=3)  # a gateway answering from its cache
    device = await read(unit)

    await write(device, "setpoint", 21.5)

    assert unit.writes == [(5, 215)]
    assert device.setpoint == pytest.approx(21.5)


async def test_write_the_device_never_shows_raises(make_unit, t010_snapshot, quick_confirm) -> None:
    unit = make_unit(dict(t010_snapshot), stale_reads=10**6)
    device = await read(unit)

    with pytest.raises(TimeoutError, match="setpoint still reads 20.0"):
        await write(device, "setpoint", 21.5)


async def test_two_flag_writes_in_a_row_keep_each_other(
    make_unit, t010_snapshot, quick_confirm
) -> None:
    unit = make_unit(dict(t010_snapshot), stale_reads=3)  # register 8 = 0x0100, heating blocked
    device = await read(unit)

    await THERMOSTAT.set_mode(device, "heat")  # clears bit 8
    await VALUES["delaying"].write(device, True)  # sets bit 9, must not bring bit 8 back

    assert unit.writes == [(8, 0x0000), (8, 0x0200)]
    assert unit.registers[8] == 0x0200


def test_values_the_thermostat_covers_are_not_repeated() -> None:
    assert not {"setpoint", "heating_enabled", "heating"} & set(VALUES)
    # Heating power adds to the thermostat in PWM mode only: an optional part, off by default.
    assert VALUES["heating_power"].feature == "heating_power"
    assert "heating_power" not in get_device_type("t010").default_features()
