"""Setting up devices and the entities they get."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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


async def add(
    hass: HomeAssistant,
    device_type: str,
    unit: FakeUnit,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    entry = make_entry(hass, device_type, options)
    await setup(hass, entry, unit)
    return entry


def make_entry(
    hass: HomeAssistant, device_type: str, options: dict[str, Any] | None = None
) -> MockConfigEntry:
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
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass: HomeAssistant, entry: MockConfigEntry, unit: FakeUnit) -> None:
    with patch("custom_components.pool_modbus.async_get_unit", return_value=unit):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str) -> str:
    current = hass.states.get(entity_id)
    assert current is not None, f"{entity_id} does not exist"
    return current.state


async def test_pool_controller_entities(hass: HomeAssistant) -> None:
    await add(hass, "pool_controller", fake_unit("pool_controller"))

    assert state(hass, "sensor.pool_controller_filtration_mode") == "Filtering"
    assert state(hass, "binary_sensor.pool_controller_filter_pump") == "on"
    assert state(hass, "switch.pool_controller_filtration") == "on"
    assert state(hass, "sensor.pool_controller_water_level") == "Normal"
    assert state(hass, "sensor.pool_controller_saved_backwash_schedule") == "Friday 06:00"
    assert state(hass, "sensor.pool_controller_rtc").startswith("2026-10-05T")


async def test_thermostat_entities_and_device(hass: HomeAssistant) -> None:
    entry = await add(hass, "t010", fake_unit("t010"))

    thermostat = hass.states.get("climate.pool_thermostat")
    assert thermostat.state == "off"
    assert thermostat.attributes["current_temperature"] == 19.0
    assert thermostat.attributes["heating_power"] == 0
    assert state(hass, "switch.pool_thermostat_heating_delay") == "off"
    assert state(hass, "number.pool_thermostat_delay_setting") == "3"
    assert state(hass, "binary_sensor.pool_thermostat_sensor_alarm") == "off"

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, entry.unique_id)}
    assert (device.manufacturer, device.model, device.sw_version) == (
        "Optika ir technologija",
        "T010",
        "1.7",
    )


async def test_dosing_pump_entities(hass: HomeAssistant) -> None:
    await add(hass, "emec_ld", fake_unit("emec_ld"))

    assert state(hass, "sensor.dosing_pump_ph_level") == "7.51"
    assert state(hass, "sensor.dosing_pump_cl_level") == "0.46"
    assert state(hass, "sensor.dosing_pump_ph_relay") == "On"
    assert state(hass, "select.dosing_pump_ph_dosing_mode") == "Proportional"
    # Only used in ON/OFF mode, so unavailable in proportional mode, as in the YAML setup.
    assert state(hass, "number.dosing_pump_ph_pulse_speed") == STATE_UNAVAILABLE


async def test_entity_ids_take_the_prefix_not_the_name(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Spa thermostat",
        unique_id="t010_tcp_192.168.1.50:502_2_spa",
        data={
            CONF_DEVICE_TYPE: "t010",
            CONF_TRANSPORT: "tcp",
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 502,
            CONF_UNIT_ID: 2,
            "prefix": "spa",
        },
    )
    entry.add_to_hass(hass)
    await setup(hass, entry, fake_unit("t010"))

    assert state(hass, "climate.pool_thermostat_spa") == "off"
    assert hass.states.get("climate.pool_thermostat_spa").name == "Spa thermostat"
    assert state(hass, "number.pool_thermostat_spa_temperature_offset") == "0.0"
    assert hass.states.get("climate.pool_thermostat") is None


async def test_entity_ids_leave_out_the_area(hass: HomeAssistant) -> None:
    """Home Assistant puts a device's area in front of the ids it makes up; the
    integration's ids are the device's name and the entity's only."""
    garden = ar.async_get(hass).async_create("Garden")
    for device_type in ("t010", "emec_ld"):
        entry = make_entry(hass, device_type)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.unique_id)}
        )
        dr.async_get(hass).async_update_device(device.id, area_id=garden.id)
        await setup(hass, entry, fake_unit(device_type))

    assert state(hass, "climate.pool_thermostat") == "off"
    assert hass.states.get("climate.pool_thermostat").name == "Pool thermostat"
    assert state(hass, "sensor.dosing_pump_ph_level") == "7.51"
    assert hass.states.get("sensor.dosing_pump_ph_level").name == "Dosing pump pH Level"
    assert not [e for e in hass.states.async_entity_ids() if "garden" in e]


async def test_renamed_keys_keep_their_entities(hass: HomeAssistant) -> None:
    entry = make_entry(hass, "pool_controller")
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{entry.unique_id}_sync_clock",
        config_entry=entry,
        suggested_object_id="controller_set_clock",
    )
    await setup(hass, entry, fake_unit("pool_controller"))

    moved = registry.async_get(old.entity_id)
    assert moved.unique_id == f"{entry.unique_id}_sync_rtc"
    assert hass.states.get(old.entity_id) is not None
    assert registry.async_get("button.pool_controller_sync_rtc") is None  # no duplicate


async def test_a_missed_poll_keeps_the_last_values(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    entry = await add(hass, "t010", unit)

    unit.fail = TimeoutError("no answer")  # the poll and its retry both fail
    await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()

    assert state(hass, "climate.pool_thermostat") == "off"
    assert state(hass, "number.pool_thermostat_delay_setting") == "3"


async def test_a_block_of_zeros_never_shows(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    entry = await add(hass, "emec_ld", unit)
    registers, unit.registers = unit.registers, {}  # the pump answers with zeros

    await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()

    assert state(hass, "sensor.dosing_pump_ph_level") == "7.51"
    assert state(hass, "sensor.dosing_pump_temperature") == "17.6"
    assert state(hass, "sensor.dosing_pump_ph_relay") == "On"

    unit.registers = registers
    await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()
    assert all(c.last_update_success for c in entry.runtime_data.coordinators.values())


async def test_a_value_dropping_to_zero_shows_from_the_second_read(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    entry = await add(hass, "emec_ld", unit)
    medium = entry.runtime_data.coordinators["medium"]
    unit.registers[55] = unit.registers[57] = 0  # both probe voltages, 40056 and 40058

    await medium.async_refresh()  # refused, not retried at once: the last values stay
    await hass.async_block_till_done()
    assert state(hass, "sensor.dosing_pump_ph_probe_voltage") == "-27"
    assert medium.last_update_success

    await medium.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor.dosing_pump_ph_probe_voltage") == "0"


async def test_entities_go_unavailable_when_the_device_stops_answering(
    hass: HomeAssistant, monkeypatch
) -> None:
    from custom_components.pool_modbus import coordinator

    monkeypatch.setattr(coordinator, "STALE_AFTER", 0)  # the device has been silent too long
    unit = fake_unit("t010")
    entry = await add(hass, "t010", unit)
    assert state(hass, "climate.pool_thermostat") == "off"

    unit.fail = TimeoutError("no answer")
    await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()

    assert state(hass, "climate.pool_thermostat") == STATE_UNAVAILABLE
    assert state(hass, "number.pool_thermostat_temperature_offset") == STATE_UNAVAILABLE


async def test_one_failed_read_is_tried_again(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    entry = await add(hass, "emec_ld", unit)

    # Another Modbus client's request collided with this one.
    unit.fail_once = TimeoutError("Modbus exception code 1")
    await entry.runtime_data.coordinators["fast"].async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinators["fast"].last_update_success
    assert state(hass, "sensor.dosing_pump_ph_relay") == "On"


async def test_the_icon_set_is_served(hass: HomeAssistant) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    # As on a real installation: the http and frontend integrations are up.
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.data[DATA_EXTRA_MODULE_URL] = MagicMock()

    await add(hass, "pool_controller", fake_unit("pool_controller"))

    [[static]] = hass.http.async_register_static_paths.call_args.args
    assert static.url_path == "/pool_modbus/pool_icons.js"
    assert static.path.endswith("pool_icons.js")
    [url] = [c.args[0] for c in hass.data[DATA_EXTRA_MODULE_URL].add.call_args_list]
    assert url.startswith("/pool_modbus/pool_icons.js?v=")
    assert hass.states.get("cover.pool_controller_cover").attributes["icon"] == "pool:cover-closed"


async def test_unload(hass: HomeAssistant) -> None:
    entry = await add(hass, "pool_controller", fake_unit("pool_controller"))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_each_scan_group_has_its_interval(hass: HomeAssistant) -> None:
    entry = await add(hass, "emec_ld", fake_unit("emec_ld"))
    intervals = {g: c.update_interval for g, c in entry.runtime_data.coordinators.items()}
    assert intervals == {
        "fast": timedelta(seconds=5),
        "medium": timedelta(seconds=10),
        "slow": timedelta(seconds=15),
    }


async def test_intervals_from_the_options(hass: HomeAssistant) -> None:
    options = {"scan_interval_fast": 2, "scan_interval_medium": 20, "scan_interval": 60}
    entry = await add(hass, "t010", fake_unit("t010"), options)
    coordinators = entry.runtime_data.coordinators
    assert coordinators["fast"].update_interval == timedelta(seconds=2)
    assert coordinators["slow"].update_interval == timedelta(seconds=60)


async def test_an_older_entry_keeps_its_interval_as_the_slow_one(hass: HomeAssistant) -> None:
    entry = await add(hass, "pool_controller", fake_unit("pool_controller"), {"scan_interval": 30})
    coordinators = entry.runtime_data.coordinators
    assert coordinators["slow"].update_interval == timedelta(seconds=30)
    assert coordinators["fast"].update_interval == timedelta(seconds=5)


async def test_optional_parts_are_on_by_default(hass: HomeAssistant) -> None:
    await add(hass, "emec_ld", fake_unit("emec_ld"))
    assert state(hass, "sensor.dosing_pump_ph_probe_voltage") == "-27"
    assert state(hass, "select.dosing_pump_ph_dosing_mode") == "Proportional"


async def test_an_optional_part_switched_off(hass: HomeAssistant) -> None:
    entry = await add(
        hass, "emec_ld", fake_unit("emec_ld"), {"read_probe_voltages": False, "read_clock": False}
    )
    assert hass.states.get("sensor.dosing_pump_ph_probe_voltage") is None
    assert hass.states.get("sensor.dosing_pump_clock") is None
    assert state(hass, "sensor.dosing_pump_ph_level") == "7.51"
    assert set(entry.runtime_data.coordinators) == {"fast", "medium", "slow"}  # dosing settings
