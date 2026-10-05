# pool-modbus

Pool equipment over Modbus, for Home Assistant and standalone Python.

Dosing controllers, thermostats and pool controllers often speak Modbus but
have no Home Assistant integration. This project models each device type as
typed Python (registers, scaling, byte layout) and tests it against data
recorded from real hardware, so the decoding is right once and stays right.

> **Status: early development.** The Python library reads EMEC LD
> controllers today. The Home Assistant integration is the next step; see the
> [roadmap](#roadmap).

## Supported devices

| Device type | Key | Status | Tested with |
|---|---|---|---|
| EMEC LD series pH/Cl controller | `emec_ld` | Library, read-only | LDPHCL, firmware 5.1.4 |
| T010 pool thermostat | | Planned | |
| Pool controller (filtration, cover, light, water level) | | Planned | |

Want your device here? See [CONTRIBUTING.md](CONTRIBUTING.md).

## How it works

- Every device is set up on its own: device type, transport, address and
  Modbus ID. Devices can share one gateway or use separate ones.
- Supported transports: **Modbus TCP**, **RTU over TCP**, **UDP** and
  **serial** (RS-485/RS-232, RTU or ASCII).
- Devices with identical connection settings share one connection. In Home
  Assistant this uses the shared Modbus connections introduced in 2026, so
  several integrations can use the same gateway without clashing.
- Each device type is a module in `src/pool_modbus/devices/`, built on
  [modbus-connection](https://github.com/home-assistant-libs/modbus-connection),
  Home Assistant's library for Modbus device models. A type can define
  variants for different firmware or protocol editions.

### EMEC LD controllers

These controllers are byte-addressed: each value takes two Modbus addresses and
sits at an odd one, and some values are a single byte in the high half of a
register. The vendor's register PDF gets several of these details wrong.
[docs/devices/emec_ld.md](docs/devices/emec_ld.md) has the verified register
map and the list of errors.

## Command line

Read a device and print its values (read-only):

```bash
pip install "pool-modbus[cli] @ git+https://github.com/Bl00d-B0b/pool-modbus"
python -m pool_modbus types
python -m pool_modbus read emec_ld --host 192.168.1.50 --unit 3
python -m pool_modbus read emec_ld --transport serial --serial-port /dev/ttyUSB0 --baudrate 38400 --unit 3
```

## Python

```python
from modbus_connection.pymodbus import ModbusConnection
from pool_modbus import ConnectionConfig, get_device_type

device_type = get_device_type("emec_ld")
connection = ModbusConnection(ConnectionConfig(host="192.168.1.50").params(), timeout=3)
await connection.connect()
pump = device_type.model(connection.for_unit(3))
await pump.async_update()
print(pump.ch1_value, pump.ch2_value, pump.pulse_rate_ch2)  # pH, Cl ppm, p/min
await connection.close()
```

## Roadmap

1. EMEC LD: register model and tests (done).
2. Home Assistant custom integration, installable through HACS: one entry per
   device, each with its own connection settings; read-only sensors first.
3. EMEC LD: alarm coils, relay and alarm settings, then writes.
4. T010 thermostat and pool controller device types.

## Development

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[test,cli]"
pytest
ruff check . && ruff format --check .
```

## Safety

This software talks to equipment that doses chemicals. It is read-only for
now. When write support arrives, test changes carefully and keep the
controller's own safety settings in place. You are responsible for your
installation.

This is an independent project, not affiliated with or endorsed by EMEC or
any other manufacturer named here. Product names belong to their owners.

## License

[Apache-2.0](LICENSE)
