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
    # values 0-109 (up to the Cl relay) and 259-262 (timing) in two reads; the alarm coils in a
    # read of 16, the priority coil from a read of 128
    assert sorted(pump.requests) == [
        ("coils", 0, 16),
        ("coils", 0, 128),
        ("holding", 1, 110),
        ("holding", 519, 4),
    ]
    assert pump.message_spacing == pytest.approx(0.1)


async def test_alarm_coils(make_pump, ldphcl_snapshot) -> None:
    """Coil 11 = standby, coil 6 = no flow (both seen live), numbered from 1."""
    device = await read(make_pump(ldphcl_snapshot, coils={10: True}))
    assert device.standby is True
    assert device.no_flow is False
    assert device.cl_tank_empty is False
    assert device.active_alarm_coils == "11"
    device = await read(make_pump(ldphcl_snapshot, coils={5: True, 6: True, 7: True}))
    assert device.no_flow is True
    assert device.cl_tank_empty is True
    assert device.ph_tank_empty is True
    assert device.active_alarm_coils == "6, 7, 8"
    device = await read(make_pump(ldphcl_snapshot))
    assert device.active_alarm_coils == "None"


async def test_coils_are_always_read_as_the_whole_block(make_pump, ldphcl_snapshot) -> None:
    pump = make_pump(ldphcl_snapshot, coils={10: True})
    unit = EmecUnit(pump)
    assert await unit.read_coils(10, 1) == [True]
    assert await unit.read_coils(4, 3) == [False, False, False]
    assert pump.requests == [("coils", 0, 16), ("coils", 0, 16)]
    with pytest.raises(ValueError):
        await unit.read_coils(16, 1)
    with pytest.raises(NotImplementedError):
        await unit.write_coil(10, True)  # only the tested configuration coil


async def test_usa_units_format(make_pump, ldphcl_snapshot) -> None:
    """Seen live 2026-10-14: in USA format the temperature reads whole °F (60 for the
    display's 60.2 °F) and the date word swaps day and month (0x0E0A for 14 October)."""
    registers = dict(ldphcl_snapshot)
    registers[51] = 60  # 40052
    registers[43] = 0x0E0A  # 40044: day 14 high, month 10 low
    registers[45] = (7 << 8) | 26  # 40046: hour 7 (12-hour, set in USA format), year 26
    registers[47] = 13  # 40048: minute
    pump = make_pump(registers, coils={47: True, 46: True})  # USA, PM
    device = await read(pump)
    values = {v.key: v for v in get_device_type("emec_ld").values}
    assert device.temperature is None
    assert device.temperature_f == 60
    assert device.clock == datetime(2026, 10, 14, 19, 13)
    assert value_text(values["units_format"], device) == "USA"
    assert values["units_format"].write is None  # switched on the keypad only
    assert value_text(values["temperature_f"], device) == "60 °F"
    assert value_text(values["temperature"], device) == "unavailable"


async def test_europe_units_format(make_pump, ldphcl_snapshot) -> None:
    """Europe format: °C × 10, month in the date word's high byte, a 24-hour hour that the
    PM flag does not change (seen live 2026-10-14: hour 19 with PM on, and with PM off)."""
    registers = dict(ldphcl_snapshot)
    registers[51] = 157  # 40052
    registers[43] = 0x0A0E  # 40044: month 10 high, day 14 low
    registers[45] = (9 << 8) | 26  # 40046: hour 9, year 26
    registers[47] = 5  # 40048: minute
    for pm in (False, True):
        device = await read(make_pump(registers, coils={47: False, 46: pm}))
        values = {v.key: v for v in get_device_type("emec_ld").values}
        assert device.clock == datetime(2026, 10, 14, 9, 5)
        assert value_text(values["units_format"], device) == "Europe"
        assert value_text(values["temperature"], device) == "15.7 °C"
        assert value_text(values["temperature_f"], device) == "unavailable"


@pytest.mark.parametrize(
    ("hour", "pm", "expected"),
    [(9, False, 9), (12, False, 0), (12, True, 12), (7, True, 19), (11, True, 23)],
)
async def test_usa_clock_hours(make_pump, ldphcl_snapshot, hour, pm, expected) -> None:
    registers = dict(ldphcl_snapshot)
    registers[43], registers[45], registers[47] = 0x0E0A, (hour << 8) | 26, 0
    coils = {47: True, 46: pm, 45: not pm}
    device = await read(make_pump(registers, coils=coils))
    assert device.clock == datetime(2026, 10, 14, expected, 0)


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
    for name in ("Max Value", "Min Value", "Max Rate", "Min Rate", "Pulse Speed"):
        assert texts[f"pH Pulse 2 {name}"] == "unavailable"
        assert values[f"pH Pulse 2 {name}"].writable
    assert {v.key for v in values.values() if v.name.startswith("pH Pulse 2")} == {
        f"ph2_{k}" for k in ("mode", "max", "min", "max_rate", "min_rate", "pulse_speed")
    } | {"pulse_rate_ph_2"}

    registers[wire_address(74)] = PulseMode.PROPORTIONAL  # 40150
    device = await read(make_pump(registers))
    texts = {name: value_text(values[name], device) for name in values}
    assert texts["pH Pulse 2 Max Value"] == "7.8 pH"
    assert texts["pH Pulse 2 Min Value"] == "7.3 pH"
    assert texts["pH Pulse 2 Max Rate"] == "180 p/min"
    assert texts["pH Pulse 2 Min Rate"] == "0 p/min"
    assert texts["pH Pulse 2 Pulse Speed"] == "unavailable"


async def test_switching_relays(make_pump, ldphcl_snapshot) -> None:
    """CH1 PH RELAY 40082-40090 and CH2 CL2 RELAY 40212-40220, as read on 2026-10-14:
    Proportional PWM between 6.55 (100 %) and 6.50 (0 %) pH, 2.45 and 2.50 mg/l."""
    registers = dict(ldphcl_snapshot)
    registers.update({81: 655, 83: 650, 85: 100, 87: 0, 89: 0})
    registers.update({211: 245, 213: 250, 215: 100, 217: 0, 219: 0})
    device = await read(make_pump(registers))
    values = {v.name: v for v in get_device_type("emec_ld").values}
    texts = {name: value_text(values[name], device) for name in values}
    assert texts["pH Relay Dosing Mode"] == "Proportional PWM"
    assert texts["pH Relay Max Value"] == "6.55 pH"
    assert texts["pH Relay Min Value"] == "6.5 pH"
    assert texts["pH Relay Max Duty Cycle"] == "100 %"
    assert texts["Cl Relay Min Value"] == "2.5 mg/L"
    assert texts["pH Relay State"] == "On"

    registers[89] = 1  # ON/OFF: no duty cycles
    device = await read(make_pump(registers))
    assert value_text(values["pH Relay Max Duty Cycle"], device) == "unavailable"
    assert value_text(values["pH Relay Max Value"], device) == "6.55 pH"
    registers[89] = 3  # Disabled: no values either
    device = await read(make_pump(registers))
    assert value_text(values["pH Relay Max Value"], device) == "unavailable"


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
                "pH Pulse 1 Max Value",
                "pH Pulse 1 Min Value",
                "pH Pulse 1 Max Rate",
                "pH Pulse 1 Min Rate",
            },
            {"pH Pulse 1 Pulse Speed"},
        ),
        (
            PulseMode.ON_OFF,
            {"pH Pulse 1 Max Value", "pH Pulse 1 Min Value", "pH Pulse 1 Pulse Speed"},
            {"pH Pulse 1 Max Rate", "pH Pulse 1 Min Rate"},
        ),
        (
            PulseMode.DISABLED,
            set(),
            {
                "pH Pulse 1 Max Value",
                "pH Pulse 1 Min Value",
                "pH Pulse 1 Max Rate",
                "pH Pulse 1 Min Rate",
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
        ("ph_relay_max", 6.6, [(81, 660)]),  # 40082
        ("cl_relay_min_duty", 10, [(217, 10)]),  # 40218
        ("cl_relay_mode", "ON/OFF", [(219, 1)]),  # 40220
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
