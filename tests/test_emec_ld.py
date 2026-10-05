"""EMEC LD model against a simulated, byte-addressed controller."""

from __future__ import annotations

from datetime import datetime

import pytest

from pool_modbus.devices import get_device_type
from pool_modbus.devices.emec_ld import EmecLD, EmecUnit, OutputState, PulseMode, wire_address
from pool_modbus.reader import value_text


async def read(pump) -> EmecLD:
    device = get_device_type("emec_ld").model(pump)
    await device.async_update()
    return device


@pytest.mark.parametrize(("index", "wire"), [(0, 1), (11, 23), (25, 51), (33, 67), (76, 153)])
def test_wire_address(index: int, wire: int) -> None:
    assert wire_address(index) == wire


async def test_one_read_from_an_odd_address(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(ldphcl_snapshot)
    await read(pump)
    assert pump.requests == [("holding", 1, 82)]  # values 0-81; the controller allows 125
    assert pump.message_spacing == pytest.approx(0.1)


async def test_decodes_ldphcl_snapshot(make_pump, ldphcl_snapshot) -> None:
    device = await read(make_pump(ldphcl_snapshot))

    assert device.ch1_value == pytest.approx(7.51)  # pH
    assert device.ch2_value == pytest.approx(0.46)  # free chlorine, ppm
    assert device.temperature == pytest.approx(17.6)
    assert device.probe_mv_ch1 == -27
    assert device.probe_mv_ch2 == 34
    assert device.relay_ch1 is OutputState.ON
    assert device.relay_ch2 is OutputState.ON
    assert device.pulse_rate_ch1 == 0
    assert device.pulse_rate_ch1_2 == 0
    assert device.pulse_rate_ch2 == 0
    assert device.clock == datetime(2026, 10, 1, 20, 32)


async def test_decodes_dosing_settings(make_pump, ldphcl_snapshot) -> None:
    device = await read(make_pump(ldphcl_snapshot))

    # pH: 0 p/min at 7.50, rising to 30 p/min at 10.00
    assert device.ch1_pulse_val1 == pytest.approx(10.0)
    assert device.ch1_pulse_val2 == pytest.approx(7.5)
    assert device.ch1_pulse_perc1 == 30
    assert device.ch1_pulse_perc2 == 0
    assert device.ch1_pulse_wait == 0
    assert device.ch1_pulse_mode is PulseMode.PROPORTIONAL

    # Chlorine: 13 p/min at 0.00 ppm, falling to 0 p/min at 0.45 ppm
    assert device.ch2_pulse_val1 == pytest.approx(0.0)
    assert device.ch2_pulse_val2 == pytest.approx(0.45)
    assert device.ch2_pulse_perc1 == 13
    assert device.ch2_pulse_perc2 == 0
    assert device.ch2_pulse_mode is PulseMode.PROPORTIONAL


@pytest.mark.parametrize(
    ("word", "rate"),
    [
        (0x0000, 0),
        (0x0100, 1),
        (0x0500, 5),
        (0x0800, 8),  # recorded at 0.14 ppm chlorine
        (0xFF00, 0),  # one-poll transient while the rate sits at zero
    ],
)
async def test_pulse_rate_is_the_high_byte(
    make_pump, ldphcl_snapshot, word: int, rate: int
) -> None:
    registers = dict(ldphcl_snapshot)
    registers[wire_address(14)] = word  # 40030, chlorine pulse output
    device = await read(make_pump(registers))
    assert device.pulse_rate_ch2 == rate


@pytest.mark.parametrize(
    ("word", "state"),
    [(0x0000, OutputState.DISABLED), (0x0100, OutputState.ON), (0x0200, OutputState.OFF)],
)
async def test_relay_state_is_the_high_byte(make_pump, ldphcl_snapshot, word, state) -> None:
    registers = dict(ldphcl_snapshot)
    registers[wire_address(15)] = word  # 40032, channel 1 relay
    device = await read(make_pump(registers))
    assert device.relay_ch1 is state


async def test_invalid_clock_reads_as_none(make_pump, ldphcl_snapshot) -> None:
    registers = dict(ldphcl_snapshot)
    for index in (21, 22, 23):
        registers[wire_address(index)] = 0
    device = await read(make_pump(registers))
    assert device.clock is None


async def test_a_write_goes_to_the_odd_wire_address(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(ldphcl_snapshot)
    unit = EmecUnit(pump)
    await unit.write_register(33, 755)  # 40068, ch1 pulse1 val1
    assert pump.writes == [(67, 755)]
    with pytest.raises(NotImplementedError):
        await unit.write_registers(33, [755, 750])  # one value per request


@pytest.mark.parametrize(
    ("mode", "available", "unavailable"),
    [
        (
            PulseMode.PROPORTIONAL,
            {"pH Max Value", "pH Min Value", "pH Max Pulse Rate", "pH Min Pulse Rate"},
            {"pH Pulse Speed"},
        ),
        (
            PulseMode.ON_OFF,
            {"pH Max Value", "pH Min Value", "pH Pulse Speed"},
            {"pH Max Pulse Rate", "pH Min Pulse Rate"},
        ),
        (
            PulseMode.DISABLED,
            set(),
            {
                "pH Max Value",
                "pH Min Value",
                "pH Max Pulse Rate",
                "pH Min Pulse Rate",
                "pH Pulse Speed",
            },
        ),
    ],
)
async def test_dosing_settings_follow_the_working_mode(
    make_pump, ldphcl_snapshot, mode, available: set[str], unavailable: set[str]
) -> None:
    registers = dict(ldphcl_snapshot)
    registers[wire_address(38)] = mode  # 40078, channel 1 pulse output mode
    device = await read(make_pump(registers))
    texts = {v.name: value_text(v, device) for v in get_device_type("emec_ld").values}
    assert {name for name in available | unavailable if texts[name] == "unavailable"} == unavailable


# -- writes --------------------------------------------------------------------
# The snapshot: proportional mode, pH Max 10.00 (wire 67 = 1000), pH Min 7.50
# (69 = 750), max rate 30 (71), min rate 0 (73), speed 0 (75), mode 1 (77).

WRITE_VALUES = {value.key: value for value in get_device_type("emec_ld").values}


@pytest.fixture(autouse=True)
def quick_confirm(monkeypatch):
    from pool_modbus.devices import writing

    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(writing, "CONFIRM_TIMEOUT", 0.05)


@pytest.mark.parametrize(
    ("key", "value", "writes"),
    [
        ("ph_max", 9.9, [(67, 990)]),
        ("ph_min", 7.55, [(69, 755)]),
        ("ph_max_rate", 29, [(71, 29)]),  # min rate already 0
        ("ph_min_rate", 5, [(73, 5), (71, 0)]),  # the other end goes to 0, as on the pump
        ("ph_pulse_speed", 3, [(75, 3)]),
    ],
)
async def test_dosing_setting_writes(make_pump, ldphcl_snapshot, key, value, writes) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES[key].write(device, value)
    assert pump.writes == writes


async def test_a_setting_the_pump_holds_is_not_written(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES["ph_max"].write(device, 10.0)
    await WRITE_VALUES["ph_max_rate"].write(device, 30)
    await WRITE_VALUES["ph_mode"].write(device, "Proportional")
    assert pump.writes == []


@pytest.mark.parametrize(
    ("label", "writes"),
    [
        # rate 100 (min rate already 0), one pulse a minute, then the mode
        ("ON/OFF", [(71, 100), (75, 1), (77, 0)]),
        ("Disabled", [(77, 2)]),
    ],
)
async def test_mode_switch_writes_its_settings_in_order(
    make_pump, ldphcl_snapshot, label, writes
) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES["ph_mode"].write(device, label)
    assert pump.writes == writes
    assert device.ch1_pulse_mode.label == label


async def test_back_to_proportional(make_pump, ldphcl_snapshot) -> None:
    registers = dict(ldphcl_snapshot)
    registers[75], registers[77] = 1, 0  # ON/OFF, one pulse a minute
    pump = make_pump(registers)
    device = await read(pump)
    await WRITE_VALUES["ph_mode"].write(device, "Proportional")
    assert pump.writes == [(75, 0), (77, 1)]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ph_max", 14.01),
        ("ph_min", -0.1),
        ("ph_max_rate", 181),
        ("ph_min_rate", 2.5),
        ("ph_pulse_speed", 100),
        ("ph_mode", "Turbo"),
    ],
)
async def test_values_the_pump_does_not_take_are_refused(
    make_pump, ldphcl_snapshot, key, value
) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    pump.requests.clear()
    with pytest.raises(ValueError):
        await WRITE_VALUES[key].write(device, value)
    assert pump.writes == []
