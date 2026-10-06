"""Adding a device through the UI, and changing it with Configure."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pool_modbus.const import (
    CONF_DEVICE_TYPE,
    CONF_PREFIX,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    DOMAIN,
)

from .conftest import FakeUnit, fake_unit

NETWORK = {CONF_NAME: "Pool thermostat", CONF_HOST: "192.168.1.50", CONF_PORT: 502, CONF_UNIT_ID: 2}


def temporary_unit(unit: FakeUnit):
    @asynccontextmanager
    async def get(hass, params, unit_id):
        yield unit

    return get


async def start(hass: HomeAssistant, device_type: str = "t010", transport: str = "tcp"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TYPE: device_type, CONF_TRANSPORT: transport}
    )


async def finish(hass: HomeAssistant, flow_id: str, unit: FakeUnit, user_input: dict):
    with (
        patch(
            "custom_components.pool_modbus.config_flow.async_get_temporary_unit",
            temporary_unit(unit),
        ),
        patch("custom_components.pool_modbus.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(flow_id, user_input)


async def test_add_t010_over_tcp(hass: HomeAssistant) -> None:
    result = await start(hass)
    assert result["step_id"] == "network"

    result = await finish(hass, result["flow_id"], fake_unit("t010"), NETWORK)
    assert result["step_id"] == "settings"  # the device answered; now how to read it
    result = await finish(
        hass,
        result["flow_id"],
        fake_unit("t010"),
        {"scan_interval_fast": 5, "scan_interval": 15},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pool thermostat"
    assert result["data"] == {
        CONF_DEVICE_TYPE: "t010",
        CONF_TRANSPORT: "tcp",
        CONF_HOST: "192.168.1.50",
        CONF_PORT: 502,
        CONF_UNIT_ID: 2,
        CONF_PREFIX: "",
    }
    assert result["result"].unique_id == "t010_tcp_192.168.1.50:502_2"
    assert result["options"] == {"scan_interval_fast": 5, "scan_interval": 15}


async def add_with_prefix(hass: HomeAssistant, prefix: str):
    result = await start(hass)
    result = await finish(
        hass, result["flow_id"], fake_unit("t010"), {**NETWORK, CONF_PREFIX: prefix}
    )
    if result["step_id"] != "settings":
        return result
    return await finish(hass, result["flow_id"], fake_unit("t010"), {"scan_interval_fast": 5})


async def test_the_same_device_again_with_a_prefix(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="t010_tcp_192.168.1.50:502_2", data={CONF_DEVICE_TYPE: "t010"}
    ).add_to_hass(hass)

    result = await add_with_prefix(hass, "Spa 1")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PREFIX] == "spa_1"
    assert result["result"].unique_id == "t010_tcp_192.168.1.50:502_2_spa_1"


async def test_a_prefix_another_device_of_the_type_has_is_refused(hass: HomeAssistant) -> None:
    # another T010, elsewhere, already without a prefix
    MockConfigEntry(
        domain=DOMAIN, unique_id="t010_tcp_192.168.1.60:502_2", data={CONF_DEVICE_TYPE: "t010"}
    ).add_to_hass(hass)

    result = await add_with_prefix(hass, "")

    assert result["errors"] == {CONF_PREFIX: "prefix_taken"}


async def test_a_prefix_with_other_characters_is_refused(hass: HomeAssistant) -> None:
    result = await add_with_prefix(hass, "spa!")

    assert result["errors"] == {CONF_PREFIX: "invalid_prefix"}


async def test_serial_asks_for_line_settings(hass: HomeAssistant) -> None:
    result = await start(hass, "emec_ld", "serial")
    assert result["step_id"] == "serial"

    result = await finish(
        hass,
        result["flow_id"],
        fake_unit("emec_ld"),
        {
            CONF_NAME: "Dosing pump",
            "serial_port": "/dev/ttyUSB0",
            "baudrate": "38400",
            "bytesize": "8",
            "parity": "N",
            "stopbits": "1",
            CONF_UNIT_ID: 3,
        },
    )
    assert result["step_id"] == "settings"
    result = await finish(hass, result["flow_id"], fake_unit("emec_ld"), {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["baudrate"] == "38400"
    # The pump's optional parts are all on unless switched off.
    assert result["options"]["read_dosing_settings"] is True


async def test_wrong_device_type(hass: HomeAssistant) -> None:
    result = await start(hass)
    unit = fake_unit("t010")
    unit.registers[0] = 0x2011  # not a T010

    result = await finish(hass, result["flow_id"], unit, NETWORK)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_device"}


async def test_device_does_not_answer(hass: HomeAssistant) -> None:
    result = await start(hass)
    unit = fake_unit("t010")
    unit.fail = TimeoutError("no answer")

    result = await finish(hass, result["flow_id"], unit, NETWORK)

    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_device_twice(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="t010_tcp_192.168.1.50:502_2").add_to_hass(hass)
    result = await start(hass)

    result = await finish(hass, result["flow_id"], fake_unit("t010"), NETWORK)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_another_device_of_a_type_is_offered_a_numbered_name(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, title="Pool thermostat", data={CONF_DEVICE_TYPE: "t010"}
    ).add_to_hass(hass)
    result = await start(hass)

    [name] = [key for key in result["data_schema"].schema if key == CONF_NAME]
    assert name.default() == "Pool thermostat 2"


# -- Configure ------------------------------------------------------------------

DATA = {
    CONF_DEVICE_TYPE: "t010",
    CONF_TRANSPORT: "tcp",
    CONF_HOST: "192.168.1.50",
    CONF_PORT: 502,
    CONF_UNIT_ID: 2,
}
SETTINGS = {
    CONF_TRANSPORT: "tcp",
    "scan_interval_fast": 3,
    "scan_interval": 30,
}


def existing(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pool thermostat",
        unique_id="t010_tcp_192.168.1.50:502_2",
        data=data or DATA,
    )
    entry.add_to_hass(hass)
    return entry


async def configure(hass: HomeAssistant, entry: MockConfigEntry, unit: FakeUnit, address: dict):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], SETTINGS)
    assert result["step_id"] == "network"
    with patch(
        "custom_components.pool_modbus.config_flow.async_get_temporary_unit",
        temporary_unit(unit),
    ):
        return await hass.config_entries.options.async_configure(result["flow_id"], address)


async def test_configure_changes_the_connection_and_reading(hass: HomeAssistant) -> None:
    entry = existing(hass)

    result = await configure(
        hass,
        entry,
        fake_unit("t010"),
        {CONF_HOST: "192.168.1.60", CONF_PORT: 5020, CONF_UNIT_ID: 7},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data == {**DATA, CONF_HOST: "192.168.1.60", CONF_PORT: 5020, CONF_UNIT_ID: 7}
    assert entry.options == {k: v for k, v in SETTINGS.items() if k != CONF_TRANSPORT}
    # The entry keeps its unique ID, so its entities keep their IDs.
    assert entry.unique_id == "t010_tcp_192.168.1.50:502_2"


async def test_configure_shows_the_current_address(hass: HomeAssistant) -> None:
    entry = existing(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], SETTINGS)

    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if callable(key.default)
    }
    assert defaults[CONF_HOST] == "192.168.1.50"
    assert defaults[CONF_UNIT_ID] == 2


async def test_configure_checks_the_device_answers(hass: HomeAssistant) -> None:
    entry = existing(hass)
    unit = fake_unit("t010")
    unit.fail = TimeoutError("no answer")

    result = await configure(
        hass, entry, unit, {CONF_HOST: "192.168.1.60", CONF_PORT: 502, CONF_UNIT_ID: 2}
    )

    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == DATA


async def test_configure_refuses_a_device_another_entry_reaches(hass: HomeAssistant) -> None:
    entry = existing(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="t010_tcp_192.168.1.60:502_2",
        data={**DATA, CONF_HOST: "192.168.1.60"},
    ).add_to_hass(hass)

    result = await configure(
        hass, entry, fake_unit("t010"), {CONF_HOST: "192.168.1.60", CONF_PORT: 502, CONF_UNIT_ID: 2}
    )

    assert result["errors"] == {"base": "already_configured"}
    assert entry.data == DATA


async def test_settings_show_only_the_intervals_a_device_uses(hass: HomeAssistant) -> None:
    for device_type, expected in (
        ("t010", ["scan_interval_fast", "scan_interval"]),
        ("emec_ld", ["scan_interval_fast", "scan_interval_medium", "scan_interval"]),
    ):
        result = await start(hass, device_type)
        result = await finish(
            hass,
            result["flow_id"],
            fake_unit(device_type),
            {
                CONF_NAME: device_type,
                CONF_HOST: "192.168.1.70",
                CONF_PORT: 502,
                CONF_UNIT_ID: {"t010": 2, "emec_ld": 3}[device_type],
            },
        )
        fields = [str(key) for key in result["data_schema"].schema]
        assert [f for f in fields if f.startswith("scan_interval")] == expected
