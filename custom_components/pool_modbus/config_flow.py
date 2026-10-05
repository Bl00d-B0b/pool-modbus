"""Config flow: one entry per device, each with its own connection settings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from modbus_connection import ModbusError

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_TYPE,
    CONF_PARITY,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import connection_config
from .library import DEVICE_TYPES, ConnectionConfig, DeviceType, Transport, get_device_type

BAUDRATES = ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]


def _number(minimum: int, maximum: int) -> vol.All:
    return vol.All(
        NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=minimum, max=maximum)),
        vol.Coerce(int),
    )


def _select(options: list[str], translation_key: str | None = None) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options, mode=SelectSelectorMode.DROPDOWN, translation_key=translation_key
        )
    )


USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_TYPE): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=key, label=device_type.name)
                    for key, device_type in DEVICE_TYPES.items()
                ],
                mode=SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_TRANSPORT, default=Transport.TCP.value): _select(
            [transport.value for transport in Transport], "transport"
        ),
    }
)


def unique_id(device_type: DeviceType, config: ConnectionConfig, unit_id: int) -> str:
    """One entry per device: its type, where it is reached, and its Modbus ID."""
    if config.transport is Transport.SERIAL:
        where = config.serial_port
    else:
        where = f"{config.host}:{config.port}"
    return f"{device_type.key}_{config.transport}_{where}_{unit_id}"


class PoolModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one device: choose its type and connection, then check it answers."""

    VERSION = 1

    def __init__(self) -> None:
        self._choice: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._choice = user_input
            if user_input[CONF_TRANSPORT] == Transport.SERIAL:
                return await self.async_step_serial()
            return await self.async_step_network()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)

    async def async_step_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        device_type = self._device_type()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=device_type.name): TextSelector(),
                vol.Required(CONF_HOST): TextSelector(),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): _number(1, 65535),
                vol.Required(CONF_UNIT_ID, default=device_type.default_unit_id): _number(1, 247),
            }
        )
        return await self._async_connection_step("network", schema, user_input)

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        device_type = self._device_type()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=device_type.name): TextSelector(),
                vol.Required(CONF_SERIAL_PORT): TextSelector(),
                vol.Required(CONF_BAUDRATE, default="9600"): _select(BAUDRATES),
                vol.Required(CONF_BYTESIZE, default="8"): _select(["7", "8"]),
                vol.Required(CONF_PARITY, default="N"): _select(["N", "E", "O"], "parity"),
                vol.Required(CONF_STOPBITS, default="1"): _select(["1", "2"]),
                vol.Required(CONF_UNIT_ID, default=device_type.default_unit_id): _number(1, 247),
            }
        )
        return await self._async_connection_step("serial", schema, user_input)

    def _device_type(self) -> DeviceType:
        return get_device_type(self._choice[CONF_DEVICE_TYPE])

    async def _async_connection_step(
        self, step_id: str, schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            data = {**self._choice, **user_input}
            name = data.pop(CONF_NAME)
            device_type = self._device_type()
            unit_id = int(data[CONF_UNIT_ID])
            config = connection_config(data)
            await self.async_set_unique_id(unique_id(device_type, config, unit_id))
            self._abort_if_unique_id_configured()
            try:
                params = config.params()
                async with async_get_temporary_unit(self.hass, params, unit_id) as unit:
                    device = device_type.model(unit)
                    await device.async_update()
            except ValueError as err:
                errors["base"] = "invalid_settings"
                placeholders["error"] = str(err)
            except (ModbusError, HomeAssistantError, OSError, TimeoutError) as err:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(err) or type(err).__name__
            else:
                if device_type.matches(device):
                    return self.async_create_entry(title=name, data=data)
                errors["base"] = "wrong_device"
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PoolModbusOptionsFlow:
        return PoolModbusOptionsFlow()


class PoolModbusOptionsFlow(OptionsFlow):
    """How often the device is read."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema({vol.Required(CONF_SCAN_INTERVAL, default=current): _number(5, 3600)})
        return self.async_show_form(step_id="init", data_schema=schema)
