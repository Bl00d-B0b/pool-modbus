# pool-modbus

Pool equipment over Modbus, for Home Assistant and standalone Python.

Dosing controllers, thermostats and pool controllers often speak Modbus but
have no Home Assistant integration. This project models each device type as
typed Python (registers, scaling, byte layout) and tests it against data
recorded from real hardware, so the decoding is right once and stays right.

> **Status: early development.** The Python library and the Home Assistant
> integration read all three device types below and change the T010's
> settings; writing to the other two comes next. See the [roadmap](#roadmap).

## Supported devices

| Device type | Key | Status | Tested with |
|---|---|---|---|
| Pool controller (filtration, cover, light, water level) | `pool_controller` | Read-only | [register map](docs/devices/pool_controller.md) |
| T010 pool thermostat (Optika ir technologija) | `t010` | Read and write | Firmware 1.7, [register map](docs/devices/t010.md) |
| EMEC LD series pH/Cl controller | `emec_ld` | Read-only | LDPHCL, firmware 5.1.4, [register map](docs/devices/emec_ld.md) |

Each device type also defines the values a user sees, named like the matching
Home Assistant entities, and the rules that go with them (for example, which
dosing settings apply in which working mode).

Want your device here? See [CONTRIBUTING.md](CONTRIBUTING.md).

## How it works

- Every device is set up on its own: device type, transport, address and
  Modbus ID. Devices can share one gateway or use separate ones.
- Supported transports: **Modbus TCP**, **RTU over TCP**, **UDP** and
  **serial** (RS-485/RS-232, RTU or ASCII).
- Each device is read in as few requests as it allows: its own per-request
  limit is part of its device type and documented with its register map.
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
register. [docs/devices/emec_ld.md](docs/devices/emec_ld.md) has the register
map, tested on an LDPHCL with firmware 5.1.4.

## Home Assistant

Requires Home Assistant **2026.9** or later, which shares Modbus connections
between integrations. Tested with Home Assistant 2026.9.4.

1. In HACS, add `https://github.com/Bl00d-B0b/pool-modbus` as a custom
   repository of type *Integration*, install *Pool equipment* and restart
   Home Assistant.
2. Go to **Settings → Devices & services → Add integration** and pick *Pool
   equipment*.
3. Choose the device type and connection (Modbus TCP, RTU over TCP, UDP or
   serial), then the address and Modbus ID. The integration reads the device
   and checks it is the chosen type.
4. Choose how often it is read and which of its optional parts to read (below).
5. Repeat for each device. Devices behind the same gateway share one
   connection.

**Configure** on a device changes all of it later: the connection, the
address and Modbus ID, the intervals and the optional parts. A new address is
read before it is saved, and the device keeps its entities.

Values are read in three groups, each on its own interval, as in
solax-modbus. Each group reads only the registers of its own values, and a
device's Configure only shows the intervals of the groups it uses (the T010 and
the pool controller have no medium group).

| Group | Default | Values |
|---|---|---|
| Fast | 5 s | States and alarms, the thermostat |
| Medium | 10 s | Measurements: temperatures, pH, chlorine, pulse rates |
| Slow | 15 s | Settings, clocks, firmware |

Optional parts, each a checkbox:

| Device | Optional part | Default |
|---|---|---|
| Pool controller | Backwash schedule, clock | On |
| EMEC LD | Dosing settings (pH channel), probe voltages, clock | On |

Every value becomes an entity named as in the device's register map. Values
a device lets you change become switches and numbers, and the T010 also gets a
thermostat (climate) entity. Settings you can change are configuration
entities; read-only settings are diagnostic. For now only the T010 is
writable; see the [roadmap](#roadmap).

Before a write, the integration checks the value against the range the device
accepts and reads the device again; a value the device already holds is not
written. The T010 stores every write in its EEPROM, so this keeps automations
that repeat a setting from wearing it out. After a write, the integration reads
the device until it shows the new value, for up to 5 s, and reports an error if
it does not. Some Modbus TCP gateways answer reads from a cache: on the test
installation a written value showed in reads about 1 s later.

If you already read the same devices through Home Assistant's YAML `modbus:`
configuration, that hub keeps its own connection to the gateway. Both work,
but the gateway then has two clients, and some gateways answer the occasional
request with a timeout. Move a device to this integration and out of the YAML
at the same time.

## Command line

Read a device and print its values (read-only):

```bash
pip install "pool-modbus[cli] @ git+https://github.com/Bl00d-B0b/pool-modbus"
python -m pool_modbus types
python -m pool_modbus read t010 --host 192.168.1.50 --unit 2
python -m pool_modbus read emec_ld --transport serial --serial-port /dev/ttyUSB0 --baudrate 38400 --unit 3
```

Add `--raw` to print every register field instead of the user-facing values.

### Several devices

List the devices in a TOML file, one `[[device]]` table each, with their own
connection settings ([example](examples/devices.toml)), and read them all:

```bash
python -m pool_modbus read-config devices.toml
```

Devices with identical connection settings share one connection. A device that
does not answer is reported as unavailable, and the others are still read.
Output from a test installation, three devices behind one gateway:

```text
Pool controller (unit 1, tcp 192.168.1.50:502)
  Pool Filtration          on
  Block Filling Up         off
  Filtration Mode          Filtering
  Filter Pump Running      on
  Water Level              Normal
  Water Level Monitoring   on
  Pool Filling Up          off
  Pool Cover               closed
  Pool Light               off
  Room Flooding Alarm      off
  Backwash Day             Friday
  Backwash Time            06:00
  Saved Backwash Schedule  Friday 06:00
  Controller Last Update   2026-10-05 12:42:52

T010 pool thermostat (unit 2, tcp 192.168.1.50:502)
  Pool Thermostat         off, 19.0 °C, target 20.0 °C
  Pool Delaying           off
  Delay Time              00:00
  Offset Temperature      0.0 °C
  Set Delay Time          3 min
  Menu Mode               off
  Thermometer Alarm       off
  Short Connection Alarm  off
  EEPROM Alarm            off
  Thermostat Firmware     1.7

EMEC LD series pH/Cl controller (unit 3, tcp 192.168.1.50:502)
  Pool pH Level          7.52 pH
  Pool Cl Level          0.46 ppm
  Dispenser Temperature  19.2 °C
  Out Relay pH           On
  Out Relay Cl           On
  pH Pump Pulse Rate     0 p/min
  Cl Pump Pulse Rate     0 p/min
  pH Probe Voltage       -29 mV
  Cl Probe Voltage       34 mV
  Dispenser Last Update  2026-10-05 12:44:00
  Ch1 pH pulse1 Mode     Proportional
  pH Max Value           10.0 pH
  pH Min Value           7.5 pH
  Max Pulse Rate         30 p/min
  Min Pulse Rate         0 p/min
  Pulse Speed            unavailable
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

1. Read-only models and tests for the pool controller, T010 and EMEC LD (done).
2. Home Assistant custom integration, installable through HACS: one entry per
   device, each with its own connection settings (done).
3. Writes, one device at a time, each with tests: thermostat settings on the
   T010 (done), then switches and pulses on the pool controller, then dosing
   settings on the EMEC LD.
4. EMEC LD alarm coils.

## Development

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[test,cli]"
pytest
ruff check . && ruff format --check .
```

The integration under `custom_components/pool_modbus/` carries a copy of the
library in `library/`. After changing `src/pool_modbus`, run
`python script/vendor_library.py`; a test fails while the copy is out of date.

The integration's icon, in `custom_components/pool_modbus/brand/`, is drawn by
`python script/make_icon.py` (needs Pillow). Home Assistant 2026.9 and later
show it from there.

The integration's own tests need Home Assistant, which runs on Linux and macOS
but not Windows (CI runs them on every push):

```bash
pip install "pytest-homeassistant-custom-component==0.13.367" "modbus-connection[tmodbus]==4.10.0"
pytest tests_ha
```

## Safety

This software talks to equipment that doses chemicals, heats water and moves a
pool cover. It writes only the T010's thermostat settings for now. Test
changes carefully and keep each device's own safety settings in place. You are
responsible for your installation.

This is an independent project, not affiliated with or endorsed by EMEC or
any other manufacturer named here. Product names belong to their owners.

## License

[Apache-2.0](LICENSE)
