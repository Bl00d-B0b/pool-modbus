# Pool equipment (pool-modbus)

A Home Assistant integration for pool equipment that speaks Modbus: a pool
controller, a heating thermostat and a pH/Cl dosing controller.

- Home Assistant name: **Pool equipment**, domain `pool_modbus`
- Version: **2026.10.0**
- Requires Home Assistant **2026.9.0** or later
- Installs through [HACS](https://hacs.xyz/) as a custom repository

Each device type is modelled as typed Python (registers, scaling, byte layout).

## Supported devices

| Device | Key | Maker | Firmware | Details |
|---|---|---|---|---|
| Pool controller: filtration, filter valve (backwash), cover, light, water level | `pool_controller` | Optika ir technologija | | [docs/devices/pool_controller.md](docs/devices/pool_controller.md) |
| T010 pool thermostat | `t010` | Optika ir technologija | 1.7 | [docs/devices/t010.md](docs/devices/t010.md) |
| EMEC LD series pH/Cl controller | `emec_ld` | EMEC | LDPHCL, 5.1.4 | [docs/devices/emec_ld.md](docs/devices/emec_ld.md) |

All three are read and written. The device documents list every register the
integration uses, the entities it creates and each device's limits.

The EMEC LD is byte-addressed: each value takes two Modbus addresses and sits
at an odd one, and some values are a single byte in the high half of a
register. The integration handles this itself; see its
[device document](docs/devices/emec_ld.md).

Want another device here? Open an issue with its register map.

## Installation

1. In HACS, open the menu, choose **Custom repositories**, add
   `https://github.com/Bl00d-B0b/pool-modbus` with the type **Integration**.
2. Find **Pool equipment** in HACS, download it and restart Home Assistant.
3. Add each device (below).

The integration uses Home Assistant's built-in Modbus integration for its
connections, but needs no `modbus:` YAML configuration.

### Updating

HACS offers a new version when one is published on the repository's
[GitHub releases](https://github.com/Bl00d-B0b/pool-modbus/releases). Update
it in HACS and restart Home Assistant.

### Removing

Delete each device under **Settings → Devices & services → Pool equipment**,
then remove **Pool equipment** in HACS and restart Home Assistant.

## Adding a device

Go to **Settings → Devices & services → Add integration** and pick **Pool
equipment**. Each device is added on its own, with its own connection:

1. **Device type** and **connection**: Modbus TCP, RTU over TCP, Modbus UDP,
   or serial (RS-485 / RS-232).
2. **Name**, **entity id prefix** and the address:
   - network connections: host and port (default 502);
   - serial: serial port, baud rate, data bits, parity and stop bits;
   - **Modbus ID**, 1–247, offered as 1.

   The integration reads the device before it saves anything. The T010 and the
   EMEC LD must also answer as the chosen device type; the pool controller only
   has to answer.
3. **Reading**: the polling intervals and the optional parts to read (see
   [Configure](#configure)).

Devices with the same connection settings share one connection, so several
devices behind one gateway work side by side.

**Name.** A new device is offered the type's default name: *Controller*,
*Thermostat* or *Dosing pump*. A second device of a type is offered the first
one's name with a number (*Thermostat 2*). The name is only what is shown:
entities appear as "<device name> <entity name>", for example "Thermostat
Heating Delay".

**Entity id prefix.** Optional. It may contain letters, digits, spaces, `-`
and `_`; it is stored in lower case, with each run of other characters (spaces,
`-`) turned into one `_`. Each device of a type needs a different prefix, and
only one device of a type can have none. The prefix is part of the device's
identity: it cannot be changed later (Configure shows it), and the same device
can be added a second time with a prefix of its own.

## Configure

**Configure** on a device changes:

- the connection type, the address or serial settings and the Modbus ID (the
  device is read before the change is saved, and it keeps its entities);
- the polling intervals;
- the optional parts to read;
- **Log the bus traffic around failures** (see [Debugging](#debugging)).

Optional parts, each a checkbox, all on by default. A part that is off has no
entities, and the regular polls read only the parts that are on (the read when
a device is set up reads all of them).

| Device | Optional parts |
|---|---|
| Pool controller | Read the backwash schedule; Read the clock |
| T010 thermostat | none |
| EMEC LD | Read the dosing settings (outputs, alarms, timing): the settings of the five outputs, the alarm settings, timing, flow and priority; Read the alarms and standby; Read the mA outputs; Read the data logging settings; Read the label and PIN; Read probe voltages; Read the clock |

## How values are read

Values are read in three groups, each on its own interval. Each group reads
only the registers of its own values, in as few requests as the device allows.
Configure shows only the intervals of the groups a device uses (the T010 and
the pool controller have no medium group).

| Group | Default | Values |
|---|---|---|
| Fast | 5 s | States and alarms, the thermostat |
| Medium | 10 s | Measurements: temperatures, pH, chlorine, pulse rates |
| Slow | 15 s | Settings, clocks, firmware |

A read that fails is tried once more after 0.5 s. If that fails too, the
group's entities keep their last values, so a missed poll does not cut their
history and statistics; only when a group has had no good read for 60 s do its
entities become unavailable. A device type can also refuse a read it knows to
be wrong (the EMEC LD now and then answers with zeros); a refused read never
changes the values shown, and the next poll reads again.

Settings that the device's current working mode does not use (for example a
dosing output's pulse speed outside ON/OFF mode) have no entity, as in the
device's own menu. When the mode changes, the device's entities are set up
again: at once after a change made in Home Assistant, and after two reads in a
row for a change made on the device.

## Writing

Values a device lets you change become switches, numbers, selects, times,
texts or a light. The T010 also has a thermostat (climate) entity; the pool
controller has a cover and buttons for its commands.

- A new value is checked against the range the device accepts; a value
  outside it is refused with an error.
- The device is read first, and a value it already holds is not written again
  (the T010 stores every write in its EEPROM).
- After a write, the integration reads the device until it shows the new value,
  for up to 5 s, and reports an error if it does not. Some Modbus TCP gateways
  answer reads from a cache, so a written value can take a moment to show.
- A write that gets no reply (a gateway exception 0x0B or a timeout) is decided
  by that read-back: if the device shows the new value, the write succeeded.
  The EMEC LD can answer writes later than a gateway waits.
- Writes to one device run one at a time.

The pool controller may not carry out a light toggle (it ignores the light
while the cover is closed); the light then keeps showing its real state,
without an error.

If you also read the same devices through Home Assistant's YAML `modbus:`
configuration, that hub opens its own connection to the gateway. The gateway
then has two clients whose requests can collide, so a device may answer one of
them with an error or a timeout. Move a device to this integration and out of
the YAML at the same time.

## Entities

Every entity id is `<device type base>_<prefix>_<entity name>`, with the base
`pool_controller`, `pool_thermostat` or `dosing_pump`:

- `switch.pool_controller_filtration`
- `number.pool_thermostat_temperature_offset`, or with prefix `2`,
  `number.pool_thermostat_2_temperature_offset`
- `climate.pool_thermostat`: the thermostat is the T010's main entity and has
  no name of its own

The integration sets these ids itself, so the device's area never becomes part
of an id, and moving a device to another area changes no id. Clocks are named
RTC; the pool controller's **Sync RTC** button sets its clock to Home
Assistant's time.

Entities are sorted into Home Assistant's sections:

| Section | What goes there |
|---|---|
| Controls and sensors | States, measurements, the thermostat, the cover, the light, commands, and the dosing settings of the EMEC LD's five outputs (pH Pulse 1 and 2, Cl Pulse, pH and Cl Relay) |
| Configuration | Settings you can change |
| Diagnostic | Read-only settings, clocks, firmware versions, the EMEC LD's label and PIN and its alarms other than Standby, and the Reset Alarms and Sync RTC buttons |

Created disabled, to be enabled by hand: the EMEC LD's RTC and PIN.

The integration brings a small icon set, `pool:`, for what Material Design
Icons lack: `pool:cover-open` and `pool:cover-closed` for a pool cover.

## Debugging

**Debug logging.** Turn on **Enable debug logging** on the integration's page.
Every request is then logged with its time, Modbus ID, function, address and
the request and reply frames in RTU form (built from the request and the values
of the answer, the bytes a TCP gateway puts on the bus), or its error.

**Traffic log.** To catch a failure that comes rarely, switch on **Log the bus
traffic around failures** in a device's Configure. The integration keeps the
last 300 requests of each connection in memory, those of every device on it in
the order they went out. When 5 requests in a row fail, it writes them all to
the log as one warning; when a device answers again, a second warning has the
last failures, the first answer and the 10 requests after it. A single failure
writes nothing. Home Assistant's system log shows only the last few messages of
a kind, so download the full log (**Settings → System → Logs**) to keep them.

The EMEC LD's keypad PIN never appears in the log: its value is written as 0
in both debug lines and traffic warnings. The PIN entity's state is the PIN
itself, so when you enable it, exclude it from the recorder:

```yaml
recorder:
  exclude:
    entities:
      - text.dosing_pump_pin
```

## Safety

This software talks to equipment that doses chemicals, heats water and moves a
pool cover, and it writes settings that change dosing, flow and filtration:

- the T010's thermostat settings;
- the pool controller's switches and commands, which run the filter pump, the
  backwash, the water filling, the cover and the light;
- the EMEC LD's dosing settings of its pH and Cl pulse outputs and both relays,
  its flow mode (Disabled makes the controller dose without flow), timing,
  alarm, mA output and data logging settings and its keypad PIN.

Make changes carefully and keep each device's own safety settings in place.
You are responsible for your installation.

This is an independent project, not affiliated with or endorsed by EMEC,
Optika ir technologija or any other manufacturer named here. Product names
belong to their owners.

## License

[Apache-2.0](LICENSE)
