"""Config flow: one entry per device, each with its own connection settings.

Adding a device asks for its type and connection, reads it to check it is that
type, then asks for the scan intervals and optional parts. Configure changes
all of that later, connection included; the entry keeps its unique ID, so its
entities keep their IDs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    DOMAIN,
    FEATURE_PREFIX,
    SCAN_INTERVAL_KEYS,
)
from .coordinator import connection_config, enabled_features, scan_interval
from .library import (
    DEVICE_TYPES,
    ConnectionConfig,
    DeviceType,
    Transport,
    get_device_type,
)

BAUDRATES = ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]
CONNECTION_KEYS = (
    CONF_TRANSPORT,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_STOPBITS,
    CONF_UNIT_ID,
)


def _number(minimum: int, maximum: int) -> vol.All:
    return vol.All(
        NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=minimum, max=maximum)),
        vol.Coerce(int),
    )


def _select(options: list[str], translation_key: str | None = None) -> SelectSelector:
    config = SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
    if translation_key is not None:
        # The selector schema rejects a translation_key of None.
        config["translation_key"] = translation_key
    return SelectSelector(config)


def _transport_select() -> SelectSelector:
    return _select([transport.value for transport in Transport], "transport")


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
        vol.Required(CONF_TRANSPORT, default=Transport.TCP.value): _transport_select(),
    }
)


def unique_id(device_type: DeviceType, config: ConnectionConfig, unit_id: int) -> str:
    """One entry per device: its type, where it is reached, and its Modbus ID."""
    if config.transport is Transport.SERIAL:
        where = config.serial_port
    else:
        where = f"{config.host}:{config.port}"
    return f"{device_type.key}_{config.transport}_{where}_{unit_id}"


def _identity(data: Mapping[str, Any]) -> str | None:
    """Which device an entry reaches; None for an entry without connection data."""
    try:
        device_type = get_device_type(data[CONF_DEVICE_TYPE])
        return unique_id(device_type, connection_config(data), int(data[CONF_UNIT_ID]))
    except (KeyError, ValueError):
        return None


def connection_schema(
    device_type: DeviceType, transport: str, current: Mapping[str, Any], *, with_name: bool
) -> vol.Schema:
    """The address (or serial line) and Modbus ID, prefilled from ``current``."""

    def required(key: str, default: Any = None) -> vol.Required:
        value = current.get(key, default)
        return vol.Required(key) if value is None else vol.Required(key, default=value)

    fields: dict[Any, Any] = {}
    if with_name:
        fields[required(CONF_NAME, device_type.name)] = TextSelector()
    if transport == Transport.SERIAL:
        fields[required(CONF_SERIAL_PORT)] = TextSelector()
        fields[required(CONF_BAUDRATE, "9600")] = _select(BAUDRATES)
        fields[required(CONF_BYTESIZE, "8")] = _select(["7", "8"])
        fields[required(CONF_PARITY, "N")] = _select(["N", "E", "O"], "parity")
        fields[required(CONF_STOPBITS, "1")] = _select(["1", "2"])
    else:
        fields[required(CONF_HOST)] = TextSelector()
        fields[required(CONF_PORT, DEFAULT_PORT)] = _number(1, 65535)
    fields[required(CONF_UNIT_ID, device_type.default_unit_id)] = _number(1, 247)
    return vol.Schema(fields)


def settings_schema(device_type: DeviceType, options: Mapping[str, Any]) -> vol.Schema:
    """The intervals of the scan groups the device type uses, and its optional parts."""
    fields: dict[Any, Any] = {
        vol.Required(SCAN_INTERVAL_KEYS[group], default=scan_interval(options, group)): _number(
            1, 3600
        )
        for group in device_type.scan_groups()
    }
    on = enabled_features(device_type, options)
    for feature in device_type.features:
        fields[vol.Required(FEATURE_PREFIX + feature.key, default=feature.key in on)] = (
            BooleanSelector()
        )
    return vol.Schema(fields)


async def validate_connection(
    hass: HomeAssistant, device_type: DeviceType, data: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Read the device with ``data``: the form's errors and placeholders, empty if it is fine."""
    unit_id = int(data[CONF_UNIT_ID])
    try:
        params = connection_config(data).params()
        async with async_get_temporary_unit(hass, params, unit_id) as unit:
            device = device_type.model(unit)
            await device.async_update()
    except ValueError as err:
        return {"base": "invalid_settings"}, {"error": str(err)}
    except (ModbusError, HomeAssistantError, OSError, TimeoutError) as err:
        return {"base": "cannot_connect"}, {"error": str(err) or type(err).__name__}
    if not device_type.matches(device):
        return {"base": "wrong_device"}, {"error": ""}
    return {}, {}


def _other_entry_reaches(hass: HomeAssistant, identity: str, exclude_entry_id: str | None) -> bool:
    return any(
        entry.entry_id != exclude_entry_id and _identity(entry.data) == identity
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


class PoolModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one device: its type and connection, then how it is read."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""

    def _device_type(self) -> DeviceType:
        return get_device_type(self._data[CONF_DEVICE_TYPE])

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data = dict(user_input)
            return await self.async_step_connection()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        device_type = self._device_type()
        transport = self._data[CONF_TRANSPORT]
        step_id = "serial" if transport == Transport.SERIAL else "network"
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            data = {**self._data, **user_input}
            title = data.pop(CONF_NAME)
            config = connection_config(data)
            identity = unique_id(device_type, config, int(data[CONF_UNIT_ID]))
            await self.async_set_unique_id(identity)
            self._abort_if_unique_id_configured()
            if _other_entry_reaches(self.hass, identity, None):
                return self.async_abort(reason="already_configured")
            errors, found = await validate_connection(self.hass, device_type, data)
            placeholders.update(found)
            if not errors:
                self._data, self._title = data, title
                return await self.async_step_settings()
        schema = connection_schema(device_type, transport, user_input or {}, with_name=True)
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    # The connection form has one step id per transport, so the strings can differ.
    async_step_network = async_step_connection
    async_step_serial = async_step_connection

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self._title, data=self._data, options=user_input)
        return self.async_show_form(
            step_id="settings", data_schema=settings_schema(self._device_type(), {})
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PoolModbusOptionsFlow:
        return PoolModbusOptionsFlow()


class PoolModbusOptionsFlow(OptionsFlow):
    """Configure: the connection, the scan intervals and the optional parts."""

    def __init__(self) -> None:
        self._transport = ""
        self._options: dict[str, Any] = {}

    def _device_type(self) -> DeviceType:
        return get_device_type(self.config_entry.data[CONF_DEVICE_TYPE])

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if user_input is not None:
            self._transport = user_input.pop(CONF_TRANSPORT)
            self._options = user_input
            return await self.async_step_connection()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TRANSPORT, default=entry.data[CONF_TRANSPORT]
                ): _transport_select(),
                **settings_schema(self._device_type(), entry.options).schema,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        device_type = self._device_type()
        step_id = "serial" if self._transport == Transport.SERIAL else "network"
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            data = {
                **{k: v for k, v in entry.data.items() if k not in CONNECTION_KEYS},
                CONF_TRANSPORT: self._transport,
                **user_input,
            }
            identity = _identity(data)
            if identity is not None and _other_entry_reaches(self.hass, identity, entry.entry_id):
                errors["base"] = "already_configured"
            else:
                errors, found = await validate_connection(self.hass, device_type, data)
                placeholders.update(found)
            if not errors:
                # Data and options in one update, so the entry reloads once.
                self.hass.config_entries.async_update_entry(entry, data=data, options=self._options)
                return self.async_create_entry(data=self._options)
        current = dict(entry.data) if entry.data[CONF_TRANSPORT] == self._transport else {}
        schema = connection_schema(
            device_type, self._transport, user_input or current, with_name=False
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async_step_network = async_step_connection
    async_step_serial = async_step_connection
