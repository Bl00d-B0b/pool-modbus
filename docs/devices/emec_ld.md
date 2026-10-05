# EMEC LD series

Verified on an **LDPHCL, firmware 5.1.4**, through a Modbus TCP-to-RS-485
gateway at 38400 8N1. The vendor document is *MODBUS PROTOCOL – LD SERIES*;
its addresses are written `4xxxx`.

## Addressing

The controller is byte-addressed. Every value takes two Modbus addresses and
sits at an odd wire address:

```
wire address = PDF address - 40001        (40002 -> 1, 40052 -> 51)
```

A read of N registers from an odd address returns the N values at
`a, a+2, a+4, ...`. HA's YAML integration, for example, reads 32 registers
from address 1 and gets the 32 values 40002 to 40064. A read from an even
address returns byte-shifted data.

The model numbers values by index, `index = (PDF address - 40002) / 2`, and
`EmecUnit` sends every read to `wire = 2 * index + 1`.

Coils (function 01) use normal bit addressing.

## Value formats

| Format | Values | Decode |
|---|---|---|
| Plain 16-bit, signed | readings, divisors, temperature, probe mV, setpoints | as is, then scale |
| One byte in the high half (low byte 0) | relay states, pulse rates | `word >> 8` |
| Two bytes packed | clock | high byte, low byte |

A pulse rate reads `0xFF` for a single poll while the rate sits at zero; the
model reports it as 0.

## Register map (modelled)

| PDF | Index | Wire | Format | Meaning |
|---|---|---|---|---|
| 40002 | 0 | 1 | int16 | Channel 1 reading (pH ×100 on an LDPHCL) |
| 40004 | 1 | 3 | uint16 | Channel 1 divisor: 1, 10, 100 or 1000 |
| 40006 | 2 | 5 | int16 | Channel 2 reading (Cl ppm ×100) |
| 40008 | 3 | 7 | uint16 | Channel 2 divisor |
| 40024 | 11 | 23 | high byte | Relay output, channel 2: 0 disabled, 1 on, 2 off |
| 40026 | 12 | 25 | high byte | Pulse rate, channel 1 output IS1 (p/min) |
| 40028 | 13 | 27 | high byte | Pulse rate, channel 1 output IS2 (p/min) |
| 40030 | 14 | 29 | high byte | Pulse rate, channel 2 (p/min) |
| 40032 | 15 | 31 | high byte | Relay output, channel 1 |
| 40044 | 21 | 43 | packed | Clock: month (high), day (low) |
| 40046 | 22 | 45 | packed | Clock: hour (high), year − 2000 (low) |
| 40048 | 23 | 47 | packed | Clock: minute (low) |
| 40052 | 25 | 51 | int16 | Temperature ×10 (°C) |
| 40056 | 27 | 55 | int16 | Probe channel 1 (mV) |
| 40058 | 28 | 57 | int16 | Probe channel 2 (mV) |
| 40068–40078 | 33–38 | 67–77 | int16 | Channel 1 pulse output 1: val1, val2 (×100), perc1, perc2 (p/min), wait (min), mode |
| 40154–40164 | 76–81 | 153–163 | int16 | Channel 2 pulse output: same layout |

Pulse modes: 0 ON/OFF, 1 proportional, 2 disabled. In proportional mode the
pulse rate moves linearly from `perc1` at `val1` to `perc2` at `val2`, rounded
down; this is a quick check that a decode is right.

Not modelled yet: alarm coils, relay settings (40082–40090, 40212–40220),
min/max, mA, flow, log and clock settings. The password register (40542) is
deliberately left out.

## Errors in the vendor PDF

Checked against live data:

1. Relay states and pulse rates are documented as Int16; they are one byte in
   the high half of the register.
2. "No. register 2" for each value really means one register at an odd address.
3. 40026 and 40028 are both labelled "P/m for out IS pH". They are most likely
   outputs IS1 and IS2 (the alarm list names both).
4. Label registers are listed one address apart (40844, 40845, ...); they sit
   two apart (40844, 40846, ...).
5. The two relay states (40024, 40032) have always changed together on the
   test unit, so they do not behave like independent outputs.

Likely typos from the document's layout: 40458 is called "ch1 Low" inside the
ch2 group, coil 63 is called "ch1" inside the ch2 group, and 40128/40130 say
"ch2" under the ch3 mode.

Open: the alarm coils are numbered 00–13 in an older edition and 01–14 in a
newer one. The configuration coil numbers match wire addresses, which favours
00–13, but no alarm has been active on the test unit to confirm it.

## Not available over Modbus

The firmware version. A read-only scan of every readable address (up to PDF
41840) found no version value, the label registers are blank, and the
controller does not answer *Report Slave ID* (0x11) or *Read Device
Identification* (0x2B).
