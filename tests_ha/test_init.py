"""Setting up devices and the entities they get."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pool_modbus.const import (
    CONF_DEVICE_TYPE,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    DOMAIN,
)

from .conftest import FakeUnit, fake_unit

TITLES = {
    "pool_controller": "Pool controller",
    "t010": "Pool thermostat",
    "emec_ld": "Dosing pump",
}
UNIT_IDS = {"pool_controller": 1, "t010": 2, "emec_ld": 3}


async def add(hass: HomeAssistant, device_type: str, unit: FakeUnit) -> MockConfigEntry:
    unit_id = UNIT_IDS[device_type]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TITLES[device_type],
        unique_id=f"{device_type}_tcp_192.168.1.50:502_{unit_id}",
        data={
            CONF_DEVICE_TYPE: device_type,
            CONF_TRANSPORT: "tcp",
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 502,
            CONF_UNIT_ID: unit_id,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.pool_modbus.async_get_unit", return_value=unit):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def state(hass: HomeAssistant, entity_id: str) -> str:
    current = hass.states.get(entity_id)
    assert current is not None, f"{entity_id} does not exist"
    return current.state


async def test_pool_controller_entities(hass: HomeAssistant) -> None:
    await add(hass, "pool_controller", fake_unit("pool_controller"))

    assert state(hass, "sensor.pool_controller_filtration_mode") == "Filtering"
    assert state(hass, "binary_sensor.pool_controller_filter_pump_running") == "on"
    assert state(hass, "binary_sensor.pool_controller_pool_filtration") == "on"
    assert state(hass, "sensor.pool_controller_water_level") == "Normal"
    assert state(hass, "sensor.pool_controller_saved_backwash_schedule") == "Friday 06:00"
    assert state(hass, "sensor.pool_controller_controller_last_update").startswith("2026-10-05T")


async def test_thermostat_entities_and_device(hass: HomeAssistant) -> None:
    entry = await add(hass, "t010", fake_unit("t010"))

    assert state(hass, "sensor.pool_thermostat_pool_temperature") == "19.0"
    assert state(hass, "climate.pool_thermostat_pool_thermostat") == "off"
    assert state(hass, "switch.pool_thermostat_pool_heating") == "off"
    assert state(hass, "number.pool_thermostat_set_temperature") == "20.0"
    assert state(hass, "binary_sensor.pool_thermostat_thermometer_alarm") == "off"

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, entry.unique_id)}
    assert (device.manufacturer, device.model, device.sw_version) == (
        "Optika ir technologija",
        "T010",
        "1.7",
    )


async def test_dosing_pump_entities(hass: HomeAssistant) -> None:
    await add(hass, "emec_ld", fake_unit("emec_ld"))

    assert state(hass, "sensor.dosing_pump_pool_ph_level") == "7.51"
    assert state(hass, "sensor.dosing_pump_pool_cl_level") == "0.46"
    assert state(hass, "sensor.dosing_pump_out_relay_ph") == "On"
    assert state(hass, "sensor.dosing_pump_ch1_ph_pulse1_mode") == "Proportional"
    # Only used in ON/OFF mode, so unavailable in proportional mode, as in the YAML setup.
    assert state(hass, "sensor.dosing_pump_pulse_speed") == STATE_UNAVAILABLE


async def test_entities_go_unavailable_when_the_device_stops_answering(
    hass: HomeAssistant,
) -> None:
    unit = fake_unit("t010")
    entry = await add(hass, "t010", unit)
    assert state(hass, "sensor.pool_thermostat_pool_temperature") == "19.0"

    unit.fail = TimeoutError("no answer")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert state(hass, "sensor.pool_thermostat_pool_temperature") == STATE_UNAVAILABLE


async def test_unload(hass: HomeAssistant) -> None:
    entry = await add(hass, "pool_controller", fake_unit("pool_controller"))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
