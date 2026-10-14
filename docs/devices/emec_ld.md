# EMEC LD series

Tested on an **LDPHCL with firmware 5.1.4**, connected through a Modbus
TCP-to-RS-485 gateway. Registers are numbered as in EMEC's register list
(`4xxxx`); the address is what a request sends.

## Communication

- Modbus RTU, 38400 8N1 on the tested unit.
- At least 100 ms between requests; the library spaces them.
- Function 03 reads up to 125 values per request, as EMEC's protocol
  description allows; tested on the LDPHCL. The library reads every value it
  uses, 40002 to 40164, in one request.
- The firmware version is shown on the controller's display only; it cannot be
  read over Modbus.

## Addressing

The controller is byte-addressed. Every value takes two Modbus addresses and
sits at an odd address:

```
address = register - 40001        (40002 -> 1, 40052 -> 51)
```

A read of N registers from an odd address returns the N values at
`a, a+2, a+4, ...`. Reads must start at an odd address.

The model numbers values by index, `index = (register - 40002) / 2`, and
`EmecUnit` sends every read to `address = 2 * index + 1`.

## Value formats

| Format | Values | Decode |
|---|---|---|
| Plain 16-bit, signed | readings, divisors, temperature, probe mV, setpoints | as is, then scale |
| One byte in the high half (low byte 0) | relay states, pulse rates | `word >> 8` |
| Two bytes packed | clock | high byte, low byte |

A pulse rate reads `0xFF` for a single poll while the rate sits at zero; the
model reports it as 0.

## Register map

| Register | Address | Index | Format | Meaning |
|---|---|---|---|---|
| 40002 | 1 | 0 | int16 | Channel 1 reading (pH ×100 on an LDPHCL) |
| 40004 | 3 | 1 | uint16 | Channel 1 divisor: 1, 10, 100 or 1000 |
| 40006 | 5 | 2 | int16 | Channel 2 reading (Cl ppm ×100) |
| 40008 | 7 | 3 | uint16 | Channel 2 divisor |
| 40024 | 23 | 11 | high byte | Relay output, channel 2: 0 disabled, 1 on, 2 off |
| 40026 | 25 | 12 | high byte | Pulse rate, channel 1 output IS1 (p/min) |
| 40028 | 27 | 13 | high byte | Pulse rate, channel 1 output IS2 (p/min) |
| 40030 | 29 | 14 | high byte | Pulse rate, channel 2 (p/min) |
| 40032 | 31 | 15 | high byte | Relay output, channel 1: 0 disabled, 1 on, 2 off |
| 40044 | 43 | 21 | packed | Clock: month (high), day (low) |
| 40046 | 45 | 22 | packed | Clock: hour (high), year − 2000 (low) |
| 40048 | 47 | 23 | packed | Clock: minute (low) |
| 40052 | 51 | 25 | int16 | Temperature ×10 (°C) |
| 40056 | 55 | 27 | int16 | Probe channel 1 (mV) |
| 40058 | 57 | 28 | int16 | Probe channel 2 (mV) |
| 40068–40078 | 67–77 | 33–38 | int16 | Channel 1 pulse output 1: val1, val2 (×100), perc1, perc2 (p/min), wait (min), mode |
| 40140–40150 | 139–149 | 69–74 | int16 | Channel 1 pulse output 2: same layout |
| 40154–40164 | 153–163 | 76–81 | int16 | Channel 2 pulse output: same layout |

Pulse modes: 0 ON/OFF, 1 proportional, 2 disabled. In proportional mode the
pulse rate moves linearly from `perc1` at `val1` to `perc2` at `val2`, rounded
down, and stays at `perc1` beyond `val1`; this is a quick check that a decode
is right. Verified live on 2026-10-14 by narrowing the bands around the
readings: 40026 followed channel 1's pulse output 1 (3, 14 and 30 p/min at
pH 7.47 for the bands 7.40–8.00, 7.00–8.00 and 7.00–7.40), 40030 followed
channel 2 (3, 7 and 11 p/min at 0.46 ppm for 0.60–0.00, 1.00–0.00 and
1.00–0.40), and 40028 stayed 0 (that controller's second channel 1 output,
40140–40150, is disabled). The controller recomputes a rate on its own cycle,
so a new rate shows up to about 20 s after a change of the reading or the
settings.

The library never reads the password register (40542).

## Values shown by this library

Named as on an LDPHCL, where channel 1 is pH and channel 2 is free chlorine.
Channel 1 has two pulse outputs and channel 2 one, shown as **pH Relay 1**
(40068–40078, rate 40026), **pH Relay 2** (40140–40150, rate 40028) and
**Cl Relay** (40154–40164, rate 40030), each with the same seven values:
Pulse Rate, Dosing Mode, Max Value, Min Value, Max Pulse Rate, Min Pulse Rate
and Pulse Speed (pH values in pH, Cl values in ppm). The dosing settings
follow the output's working mode, as on the controller:

- **Max Value / Min Value** (val1, val2): unavailable when the output is
  disabled.
- **Max Pulse Rate / Min Pulse Rate** (perc1, perc2): proportional mode only.
- **Pulse Speed** (wait): ON/OFF mode only.

On the tested controller output 2 is disabled, so only its Dosing Mode and
Pulse Rate show a value.

Read groups in Home Assistant: the relay states fast (40024, 40032); readings,
temperature, pulse rates and probe voltages medium (40002–40058, one request);
the clock and the channel 1 dosing settings slow (40044–40150, one request).
Every group also reads both divisors (40004, 40008), so the fast group reads
40004–40032 and the slow group 40004–40150, each in one request.

The tested LDPHCL now and then answers a read with zeros for a few seconds:
divisors 0 (so no pH or Cl value), temperature 0.0 °C, probe voltages 0 mV and
both relays "Disabled", while the next poll reads normally again. Seen four
times in 12 hours on 2026-10-06, and under the YAML setup before. Sometimes only
some values read 0: both probe voltages, or both relays, for one poll (twice in
the 2 hours after the first fix). A measurement's divisor is never 0, so a read with a
divisor at 0 is refused. A value that was not 0 and reads 0
(pH and Cl readings, temperature, probe voltages, relay states) is refused once
and shown when the next poll reads 0 as well; a real 0, such as a probe voltage
near 0 mV at pH 7.00, shows one poll later. Pulse rates are not checked, as they
drop to 0 whenever dosing stops. A refused read keeps the values from the last
good one.

Optional parts, all on by default: **dosing settings** (the Dosing Mode and
the five settings of the three pulse outputs), **probe voltages** and
**clock**. A part that is off is not read.

## Writing

The dosing settings of the three pulse outputs (40068–40078, 40140–40150 and
40154–40164) are written one value per request with function 06, at the
value's odd wire address. Each value is checked against its range first, not
written when the controller already holds it, and confirmed by reading it back.

The tested LDPHCL stores a written value but answers function 06 later than its
Modbus TCP gateway waits (about 0.5 s), so the gateway replies with exception
0x0B, "gateway target device failed to respond". The new value reads back
within about 2 s, so the read-back, not the reply, decides whether a write
worked.

| Setting | pH Relay 1 | pH Relay 2 | Cl Relay | Range |
|---|---|---|---|---|
| Max Value, Min Value (val1, val2) | 40068, 40070 | 40140, 40142 | 40154, 40156 | 0.00–14.00 pH; 0.00–20.00 ppm (a library limit, the controller's is not documented) |
| Max Pulse Rate, Min Pulse Rate (perc1, perc2) | 40072, 40074 | 40144, 40146 | 40158, 40160 | 0–180 p/min |
| Pulse Speed (wait) | 40076 | 40148 | 40162 | 0–99 min |
| Dosing Mode | 40078 | 40150 | 40164 | ON/OFF, Proportional, Disabled |

In proportional mode the controller doses from 0 p/min at one pH value to the
set rate at the other, so setting one end's rate sets the other end's to 0:
Max Pulse Rate writes perc1, then perc2 = 0; Min Pulse Rate writes perc2, then
perc1 = 0. A working mode is entered by writing its settings in this order:

| Mode | Writes, in order |
|---|---|
| ON/OFF | perc1 = 100, perc2 = 0, wait = 1, mode = 0 |
| Proportional | wait = 0, mode = 1 |
| Disabled | mode = 2 |

Values the controller already holds are skipped. Writes to pH relay 1 were
tested live (2026-10-05); pH relay 2 and Cl relay use the same code on
registers of the same layout, but their writes have not been tried on a
controller yet.

## In Home Assistant

| Entity | Type | Device class | Icon | Registers | Read |
|---|---|---|---|---|---|
| pH Level | Sensor | `ph` | `ph` | 40002–40004 | Medium |
| Cl Level | Sensor, ppm | | `flask-outline` | 40006–40008 | Medium |
| Temperature | Sensor, °C | `temperature` | `thermometer` | 40052 | Medium |
| pH Relay, Cl Relay | Sensors: On, Off, Disabled | `enum` | `pump` / `pump-off` | 40032, 40024 | Fast |
| pH Relay 1/2 Pulse Rate, Cl Relay Pulse Rate | Sensors, p/min | | `pulse` | 40026, 40028, 40030 | Medium |
| pH Probe Voltage, Cl Probe Voltage | Diagnostic sensors, mV | `voltage` | `sine-wave` | 40056, 40058 | Medium |
| RTC | Diagnostic sensor | `timestamp` | `clock-outline` | 40044–40048 | Slow |
| pH Relay 1/2 Dosing Mode, Cl Relay Dosing Mode | Selects | | `toggle-switch-outline` (ON/OFF), `chart-line-variant` (Proportional), `cancel` (Disabled), `tune-variant` otherwise | 40078, 40150, 40164 | Slow |
| pH Relay 1/2 Max Value, Min Value | Numbers (input box), 0.01 steps | `ph` | `gauge-full`, `gauge-empty` | 40068, 40070 / 40140, 40142 | Slow |
| Cl Relay Max Value, Min Value | Numbers (input box), ppm, 0.01 steps | | `gauge-full`, `gauge-empty` | 40154, 40156 | Slow |
| pH Relay 1/2, Cl Relay Max Pulse Rate, Min Pulse Rate | Numbers (input box) | | `speedometer`, `speedometer-slow` | 40072, 40074 / 40144, 40146 / 40158, 40160 | Slow |
| pH Relay 1/2, Cl Relay Pulse Speed | Numbers (input box), minutes | `duration` | `timer-cog-outline` | 40076 / 40148 / 40162 | Slow |

Entity ids carry the relay number where there is more than one output:
`select.dosing_pump_ph_relay_1_dosing_mode`, `number.dosing_pump_ph_relay_2_max_value`;
chlorine has one pulse output, so its entities are just "Cl Relay ...":
`sensor.dosing_pump_cl_relay_pulse_rate`, `select.dosing_pump_cl_relay_dosing_mode`.
Before 2026-10-14 pH relay 1's entities had no number ("pH Dosing Mode",
`number.dosing_pump_ph_max_value`) and the Cl rate was "Cl Pulse Rate"; an
installation from before is moved to the new unique ids and, when the entity
id is still the one made from the old name, to the new entity id, with its
history.
