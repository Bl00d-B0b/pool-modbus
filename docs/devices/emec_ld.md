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
| 40154–40164 | 153–163 | 76–81 | int16 | Channel 2 pulse output: same layout |

Pulse modes: 0 ON/OFF, 1 proportional, 2 disabled. In proportional mode the
pulse rate moves linearly from `perc1` at `val1` to `perc2` at `val2`, rounded
down; this is a quick check that a decode is right.

The library never reads the password register (40542).

## Values shown by this library

Named as on an LDPHCL, where channel 1 is pH and channel 2 is free chlorine.
The dosing settings of channel 1 follow its working mode, as on the
controller:

- **pH Max Value / pH Min Value** (val1, val2): unavailable when the output is
  disabled.
- **pH Max Pulse Rate / pH Min Pulse Rate** (perc1, perc2): proportional mode only.
- **pH Pulse Speed** (wait): ON/OFF mode only.

Read groups in Home Assistant: the relay states fast (40024, 40032); readings,
temperature, pulse rates and probe voltages medium (40002–40058, one request);
the clock and the channel 1 dosing settings slow (40044–40078, one request).
Optional parts, all on by default: **dosing settings** (pH Dosing Mode
and the five settings above), **probe voltages** and **clock**. A part that is
off is not read.
