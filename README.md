# pool-modbus

Pool equipment over Modbus, for Home Assistant and standalone Python.

Dosing controllers, thermostats and pool controllers often speak Modbus but
have no Home Assistant integration. This project models each device type as
typed Python (registers, scaling, byte layout) and tests it against data
recorded from real hardware, so the decoding is right once and stays right.

> **Status: early development.** The Python library and the Home Assistant
> integration read all three device types below. They control the T010, the
> pool controller and most of the EMEC LD's settings: its dosing outputs and
> relays, flow, alarm, mA output and data logging settings and its PIN. See the
> [roadmap](#roadmap).

## Supported devices

| Device type | Key | Status | Tested with |
|---|---|---|---|
| Pool controller (filtration, cover, light, water level) | `pool_controller` | Read and write | [register map](docs/devices/pool_controller.md) |
| T010 pool thermostat (Optika ir technologija) | `t010` | Read and write | Firmware 1.7, [register map](docs/devices/t010.md) |
| EMEC LD series pH/Cl controller | `emec_ld` | Read and write (dosing outputs and relays, flow, alarm, mA output and logging settings, PIN) | LDPHCL, firmware 5.1.4, [register map](docs/devices/emec_ld.md) |

Each device type also defines the values a user sees, with generic names, and
the rules that go with them (for example, which dosing settings apply in which
working mode). In Home Assistant the names follow the device's name, e.g.
"Thermostat Heating Delay"; a new device is offered "Controller", "Thermostat"
or "Dosing pump" as its name, and both can be renamed.

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
between integrations. Tested with Home Assistant 2026.10.1 (and 2026.9.4 before it).

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

A new device is offered Modbus ID 1. The integration depends on Home
Assistant's built-in Modbus integration for its connections, but needs no
`modbus:` YAML configuration.

### Updating

HACS shows a new version when one is released on the repository's GitHub
releases page. Update it in HACS and restart Home Assistant.

### Removing

Delete each device under **Settings → Devices & services → Pool equipment**,
then remove *Pool equipment* in HACS and restart Home Assistant.

### Configuring

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

A read that fails is tried once more after 0.5 s. If that fails too, the
group's entities keep their last values instead of turning unavailable, so a
missed poll does not cut their history and statistics; only when a group has
had no good read for 60 s do its entities show unavailable. A device type can
also refuse a read it knows to be wrong (the EMEC LD sometimes answers with
zeros); a refused read is not retried at once but at the next poll, and a
refused or failed read never changes the values shown.

Optional parts, each a checkbox:

| Device | Optional part | Default |
|---|---|---|
| Pool controller | Backwash schedule, clock | On |
| EMEC LD | Dosing settings (pH and Cl pulse outputs, timing), alarms and standby, mA outputs, data logging, label and PIN, probe voltages, clock | On |

Every value becomes an entity named as in the device's register map. Values
a device lets you change become switches, numbers, selects, times, texts or
lights; the T010 also gets a thermostat (climate) entity, and the pool
controller a cover and buttons for its commands. Settings you can change are
configuration entities, except the dosing settings of the EMEC LD's five
outputs (pH Pulse 1 and 2, Cl Pulse, pH and Cl Relay), which are shown among
the controls. Read-only settings, clocks, firmware versions, the EMEC LD's
label and PIN, its alarms other than Standby, and the buttons that set the
clock or reset alarms are diagnostic. Probe voltages are plain sensors. The
EMEC LD's RTC and PIN are created disabled. The integration brings its own
icon set, `pool:`, for what Material Design Icons lack: the pool cover, a terrace
that slides over a pool sunk into the ground (`pool:cover-open`,
`pool:cover-closed`, drawn by `script/make_pool_icons.py`). The pool light is
switched as the controller allows: a toggle the controller does not carry out
leaves the light as it is, without an error.

Entity ids start with the device type (`pool_controller`, `pool_thermostat`,
`dosing_pump`), then the device's entity id prefix if it has one, then the
entity's name: `number.pool_thermostat_temperature_offset`, or with prefix `2`,
`number.pool_thermostat_2_temperature_offset`. The thermostat, the T010's main
entity, has no name of its own (`climate.pool_thermostat`,
`climate.pool_thermostat_2`). The prefix is asked for when a device is added:
optional, letters, digits, spaces, `-` and `_` (stored in lower case, with
spaces and `-` as `_`), and different for each device of a type
(only one of them can have none). It is part of the device's identity, so it
cannot be changed later, and the same device can be added a second time with a
prefix of its own. The device's name is only what is shown: entities are named
"<device name> <entity name>". The device's area is never part of an id,
although Home Assistant puts it in front of the ids it makes up by default, so
moving a device to another area changes no id. The clocks are named as in other
integrations: RTC, and Sync RTC on the pool controller to set it.

Before a write, the integration checks the value against the range the device
accepts and reads the device again; a value the device already holds is not
written. The T010 stores every write in its EEPROM, so this keeps automations
that repeat a setting from wearing it out. After a write, the integration reads
the device until it shows the new value, for up to 5 s, and reports an error if
it does not. Some Modbus TCP gateways answer reads from a cache: on the test
installation a written value showed in reads about 1 s later. A write that gets
no reply (exception 0x0B from a gateway, or a timeout) is not reported as an
error if the device then reads back the new value; the EMEC LD answers writes
too late for its gateway.

If you already read the same devices through Home Assistant's YAML `modbus:`
configuration, that hub keeps its own connection to the gateway. Both work,
but the gateway then has two clients, and their requests can collide: a device
may answer one of them with an error or a timeout. The integration tries a
failed read once more before it marks entities unavailable. Move a device to
this integration and out of the YAML at the same time.

### Debugging the bus

Every request is logged at debug level, with its time, Modbus ID, function,
address and the request and reply frames in RTU form (built from the request and
the values of the answer, the bytes a TCP gateway puts on the bus), or the error.
Turn it on with **Enable debug logging** on the integration's page.

To catch a failure that comes rarely, switch on **Log the bus traffic around
failures** in a device's Configure. The integration keeps the last 300 requests
of each connection in memory, those of every device on it in the order they went
out. When 5 requests in a row fail, it writes them to the log as one warning;
when a device answers again, a second warning has the last failures, the first
answer and the 10 requests after it. A single failure (a late answer, a write the
gateway gave up on) writes nothing. Home Assistant's system log keeps only the
last few messages of a kind, so download the full log (**Settings → System →
Logs**) to keep them.

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
An excerpt of the output from a test installation, three devices behind one
gateway, with most lines left out (the full output lists every value of each
device):

```text
Pool controller (unit 1, tcp 192.168.1.50:502)
  Filtration               on
  Filtration Mode          Filtering
  Water Level              Normal
  Light                    off
  Backwash Friday          on
  Backwash Time            06:00
  Saved Backwash Schedule  Friday 06:00
  Cover                    closed

T010 pool thermostat (unit 2, tcp 192.168.1.50:502)
  Thermostat           off, 19.0 °C, target 20.0 °C
  Heating Delay        off
  Delay Remaining      00:00
  Temperature Offset   0.0 °C
  Delay Setting        3 min
  Menu Mode            off
  Sensor Alarm         off
  Sensor Supply Alarm  off
  EEPROM Alarm         off
  Firmware             1.7

EMEC LD series pH/Cl controller (unit 3, tcp 192.168.1.50:502)
  pH Level                 7.51 pH
  Cl Level                 0.46 mg/L
  Temperature              17.6 °C
  pH Relay State           On
  pH Pulse 1 Rate          0 p/min
  Cl Pulse Rate            0 p/min
  pH Pulse 1 Dosing Mode   Proportional
  pH Pulse 1 On Value      10.0 pH
  pH Pulse 1 Off Value     7.5 pH
  pH Pulse 1 On Rate       30 p/min
  pH Pulse 1 Pulse Speed   unavailable
  pH Relay Dosing Mode     Proportional PWM
  Standby                  off
  No Flow                  off
  Flow Mode                Direct
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
print(pump.ch1_value, pump.ch2_value, pump.pulse_rate_ch2)  # pH, Cl mg/l, p/min
await connection.close()
```

## Roadmap

1. Read-only models and tests for the pool controller, T010 and EMEC LD (done).
2. Home Assistant custom integration, installable through HACS: one entry per
   device, each with its own connection settings (done).
3. Writes, one device at a time, each with tests: thermostat settings on the
   T010 (done), switches, commands and the backwash schedule on the pool
   controller (done), and on the EMEC LD the pulse outputs and relays of both
   channels, flow, alarm, mA output and data logging settings and the PIN
   (done).
4. EMEC LD alarm coils (done).
5. Live tests of the EMEC LD writes not yet tried on a controller: pH Pulse 2,
   Cl Pulse, the relays, the mA outputs and the PIN.

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
but not Windows (CI runs them on pull requests and every push to `main`):

```bash
pip install "pytest-homeassistant-custom-component==0.13.372" "modbus-connection[tmodbus]==4.12.4"
pytest tests_ha
```

## Safety

This software talks to equipment that doses chemicals, heats water and moves a
pool cover. It writes the T010's thermostat settings, the pool controller's
switches and commands, which run the filter pump, the backwash, the cover and
the light, and the EMEC LD's settings: the dosing settings of its pH and Cl
pulse outputs and both relays, the flow mode (Disabled makes the controller
dose without flow), the alarm settings, the mA outputs, data logging and the
keypad PIN. Test changes carefully and
keep each device's own safety settings in place. You are responsible for your
installation.

This is an independent project, not affiliated with or endorsed by EMEC or
any other manufacturer named here. Product names belong to their owners.

## License

[Apache-2.0](LICENSE)
