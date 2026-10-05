"""Adding a device through the UI."""

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

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pool thermostat"
    assert result["data"] == {
        CONF_DEVICE_TYPE: "t010",
        CONF_TRANSPORT: "tcp",
        CONF_HOST: "192.168.1.50",
        CONF_PORT: 502,
        CONF_UNIT_ID: 2,
    }
    assert result["result"].unique_id == "t010_tcp_192.168.1.50:502_2"


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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["baudrate"] == "38400"


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
