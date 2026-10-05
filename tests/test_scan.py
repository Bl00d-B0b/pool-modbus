"""Scan groups: which registers each group reads, with the optional parts switched on or off."""

from __future__ import annotations

import pytest

from pool_modbus import DEVICE_TYPES, read_plan
from pool_modbus.devices import get_device_type
from pool_modbus.devices.emec_ld import EmecLD
from pool_modbus.devices.pool_controller import PoolController
from pool_modbus.devices.scan import group_model, value_fields
from pool_modbus.devices.t010 import T010

MODELS = {"pool_controller": PoolController, "t010": T010, "emec_ld": EmecLD}


@pytest.mark.parametrize("key", sorted(DEVICE_TYPES))
def test_every_value_reads_known_fields(key: str) -> None:
    model = MODELS[key]
    for value in DEVICE_TYPES[key].values:
        fields = value_fields(model, value)
        assert fields, value.name
        assert fields <= set(model.declared_fields), value.name


def test_t010_groups() -> None:
    plan = read_plan(get_device_type("t010"), T010, set())
    # The thermostat is fast: its mode, action, target and current temperature.
    assert {"heating_blocked", "heating_power", "setpoint", "temperature"} <= plan["fast"]
    assert plan["medium"] == {"temperature"}
    assert plan["slow"] == {"offset", "set_delay", "menu_mode", "software_version_raw"}


def test_emec_optional_parts() -> None:
    device_type = get_device_type("emec_ld")
    everything = read_plan(device_type, EmecLD, device_type.default_features())
    assert {"probe_mv_ch1", "probe_mv_ch2"} <= everything["medium"]
    assert "ch1_pulse_mode" in everything["slow"]

    bare = read_plan(device_type, EmecLD, set())
    assert not {"probe_mv_ch1", "probe_mv_ch2"} & bare["medium"]
    assert "slow" not in bare  # dosing settings and the clock are its only slow values
    assert bare["fast"] == {"relay_ch1_raw", "relay_ch2_raw"}


async def test_a_group_reads_only_its_registers(make_pump, ldphcl_snapshot) -> None:
    device_type = get_device_type("emec_ld")
    plan = read_plan(device_type, EmecLD, device_type.default_features())
    pump = make_pump(ldphcl_snapshot)
    fast = group_model(device_type, pump, plan["fast"])

    await fast.async_update()

    assert pump.requests == [("holding", 23, 5)]  # 40024-40032: the two relay states
    assert fast.relay_ch1 is not None and fast.ch1_value is None


async def test_t010_fast_group_is_one_request(make_unit, t010_snapshot) -> None:
    device_type = get_device_type("t010")
    unit = make_unit(t010_snapshot)
    fast = group_model(device_type, unit, read_plan(device_type, T010, set())["fast"])

    await fast.async_update()

    assert len(unit.requests) == 1
    assert fast.hvac_mode == "off" and fast.setpoint == pytest.approx(20.0)
    assert fast.offset is None  # a slow-group setting


def test_controller_optional_parts() -> None:
    device_type = get_device_type("pool_controller")
    bare = read_plan(device_type, PoolController, set())
    assert "slow" not in bare
    assert {"pump_running", "pool_open"} <= bare["fast"]
