"""EMEC LD model against a simulated, byte-addressed controller."""

from __future__ import annotations

from datetime import datetime

import pytest
from modbus_connection import ModbusError

from pool_modbus import update_or_keep
from pool_modbus.devices import ImplausibleReadError, get_device_type
from pool_modbus.devices.emec_ld import (
    EmecLD,
    EmecUnit,
    FlowMode,
    OutputState,
    PulseMode,
    wire_address,
)
from pool_modbus.reader import value_text


async def read(pump) -> EmecLD:
    device = get_device_type("emec_ld").model(pump)
    await device.async_update()
    return device


@pytest.mark.parametrize(("index", "wire"), [(0, 1), (11, 23), (25, 51), (33, 67), (76, 153)])
def test_wire_address(index: int, wire: int) -> None:
    assert wire_address(index) == wire


async def test_reads_are_register_blocks_and_one_coil_block(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(ldphcl_snapshot)
    await read(pump)
    # values 0-110 (up to the Cl relay's mode) and 213-262 (dosing alarm, timing); the alarm
    # coils in two reads of 8, the settings coils in reads of 8, each block once
    assert sorted(pump.requests) == [
        ("coils", 0, 8),
        ("coils", 8, 8),
        ("coils", 40, 8),
        ("coils", 48, 8),
        ("coils", 56, 8),
        ("coils", 72, 8),
        ("coils", 88, 8),
        ("holding", 1, 111),
        ("holding", 427, 50),  # the dosing alarm times 40428/40430 through the timing
    ]
    assert pump.message_spacing == pytest.approx(0.1)


async def test_alarm_coils(make_pump, ldphcl_snapshot) -> None:
    """Alarm coils at address = protocol number - 1; tested at the pump 2026-10-15:
    01 pH tank 1, 03 Cl tank, 04 no flow, 14 standby."""
    device = await read(make_pump(ldphcl_snapshot, coils={0: True}))
    assert (device.ph_tank_empty, device.cl_tank_empty, device.no_flow) == (True, False, False)
    device = await read(make_pump(ldphcl_snapshot, coils={2: True}))
    assert (device.ph_tank_empty, device.cl_tank_empty) == (False, True)
    device = await read(make_pump(ldphcl_snapshot, coils={3: True, 13: True}))
    assert (device.no_flow, device.standby) == (True, True)


@pytest.mark.parametrize(
    ("key", "address"),
    [
        ("ph_tank_2_empty", 1),
        ("ph_out_of_range", 4),
        ("cl_out_of_range", 5),
        ("cl_pulse_dosing_alarm", 6),
        ("cl_relay_dosing_alarm", 7),
        ("ph_pulse_1_dosing_alarm", 8),
        ("ph_relay_dosing_alarm", 9),
        ("ph_pulse_2_dosing_alarm", 10),
        ("ph_probe_fail", 11),
        ("cl_probe_fail", 12),
    ],
)
async def test_documented_alarms(make_pump, ldphcl_snapshot, key, address) -> None:
    """The protocol's other alarms, at their protocol address."""
    values = {v.key: v for v in get_device_type("emec_ld").values}
    device = await read(make_pump(ldphcl_snapshot, coils={address: True}))
    assert values[key].get(device) is True
    device = await read(make_pump(ldphcl_snapshot))
    assert values[key].get(device) is False


async def test_coils_come_from_reads_of_8(make_pump, ldphcl_snapshot) -> None:
    """Seen live 2026-10-15: a read of 8 from 0 with the pH tank empty (address 0) set bit
    7, with the Cl tank empty (address 2) bit 5; from 56 the priority (58) is bit 5."""
    pump = make_pump(ldphcl_snapshot, coils={0: True, 13: True, 58: True})
    unit = EmecUnit(pump)
    assert await unit.read_coils(13, 1) == [True]
    assert await unit.read_coils(0, 3) == [True, False, False]
    assert await unit.read_coils(58, 1) == [True]
    assert pump.requests == [("coils", 8, 8), ("coils", 0, 8), ("coils", 56, 8)]
    with pytest.raises(NotImplementedError):
        await unit.write_coil(10, True)  # only the tested configuration coil


async def test_priority_is_coil_58(make_pump, ldphcl_snapshot) -> None:
    """Seen live 2026-10-14: No priority sets the coil, which a 128-coil read shows at 62."""
    pump = make_pump(ldphcl_snapshot, coils={58: True})
    device = await read(pump)
    values = {v.key: v for v in get_device_type("emec_ld").values}
    assert value_text(values["priority"], device) == "No priority"

    pump.slow_write_reply = True  # the gateway answers the write with 0x0B, as live
    await values["priority"].write(device, "pH priority")
    assert [r for r in pump.requests if r[0] == "write_coil"] == [("write_coil", 58, False)]
    assert value_text(values["priority"], device) == "pH priority"
    with pytest.raises(ValueError):
        await values["priority"].write(device, "Cl priority")


async def test_dosing_alarm(make_pump, ldphcl_snapshot) -> None:
    """As set on the keypad 2026-10-15: pH 49 min, Cl 177 min (40428, 40430; 0 = off) and
    Dose / Stop in coils 44 (pH) and 59 (Cl)."""
    registers = dict(ldphcl_snapshot)
    registers.update({427: 49, 429: 177})
    pump = make_pump(registers, coils={44: True})
    device = await read(pump)
    values = {v.key: v for v in get_device_type("emec_ld").values}
    assert value_text(values["ph_dosing_alarm_time"], device) == "49 min"
    assert value_text(values["cl_dosing_alarm_time"], device) == "177 min"
    assert value_text(values["ph_dosing_alarm_action"], device) == "Stop"
    assert value_text(values["cl_dosing_alarm_action"], device) == "Dose"

    pump.slow_write_reply = True  # the gateway answers with 0x0B, as for coil 58
    await values["cl_dosing_alarm_action"].write(device, "Stop")
    await values["ph_dosing_alarm_time"].write(device, 0)
    assert [r for r in pump.requests if r[0] == "write_coil"] == [("write_coil", 59, True)]
    assert pump.writes == [(427, 0)]
    assert value_text(values["ph_dosing_alarm_action"], device) == "unavailable"  # off
    with pytest.raises(ValueError):
        await values["cl_dosing_alarm_time"].write(device, 1000)


async def test_probe_failure(make_pump, ldphcl_snapshot) -> None:
    """As set on the keypad 2026-10-15: pH 117 min Stop, Cl 104 min Dose (40444, 40446;
    coils 75, 90); the keypad offers 0 (off) or 100-999 min."""
    registers = dict(ldphcl_snapshot)
    registers.update({443: 117, 445: 104})
    pump = make_pump(registers, coils={75: True})
    device = await read(pump)
    values = {v.key: v for v in get_device_type("emec_ld").values}
    assert value_text(values["ph_probe_alarm_time"], device) == "117 min"
    assert value_text(values["cl_probe_alarm_time"], device) == "104 min"
    assert value_text(values["ph_probe_alarm_action"], device) == "Stop"
    assert value_text(values["cl_probe_alarm_action"], device) == "Dose"
    with pytest.raises(ValueError):
        await values["cl_probe_alarm_time"].write(device, 50)  # below the keypad's 100
    await values["cl_probe_alarm_time"].write(device, 0)
    assert pump.writes == [(445, 0)]


async def test_flow_time_only_with_a_flow_mode(make_pump, ldphcl_snapshot) -> None:
    """As on the keypad 2026-10-15: the time shows for Reverse and Direct only, and
    Disabled sets it to 0."""
    values = {v.key: v for v in get_device_type("emec_ld").values}
    pump = make_pump(ldphcl_snapshot)  # Direct, 5 min
    device = await read(pump)
    assert value_text(values["flow_time"], device) == "5 min"
    await values["flow_mode"].write(device, "Disabled")
    assert pump.writes == [(523, 0), (525, 0)]  # 40524 time, then 40526 mode
    assert value_text(values["flow_time"], device) == "unavailable"


async def test_out_of_range_alarm(make_pump, ldphcl_snapshot) -> None:
    """As set on the keypad 2026-10-15: pH High 8.76 and Low 6.34 on, 11 min, Stop; Cl High
    3.21 on, Low off, 22 min, Dose (40438-40442, 40454-40458; coils 51-53 for pH, 61-63
    for Cl: High, Low, Stop)."""
    registers = dict(ldphcl_snapshot)
    registers.update({437: 876, 439: 11, 441: 634, 453: 321, 455: 22, 457: 0})
    pump = make_pump(registers, coils={51: True, 52: True, 53: True, 61: True})
    device = await read(pump)
    values = {v.key: v for v in get_device_type("emec_ld").values}
    texts = {k: value_text(v, device) for k, v in values.items() if "_range_" in k}
    assert texts == {
        "ph_range_high": "on",
        "ph_range_high_value": "8.76 pH",
        "ph_range_low": "on",
        "ph_range_low_value": "6.34 pH",
        "ph_range_time": "11 min",
        "ph_range_action": "Stop",
        "cl_range_high": "on",
        "cl_range_high_value": "3.21 mg/L",
        "cl_range_low": "off",
        "cl_range_low_value": "unavailable",
        "cl_range_time": "22 min",
        "cl_range_action": "Dose",
    }
    await values["ph_range_action"].write(device, "Dose")  # tested live: coil 53 follows
    await values["ph_range_time"].write(device, 12)  # tested live: 40440 follows
    await values["cl_range_high"].write(device, False)  # tested live: coil 61 follows
    writes = [r for r in pump.requests if r[0] == "write_coil"]
    assert writes == [("write_coil", 53, False), ("write_coil", 61, False)]
    assert pump.writes == [(439, 12)]
    device = await read(make_pump(registers))  # no side on: no time or action
    assert value_text(values["cl_range_time"], device) == "unavailable"
    with pytest.raises(ValueError):
        await values["ph_range_low_value"].write(device, 14.0)  # the keypad stops at 13.9


async def test_cl_range_coils_are_one_off_from_the_protocol(make_pump, ldphcl_snapshot) -> None:
    """Seen on the keypad 2026-10-15: Cl High off, Low on, Stop = coils 61 off, 62 on, 63 on."""
    values = {v.key: v for v in get_device_type("emec_ld").values}
    pump = make_pump(ldphcl_snapshot, coils={62: True, 63: True})
    device = await read(pump)
    assert value_text(values["cl_range_high"], device) == "off"
    assert value_text(values["cl_range_low"], device) == "on"
    assert value_text(values["cl_range_action"], device) == "Stop"
    await values["cl_range_action"].write(device, "Dose")  # tested live: coil 63 follows
    assert [r for r in pump.requests if r[0] == "write_coil"] == [("write_coil", 63, False)]


async def test_probe_failure_action_only_while_a_time_is_set(make_pump, ldphcl_snapshot) -> None:
    """As on the keypad: Dose/Stop shows only while the time is set; Off keeps the choice."""
    values = {v.key: v for v in get_device_type("emec_ld").values}
    device = await read(make_pump(dict(ldphcl_snapshot), coils={75: True}))  # times 0, pH Stop
    assert value_text(values["ph_probe_alarm_time"], device) == "0 min"
    assert value_text(values["ph_probe_alarm_action"], device) == "unavailable"
    assert device.probe_alarm_ph_stop is True  # still stored


async def test_timing_values(make_pump, ldphcl_snapshot) -> None:
    device = await read(make_pump(ldphcl_snapshot))
    assert device.tau == 5
    assert device.feeding_delay == 5
    assert device.flow_time == 5
    assert device.flow_mode is FlowMode.DIRECT


async def test_decodes_ldphcl_snapshot(make_pump, ldphcl_snapshot) -> None:
    device = await read(make_pump(ldphcl_snapshot))

    assert device.ch1_value == pytest.approx(7.51)  # pH
    assert device.ch2_value == pytest.approx(0.46)  # free chlorine, mg/l
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
    assert device.ch1_pulse1_val1 == pytest.approx(10.0)
    assert device.ch1_pulse1_val2 == pytest.approx(7.5)
    assert device.ch1_pulse1_perc1 == 30
    assert device.ch1_pulse1_perc2 == 0
    assert device.ch1_pulse1_wait == 0
    assert device.ch1_pulse1_mode is PulseMode.PROPORTIONAL

    # pH, second pulse output: stored settings of an output that is disabled
    assert device.ch1_pulse2_val1 == pytest.approx(7.8)
    assert device.ch1_pulse2_val2 == pytest.approx(7.3)
    assert device.ch1_pulse2_perc1 == 180
    assert device.ch1_pulse2_perc2 == 0
    assert device.ch1_pulse2_wait == 0
    assert device.ch1_pulse2_mode is PulseMode.DISABLED

    # Chlorine: 13 p/min at 0.00 mg/l, falling to 0 p/min at 0.45 mg/l
    assert device.ch2_pulse1_val1 == pytest.approx(0.0)
    assert device.ch2_pulse1_val2 == pytest.approx(0.45)
    assert device.ch2_pulse1_perc1 == 13
    assert device.ch2_pulse1_perc2 == 0
    assert device.ch2_pulse1_mode is PulseMode.PROPORTIONAL


@pytest.mark.parametrize(
    ("word", "rate"),
    [
        (0x0000, 0),
        (0x0100, 1),
        (0x0500, 5),
        (0x0800, 8),  # recorded at 0.14 mg/l chlorine
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


async def test_relay_2_has_the_same_values_as_relay_1(make_pump, ldphcl_snapshot) -> None:
    """Channel 1's second pulse output: its own rate (40028) and settings (40140-40150),
    which follow its working mode like relay 1's; disabled in the snapshot."""
    registers = dict(ldphcl_snapshot)
    registers[wire_address(13)] = 0x0300  # 40028: 3 p/min on output 2
    device = await read(make_pump(registers))
    values = {v.name: v for v in get_device_type("emec_ld").values}
    texts = {name: value_text(values[name], device) for name in values}
    assert texts["pH Pulse 2 Rate"] == "3 p/min"
    assert texts["pH Pulse 2 Dosing Mode"] == "Disabled"
    for name in ("On Value", "Off Value", "On Rate", "Off Rate", "Pulse Speed"):
        assert texts[f"pH Pulse 2 {name}"] == "unavailable"
        assert values[f"pH Pulse 2 {name}"].writable
    assert {v.key for v in values.values() if v.name.startswith("pH Pulse 2")} == {
        f"ph2_{k}" for k in ("mode", "max", "min", "max_rate", "min_rate", "pulse_speed")
    } | {"pulse_rate_ph_2", "ph_pulse_2_dosing_alarm"}

    registers[wire_address(74)] = PulseMode.PROPORTIONAL  # 40150
    device = await read(make_pump(registers))
    texts = {name: value_text(values[name], device) for name in values}
    assert texts["pH Pulse 2 On Value"] == "7.8 pH"
    assert texts["pH Pulse 2 Off Value"] == "7.3 pH"
    assert texts["pH Pulse 2 On Rate"] == "180 p/min"
    assert texts["pH Pulse 2 Off Rate"] == "0 p/min"
    assert texts["pH Pulse 2 Pulse Speed"] == "unavailable"


async def test_switching_relays(make_pump, ldphcl_snapshot) -> None:
    """CH1 PH RELAY 40082-40088 with its mode at 40092 (tested on the keypad 2026-10-15)
    and CH2 CL2 RELAY 40212-40220: Proportional PWM between 6.55 (100 %) and 6.50 (0 %)
    pH, 2.45 and 2.50 mg/l."""
    registers = dict(ldphcl_snapshot)
    registers.update({81: 655, 83: 650, 85: 100, 87: 0, 89: 0, 91: 0})
    registers.update({211: 245, 213: 250, 215: 100, 217: 0, 219: 0, 221: 0})
    device = await read(make_pump(registers))
    values = {v.name: v for v in get_device_type("emec_ld").values}
    texts = {name: value_text(values[name], device) for name in values}
    assert texts["pH Relay Dosing Mode"] == "Proportional PWM"
    assert texts["pH Relay On Value"] == "6.55 pH"
    assert texts["pH Relay Off Value"] == "6.5 pH"
    assert texts["pH Relay Max Duty Cycle"] == "100 %"
    assert texts["pH Relay On Period"] == "unavailable"
    assert texts["Cl Relay Off Value"] == "2.5 mg/L"
    assert texts["Cl Relay Dosing Mode"] == "Proportional PWM"
    assert texts["pH Relay State"] == "On"

    registers[91] = 1  # ON/OFF: no duty cycles
    device = await read(make_pump(registers))
    assert value_text(values["pH Relay Max Duty Cycle"], device) == "unavailable"
    assert value_text(values["pH Relay On Value"], device) == "6.55 pH"
    registers.update({91: 2, 85: 231, 87: 123})  # Fixed PWM, as set on the keypad
    device = await read(make_pump(registers))
    assert value_text(values["pH Relay On Period"], device) == "231 s"
    assert value_text(values["pH Relay Off Period"], device) == "123 s"
    assert value_text(values["pH Relay Max Duty Cycle"], device) == "unavailable"
    registers[91] = 3  # Disabled: no values either
    device = await read(make_pump(registers))
    assert value_text(values["pH Relay On Value"], device) == "unavailable"
    registers[221] = 1  # Cl relay ON/OFF (its mode at 40222, as found 2026-10-15)
    device = await read(make_pump(registers))
    assert value_text(values["Cl Relay Dosing Mode"], device) == "ON/OFF"
    assert value_text(values["Cl Relay Max Duty Cycle"], device) == "unavailable"
    assert value_text(values["Cl Relay On Value"], device) == "2.45 mg/L"


async def test_ph_relay_duty_cycles_one_at_a_time(make_pump, ldphcl_snapshot) -> None:
    """As on the keypad: setting one duty cycle sets the other to 0."""
    registers = dict(ldphcl_snapshot)
    registers.update({81: 655, 83: 650, 85: 3, 87: 0, 91: 0})
    pump = make_pump(registers)
    device = await read(pump)
    values = {v.name: v for v in get_device_type("emec_ld").values}
    await values["pH Relay Min Duty Cycle"].write(device, 5)
    assert pump.writes == [(87, 5), (85, 0)]
    with pytest.raises(ValueError):
        await values["pH Relay Max Duty Cycle"].write(device, 101)


@pytest.mark.parametrize("mode", ["ON/OFF", "Disabled"])
async def test_ph_relay_mode_switch_sets_the_keypad_defaults(
    make_pump, ldphcl_snapshot, mode
) -> None:
    """Seen on the keypad 2026-10-15: ON/OFF and Disabled set perc1 100 and perc2 0."""
    registers = dict(ldphcl_snapshot)
    registers.update({81: 655, 83: 650, 85: 0, 87: 17, 91: 0})
    pump = make_pump(registers)
    device = await read(pump)
    values = {v.name: v for v in get_device_type("emec_ld").values}
    await values["pH Relay Dosing Mode"].write(device, mode)
    assert pump.writes == [(85, 100), (87, 0), (91, {"ON/OFF": 1, "Disabled": 3}[mode])]


@pytest.mark.parametrize("mode", ["ON/OFF", "Disabled"])
async def test_cl_relay_mode_switch_sets_the_keypad_defaults(
    make_pump, ldphcl_snapshot, mode
) -> None:
    """The Cl relay follows the pH relay's rules, its mode at 40222."""
    registers = dict(ldphcl_snapshot)
    registers.update({211: 245, 213: 250, 215: 0, 217: 30, 221: 0})
    pump = make_pump(registers)
    device = await read(pump)
    values = {v.name: v for v in get_device_type("emec_ld").values}
    await values["Cl Relay Dosing Mode"].write(device, mode)
    assert pump.writes == [(215, 100), (217, 0), (221, {"ON/OFF": 1, "Disabled": 3}[mode])]


@pytest.mark.parametrize(
    ("word", "state"),
    [(0x0000, OutputState.DISABLED), (0x0100, OutputState.ON), (0x0200, OutputState.OFF)],
)
async def test_relay_state_is_the_high_byte(make_pump, ldphcl_snapshot, word, state) -> None:
    registers = dict(ldphcl_snapshot)
    registers[wire_address(15)] = word  # 40032, channel 1 relay
    device = await read(make_pump(registers))
    assert device.relay_ch1 is state


async def test_a_block_of_zeros_is_refused_and_the_values_stay(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)

    # As seen on the LDPHCL now and then for a few seconds: every value reads 0.
    pump.registers = {}
    with pytest.raises(ImplausibleReadError):
        await update_or_keep(device)

    assert device.ch1_value == pytest.approx(7.51)
    assert device.temperature == pytest.approx(17.6)
    assert device.relay_ch1 is OutputState.ON


@pytest.mark.parametrize(
    ("indices", "field", "kept", "zero"),
    [
        ((27, 28), "probe_mv_ch1", -27, 0),  # both probe voltages, seen 2026-10-06 10:25
        ((11, 15), "relay_ch1", OutputState.ON, OutputState.DISABLED),  # both relays
        ((25,), "temperature", 17.6, 0.0),
    ],
)
async def test_a_value_dropping_to_zero_needs_a_second_read(
    make_pump, ldphcl_snapshot, indices, field, kept, zero
) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    for index in indices:
        pump.registers[wire_address(index)] = 0

    with pytest.raises(ImplausibleReadError, match="dropped to 0"):
        await update_or_keep(device)
    assert (
        getattr(device, field) == pytest.approx(kept)
        if isinstance(kept, float)
        else getattr(device, field) == kept
    )
    assert device.ch1_value == pytest.approx(7.51)  # the rest of the read is kept too

    await update_or_keep(device)  # the next read shows 0 again: it is real
    assert getattr(device, field) == zero


async def test_a_one_poll_zero_never_shows(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    good = dict(pump.registers)
    pump.registers[wire_address(27)] = pump.registers[wire_address(28)] = 0
    with pytest.raises(ImplausibleReadError):
        await update_or_keep(device)
    pump.registers = good  # the next poll reads normally again
    await update_or_keep(device)
    assert (device.probe_mv_ch1, device.probe_mv_ch2) == (-27, 34)


async def test_zeros_on_channel_2_alone_are_refused(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    for index in (2, 3):  # 40006, 40008: channel 2's reading and divisor
        pump.registers[wire_address(index)] = 0

    with pytest.raises(ImplausibleReadError, match="ch2_divisor"):
        await update_or_keep(device)

    assert device.ch2_value == pytest.approx(0.46)  # the last good value stays


async def test_a_failed_request_leaves_the_values(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    pump._failing_reads = 1  # the next read gets no answer

    with pytest.raises(ModbusError):
        await update_or_keep(device)

    assert device.ch1_value == pytest.approx(7.51)


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
            {
                "pH Pulse 1 On Value",
                "pH Pulse 1 Off Value",
                "pH Pulse 1 On Rate",
                "pH Pulse 1 Off Rate",
            },
            {"pH Pulse 1 Pulse Speed"},
        ),
        (
            PulseMode.ON_OFF,
            {"pH Pulse 1 On Value", "pH Pulse 1 Off Value", "pH Pulse 1 Pulse Speed"},
            {"pH Pulse 1 On Rate", "pH Pulse 1 Off Rate"},
        ),
        (
            PulseMode.DISABLED,
            set(),
            {
                "pH Pulse 1 On Value",
                "pH Pulse 1 Off Value",
                "pH Pulse 1 On Rate",
                "pH Pulse 1 Off Rate",
                "pH Pulse 1 Pulse Speed",
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
        ("ph1_max", 9.9, [(67, 990)]),
        ("ph1_min", 7.55, [(69, 755)]),
        ("ph1_max_rate", 29, [(71, 29)]),  # min rate already 0
        ("ph1_min_rate", 5, [(73, 5), (71, 0)]),  # the other end goes to 0, as on the pump
        ("ph1_pulse_speed", 3, [(75, 3)]),
        # relay 2: the same layout at 40140-40150 (wire 139-149)
        ("ph2_max", 7.85, [(139, 785)]),
        ("ph2_min", 7.25, [(141, 725)]),
        ("ph2_max_rate", 150, [(143, 150)]),
        ("ph2_min_rate", 5, [(145, 5), (143, 0)]),
        ("ph2_pulse_speed", 3, [(147, 3)]),
        ("ph2_mode", "Proportional", [(149, 1)]),  # wait is already 0
        # Cl relay 1: 40154-40164 (wire 153-163), mg/l values
        ("cl1_max", 0.5, [(153, 50)]),
        ("cl1_min", 0.4, [(155, 40)]),
        ("cl1_max_rate", 12, [(157, 12)]),
        ("cl1_min_rate", 4, [(159, 4), (157, 0)]),
        ("cl1_mode", "Disabled", [(163, 2)]),
        ("feeding_delay", 4, [(521, 4)]),  # 40522
        ("ph_relay_on", 6.6, [(81, 660)]),  # 40082
        ("cl_relay_min_duty", 10, [(217, 10), (215, 0)]),  # 40218, the other one to 0
        ("cl_relay_mode", "ON/OFF", [(221, 1)]),  # 40222 (perc1 100 / perc2 0 already)
        ("tau", 10, [(519, 10)]),  # 40520
        ("flow_time", 10, [(523, 10)]),  # 40524
        ("flow_mode", "Reverse", [(525, 1)]),  # 40526
    ],
)
async def test_dosing_setting_writes(make_pump, ldphcl_snapshot, key, value, writes) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES[key].write(device, value)
    assert pump.writes == writes


async def test_a_write_answered_too_late_is_confirmed_by_reading(
    make_pump, ldphcl_snapshot
) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    pump.slow_write_reply = True  # stored, but the gateway reports exception 0x0B
    pump.failed_reads = 2  # and the next reads collide
    await WRITE_VALUES["ph1_min"].write(device, 7.55)
    assert pump.writes == [(69, 755)]
    assert device.ch1_pulse1_val2 == pytest.approx(7.55)


async def test_the_read_before_a_write_is_retried_once(
    make_pump, ldphcl_snapshot, monkeypatch
) -> None:
    from pool_modbus.devices import writing

    monkeypatch.setattr(writing, "READ_RETRY_DELAY", 0)
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    pump._failing_reads = 1  # the read ahead of the write gets no answer once
    await WRITE_VALUES["ph1_min"].write(device, 7.55)
    assert pump.writes == [(69, 755)]


async def test_a_write_the_pump_did_not_store_is_an_error(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    pump.lose_writes = True
    with pytest.raises(TimeoutError, match="still reads 7.5 .* after writing 7.55, which got no"):
        await WRITE_VALUES["ph1_min"].write(device, 7.55)
    assert pump.writes == [(69, 755)]


async def test_a_setting_the_pump_holds_is_not_written(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES["ph1_max"].write(device, 10.0)
    await WRITE_VALUES["ph1_max_rate"].write(device, 30)
    await WRITE_VALUES["ph1_mode"].write(device, "Proportional")
    assert pump.writes == []


@pytest.mark.parametrize(
    ("label", "writes"),
    [
        # rate 100 (min rate already 0), the 0 interval becomes 1 min, then the mode
        ("ON/OFF", [(71, 100), (75, 1), (77, 0)]),
        ("Disabled", [(77, 2)]),  # the interval is already 0
    ],
)
async def test_mode_switch_writes_its_settings_in_order(
    make_pump, ldphcl_snapshot, label, writes
) -> None:
    pump = make_pump(dict(ldphcl_snapshot))
    device = await read(pump)
    await WRITE_VALUES["ph1_mode"].write(device, label)
    assert pump.writes == writes
    assert device.ch1_pulse1_mode.label == label


async def test_off_zeroes_the_interval(make_pump, ldphcl_snapshot) -> None:
    registers = dict(ldphcl_snapshot)
    registers[75], registers[77] = 3, 0  # ON/OFF with a 3 min interval, as on the keypad
    pump = make_pump(registers)
    device = await read(pump)
    await WRITE_VALUES["ph1_mode"].write(device, "Disabled")
    assert pump.writes == [(75, 0), (77, 2)]


async def test_on_off_keeps_a_set_interval(make_pump, ldphcl_snapshot) -> None:
    registers = dict(ldphcl_snapshot)
    registers[75] = 20  # the user's interval stays, as on the keypad
    pump = make_pump(registers)
    device = await read(pump)
    await WRITE_VALUES["ph1_mode"].write(device, "ON/OFF")
    assert pump.writes == [(71, 100), (77, 0)]


async def test_back_to_proportional(make_pump, ldphcl_snapshot) -> None:
    registers = dict(ldphcl_snapshot)
    registers[75], registers[77] = 1, 0  # ON/OFF, one pulse a minute
    pump = make_pump(registers)
    device = await read(pump)
    await WRITE_VALUES["ph1_mode"].write(device, "Proportional")
    assert pump.writes == [(75, 0), (77, 1)]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ph1_max", 14.01),
        ("ph1_min", -0.1),
        ("ph1_max_rate", 181),
        ("ph1_min_rate", 2.5),
        ("ph1_pulse_speed", 100),
        ("ph1_mode", "Turbo"),
        ("cl1_max", 10.01),
        ("flow_time", 100),
        ("tau", 31),
        ("feeding_delay", 61),
        ("flow_mode", "Sideways"),
        ("cl1_mode", "Turbo"),
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
