# Contributing

Contributions are welcome, especially new device types and reports from
hardware other than the units already tested.

## Reporting a device

Open an issue with the device model, firmware version, how it is connected
(gateway, serial adapter), and the output of:

```bash
python -m pool_modbus read <device_type> --host <address> --unit <id>
```

Compare the values with the device's own display. Do not post passwords,
serial numbers or anything else private.

## Adding a device type

1. Write `src/pool_modbus/devices/<key>.py`:
   - a `modbus_connection.model.Component` with one field per register, using
     the right type (`integer`, `gauge`, `bits`, `enum`, `coil`, ...);
   - a `DEVICE_TYPE = DeviceType(...)` with the manufacturer, tested models,
     default Modbus ID and any bus requirements such as `message_spacing`;
   - its `values`: each is read fast, medium or slow by its category (status,
     measurement, setting or diagnostic) unless `scan_group` says otherwise,
     and may belong to an optional part (`feature`, listed in `features`) that
     users switch on or off;
   - an adapter like `EmecUnit` only if the device addresses registers in a
     non-standard way.
2. List it in `_TYPES` in `src/pool_modbus/devices/__init__.py`.
3. Add tests in `tests/` with a fake unit that answers the way the real
   device does, and a snapshot recorded from real hardware (strip anything
   private).
4. Document the register map in `docs/devices/<key>.md`: the model and
   firmware version tested, and every register the device type uses, as
   verified on hardware.

Start read-only. Writes come later, one setting at a time, each with tests.

## Versions

Versions follow Home Assistant's calendar scheme, `YEAR.MONTH.PATCH`, for
example `2026.10.0`. The version is set in `pyproject.toml`,
`src/pool_modbus/__init__.py` and `custom_components/pool_modbus/manifest.json`,
and changes only when a release is made.

## Code style

```bash
ruff check . && ruff format --check .
pytest
```

Both run in CI on every pull request.
