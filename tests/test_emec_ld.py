"""EMEC LD model against a simulated, byte-addressed controller."""

from __future__ import annotations

from datetime import datetime

import pytest

from pool_modbus.devices import get_device_type
from pool_modbus.devices.emec_ld import EmecLD, EmecUnit, OutputState, PulseMode, wire_address


async def read(pump) -> EmecLD:
    device = get_device_type("emec_ld").model(pump)
    await device.async_update()
    return device


@pytest.mark.parametrize(("index", "wire"), [(0, 1), (11, 23), (25, 51), (33, 67), (76, 153)])
def test_wire_address(index: int, wire: int) -> None:
    assert wire_address(index) == wire


async def test_reads_use_odd_addresses_and_few_requests(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(ldphcl_snapshot)
    await read(pump)
    assert pump.requests == [("holding", 1, 29), ("holding", 67, 6), ("holding", 153, 6)]
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


async def test_writes_are_refused(make_pump, ldphcl_snapshot) -> None:
    unit = EmecUnit(make_pump(ldphcl_snapshot))
    with pytest.raises(NotImplementedError):
        await unit.write_register(33, 1000)
