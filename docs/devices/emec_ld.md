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
| 40006 | 5 | 2 | int16 | Channel 2 reading (Cl mg/l ×100) |
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
| 40082–40090 | 81–89 | 40–44 | int16 | Channel 1 switching relay: val1, val2 (×100), perc1, perc2 (% duty), mode |
| 40212–40220 | 211–219 | 105–109 | int16 | Channel 2 switching relay: same layout |

Pulse modes: 0 ON/OFF, 1 proportional, 2 disabled. In proportional mode the
pulse rate moves linearly from `perc1` at `val1` to `perc2` at `val2`, rounded
down, and stays at `perc1` beyond `val1`; this is a quick check that a decode
is right. Verified live on 2026-10-14 by narrowing the bands around the
readings: 40026 followed channel 1's pulse output 1 (3, 14 and 30 p/min at
pH 7.47 for the bands 7.40–8.00, 7.00–8.00 and 7.00–7.40), 40030 followed
channel 2 (3, 7 and 11 p/min at 0.46 mg/l for 0.60–0.00, 1.00–0.00 and
1.00–0.40), and 40028 stayed 0 (that controller's second channel 1 output,
40140–40150, is disabled). The controller recomputes a rate on its own cycle,
so a new rate shows up to about 20 s after a change of the reading or the
settings.

The library never reads the password register (40542).

### Not readable, and not yet usable

Found on the tested LDPHCL (firmware 5.1.4) on 2026-10-14 by changing a setting
on the keypad while every readable register (wire 1–1839) and the coils were
read before and after:

- **Output Manager** (Auto, Off, or one output run manually for 0–99 min, as the
  instruction manual describes): Auto and Off change no register or coil. A
  manual run shows only through its effect, the output's pulse rate (150 p/min
  for Man. Ch1 pH pulse1); its countdown is not readable.
- **Countdown after flow returns, temperature probe millivolts, calibration
  dates and calibration-fail flags** (all on the status screens): no register
  holds them.
- **Label** (the protocol's "chr0-chr1" … "chr26-chr27", R/W): the instrument's
  28-character NAME, two characters per value, at 40844–40870 (14 values two
  addresses apart; the protocol numbers them 40844–40857). The instruction
  manual sets the instrument ID and NAME in the "RS485 Setup" menu; the NAME goes
  into SMS messages, the USB log and ERMES. It reads 28 dashes here, an unset
  name.
- **41664–41840**: undocumented, random-looking values that did not change in any
  of 17 scans between 2026-10-01 and 2026-10-14 (clock writes, format switches,
  mode changes and dosing in between). A live watch of every value (43 full scans
  in 150 s) changed only the pH reading, the clock's minute and the pH probe
  voltage: no register counts seconds.
- **Clock setpoint** (41624–41628): a write is applied to the clock 10–60 s
  later, but not in the layout the controller reports or the protocol
  description gives. The controller copies the written bytes into a buffer at
  the even addresses 1624 (month, day), 1626 (minute, hour) and 1628 (year), and
  from there into the clock. Source bytes: day = 41624 high, month = 41626 low,
  hour = 41626 high, minute = 41628 low, year = 41628 high (the byte the protocol
  calls "00"). Day, month and hour are taken without a range check (day 32,
  month 13 and 14, hour 25 all showed on the clock); a minute of 61 was refused.
  **The year is copied into the buffer (6, 26, 99, 255 all showed at 1628) but
  never into the clock: every clock write sets the clock's year to 2000**,
  whatever the year byte holds (1 to 4 tried as well). So a write can set the
  time and date only at the cost of the year, which only the keypad corrects.
  The clock's own New Year rollover works (31/12/2000 23:59 became 01/01/2001
  00:00), but the next write resets the year again, so it cannot be used to
  count the year up.
  41612–41622 also take writes (undocumented, read 0) and do not set the year;
  41630–41642 take none. The library does not write the clock.
  **Function 16** (tested 2026-10-14 21:00, 3 registers from 41624; the gateway
  answered 0x0B as for every write) is stored, but byte by byte: the 6 bytes go
  to consecutive byte addresses in the order sent (high byte first), while a
  function 06 write and every read put a register's low byte at its own address
  and the high byte at the next. In byte terms the setpoint is one record:
  day, month, hour, minute, year at byte addresses 1624–1628. A function 16
  write did not make the controller apply the setpoint (the clock kept running
  for 80 s), so it cannot set the clock on its own.
- **Coil 47** (the protocol's date format, Europe or USA): a function 05 write
  changes one coil (128-coil read position 41). How the clock reads in USA
  format: see "Units format" below.
- **Where 2001 comes from** (reproduced 2026-10-14 20:00): a clock write is
  applied with the year 2000 in Europe format and **2001 in USA format**. A
  Modbus switch to USA re-applies the setpoint, and the year went 0 → 1 with that
  apply (14 s after the switch, together with the minute), not with the switch
  itself; back in Europe format the next apply set it to 0 again. The first coil
  47 test had shown the same, and its second try showed nothing because the coil
  was switched back after 4 s, before the apply. The other way to 2001 is the
  clock's own New Year rollover, the only one that counts the year.
- **Units format** (coil 47, the menu's Europe / USA; found 2026-10-14 by the
  keypad switch, which sets the same read position 41 as a write of 47): in USA
  format the temperature (40052) reads whole °F (60 for the display's 60.2 °F)
  instead of °C × 10, and the date word 40044 holds the day in its high byte and
  the month in its low byte. A time set on the keypad in USA format is stored as
  a 12-hour hour (7 for 7 PM) with the AM/PM flags in coils 45 (AM, read
  position 43) and 46 (PM, read position 42), found by setting 9:00 AM and 7 PM
  on the keypad; a time set in Europe format keeps its 24-hour hour after a
  switch to USA. The RTC combines the hour with the PM flag (12 AM is 0).
  Coil 46 takes function 05 writes, but only the flag changes: written off and
  on in both formats (2026-10-14), the hour stayed 19 and the AM coil stayed off.
  Coil 45 (AM) behaves the same (written on and off in Europe format at 20:05,
  read position 43 followed, the hour stayed 20). The two flags are independent:
  with 45 written on, AM and PM read on together.
  **A write of coil 46 also makes the controller re-apply its clock setpoint**,
  like a units switch: the first try, with a stale setpoint, put its old day,
  month and minute on the clock and the year 2000. The library does not write
  coils 45 and 46.
  **A switch over Modbus does not switch the readings either**: after a keypad
  switch to USA the temperature read 60 (whole °F), after each of three Modbus
  switches (2026-10-14 19:56, 20:13, 20:25) it kept reading 157 (°C × 10), so
  Temperature °F showed 157. In USA format the year byte of a write is ignored
  as well (26 tried in 41628 high and 41624 low, and in 41612–41622; the year
  stayed 2001).
  **A switch over Modbus does not convert the clock** the way the keypad does:
  after a Modbus switch to USA the date word stayed in Europe order (0x0A0E for
  14 October) and the hour in 24-hour form, and the year became 2001 (above).
  RTC decodes the USA date word as day and month swapped, so after a switch to
  USA from Home Assistant it shows unknown (month 14) until the date is set on
  the keypad.
  **A clock write with day and month swapped** (41624 high byte = the month,
  41626 low byte = the day), sent while the format is USA, gives the date word
  the keypad's USA order (0x0E0A for 14 October; tested 2026-10-14 20:14), and RTC
  then showed the right date and time again, with the year 2001. The hour is
  stored as written: 20 or, written in 12-hour form, 8; the PM flag does not
  change with it. Three times that evening the clock took the minute (or day) of
  the write before: once the controller had answered the word's write with 0x0B
  and not stored it (the read-back showed the old word), and the apply copied the
  rest; the apply also copies field by field (a half-applied date word 0x0A0A
  showed for a few seconds), so a write must be read back word by word.
  A clock write sets day, month, hour and minute (tested in Europe format: all
  four as written, the year 2000). It does not set the
  seconds: there is no register for them and the clock keeps its old seconds (the
  next minute came 43 s after an apply).
  The library reads the coil: **Temperature** (°C) exists in Europe format,
  **Temperature °F** (whole °F, without HA's temperature conversion) in USA
  format, and RTC decodes the date in both. **Units** shows the format and is
  read-only (since 2026-10-14; it was a select before): a switch over Modbus is
  only half a switch (above) and costs the clock's year, so switch the format on
  the keypad, which converts the readings and leaves the clock alone. The library
  writes no clock-related coil and not the clock setpoint.
- **Fixed PWM on and off times** of the switching relays: not documented and not
  found yet.

## Values shown by this library

Named as on an LDPHCL, where channel 1 is pH and channel 2 is free chlorine.
The outputs are named as on the controller. Channel 1 has two pulse outputs and
channel 2 one: **pH Pulse 1** ("CH1 PH PULSE 1", 40068–40078, rate 40026),
**pH Pulse 2** ("CH1 PH PULSE 2", 40140–40150, rate 40028) and **Cl Pulse**
("CH2 CL2 PULSE", 40154–40164, rate 40030), each with the same seven values:
Rate, Dosing Mode, Max Value, Min Value, Max Rate, Min Rate and Pulse Speed (pH
values in pH, Cl values in mg/l, the controller's unit; its display writes Cl₂).
The dosing settings follow the output's working mode, as on the controller:

- **Max Value / Min Value** (val1, val2): not used when the output is disabled.
- **Max Rate / Min Rate** (perc1, perc2): proportional mode only.
- **Pulse Speed** (wait): ON/OFF mode only.

Each channel also has a switching relay: **pH Relay** ("CH1 PH RELAY",
40082–40090) and **Cl Relay** ("CH2 CL2 RELAY", 40212–40220), with Dosing Mode
(Proportional PWM, ON/OFF, Fixed PWM, Disabled), Max Value, Min Value and, in
Proportional PWM only, Max/Min Duty Cycle: the share of a 100 s cycle the relay
is on at each value (the instruction manual: 100 % = 100 s). Fixed PWM's on and
off times (Ton, Toff) are not mapped yet. Their on/off state is **pH Relay
State** and **Cl Relay State** (40032, 40024).

In Home Assistant a setting the current mode does not use has no entity, like
in the controller's own menu. The entry creates the settings the modes use at
setup and removes the registry entries of the others (and of keys earlier
versions had); when a mode changes, the entry reloads and the entities follow,
with the same entity ids. A mode chosen in Home Assistant reloads at once; one
changed on the keypad is seen on two slow reads in a row first, so about 15 to
30 s later. The values of a removed setting stay in the controller as the mode
switch wrote them (see the order below), e.g. ON/OFF leaves the top rate at 100.

On the tested controller output 2 is disabled, so only its Dosing Mode and
Pulse Rate show a value.

### Alarms and state (coils)

The controller's alarms are coils (function 01), numbered 1–16 here as in the
protocol description. The tested LDPHCL answers a coil read with bits that
shift with the start address and the count (the same set coil showed at
position 11 for a read of 16 from 0, at 12 for a read of 32, at 13 for a read
of 16 from 1), so the library always reads coils 0–15 from 0 in one request
and takes the slice it needs (`EmecUnit.read_coils`).

| Coil | Shown as | Source |
|---|---|---|
| 11 | **Standby** (binary): on while the controller is held in standby, outputs off | seen live 2026-10-14: set within a minute of the filtration pump stopping, both relay outputs went Off; cleared within 15 s of flow returning |
| 6 | **No Flow** (binary, `problem`): on while the controller sees no flow and holds dosing, outputs off | seen live 2026-10-14 while the display said "no flow": for the flow time after flow returned (from flow return until dosing resumed 5 min 14 s later), and once with the filtration running |
| 7 | **Cl Tank Empty** (binary, `problem`) | the user's notes, read as counting from 0 (their "5 = no flow" is coil 6); not seen yet |
| 8 | **pH Tank Empty** (binary, `problem`) | the user's notes, read the same way; not seen yet |
| all | **Alarm Coils** (diagnostic text): the set coils, e.g. "6, 11", or "None" | for naming the rest when they occur |

The protocol description lists the coils as 1 Ch1 level 1, 2 Ch1 level 2,
3 Ch2 level, 4 Flow, 5–6 out of range 1–2, 7–11 dosing alarms (ch2 IS, ch2
relay, ch1 IS1, ch1 relay, ch1 IS2), 12–13 probe fail, 14 standby; on the
tested controller the observed meanings do not follow it, so only the
observed and noted ones are named.

### Timing

| Register | Index | Shown as |
|---|---|---|
| 40520 | 259 | **Tau** (number, 0–30, writable): the controller's measurement filter setting, "Tau" in its menu; unit not documented |
| 40522 | 260 | **Feeding Delay** (number, 0–60 min, writable): "Feeding delay" in the menu, how long dosing waits after flow or power returns |
| 40524 | 261 | **Flow Time** (number, 0–99 min, writable): how long the flow contact must hold its new state before the controller acts on it |
| 40526 | 262 | **Flow Mode** (select, writable): Disabled (contact ignored), Reverse (N.O. contact), Direct (N.C. contact) |

**Priority** (select, writable) is the menu's "Mode: No priority / pH priority",
coil 58 (protocol: 0 = pH priority, 1 = no priority). It is written at address 58
with function 05 and read from a read of 128 coils from 0, where the tested
controller shows it at position 62: found on 2026-10-14 by switching it on the
keypad (only that bit moved), and a write of 58 = on set the keypad back to No
priority with no other coil changed. Other configuration coils are not read,
since their positions are unknown. The controller gives no countdown of the wait after
flow returns (every readable register was scanned during one on 2026-10-14 and
none counted down); Home Assistant can compute it from the moment the flow
contact closed and the Flow Time.

Read groups in Home Assistant: the relay states and the alarm coils fast
(40024, 40032; coils 0–15); readings, temperature, pulse rates and probe
voltages medium (40002–40058, one request); the clock, the dosing settings and
the timing slow (40044–40150 and 40522–40526, two requests). Every group also
reads both divisors (40004, 40008), so the fast group reads 40004–40032 in one
request plus the coils.

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
the five settings of the three pulse outputs, the timing values), **alarms**
(the coils), **probe voltages** and **clock**. A part that is off is not read.

## Writing

The dosing settings of the three pulse outputs (40068–40078, 40140–40150 and
40154–40164) and the two switching relays (40082–40090, 40212–40220) are written one value per request with function 06, at the
value's odd wire address. Each value is checked against its range first, not
written when the controller already holds it, and confirmed by reading it back.

The tested LDPHCL stores a written value but answers function 06 later than its
Modbus TCP gateway waits (about 0.5 s), so the gateway replies with exception
0x0B, "gateway target device failed to respond". The new value reads back
within about 2 s, so the read-back, not the reply, decides whether a write
worked.

| Setting | pH Pulse 1 | pH Pulse 2 | Cl Pulse | Range |
|---|---|---|---|---|
| Max Value, Min Value (val1, val2) | 40068, 40070 | 40140, 40142 | 40154, 40156 | 0.00–14.00 pH; 0.00–10.00 mg/l (the working ranges in the instruction manual) |
| Max Rate, Min Rate (perc1, perc2) | 40072, 40074 | 40144, 40146 | 40158, 40160 | 0–180 p/min |
| Pulse Speed (wait) | 40076 | 40148 | 40162 | 0–99 min |
| Dosing Mode | 40078 | 40150 | 40164 | ON/OFF, Proportional, Disabled |

The timing values are written the same way: Tau (40520, 0–30), Feeding Delay (40522, 0–60 min),
Flow Time (40524, 0–99 min) and Flow Mode (40526). Setting Flow Mode to
Disabled makes the controller dose without flow, which its manual warns against.

In proportional mode the controller doses from 0 p/min at one pH value to the
set rate at the other, so setting one end's rate sets the other end's to 0:
Max Rate writes perc1, then perc2 = 0; Min Rate writes perc2, then
perc1 = 0. A working mode is entered by writing its settings in this order,
which is what the controller's own keypad does (watched on 2026-10-14: ON/OFF
forces the top rate to 100 and keeps a set interval, giving a 0 interval 1 minute; Proportional zeroes the
interval and keeps the rates, Off also zeroes the interval (seen on the Cl output,
ON/OFF with 3 min to Off) and changes nothing else; the same on all three outputs; the stored values show
up to a minute after the keypad confirms them):

| Mode | Writes, in order |
|---|---|
| ON/OFF | perc1 = 100, perc2 = 0, wait = 1 only if it was 0, mode = 0 |
| Proportional | wait = 0, mode = 1 |
| Disabled | wait = 0, mode = 2 |

Values the controller already holds are skipped. Writes to pH Pulse 1 were
tested live (2026-10-05, and a mode round trip on 2026-10-14); pH Pulse 2 and Cl
Pulse use the same code on registers of the same layout, but their writes have
not been tried on a controller yet. A switching relay's settings are written one
by one, its mode on its own: how the keypad changes the other settings on a
relay mode switch has not been watched yet, so none are forced. Relay writes have
not been tried live.

| Relay setting | pH Relay | Cl Relay | Range |
|---|---|---|---|
| Max Value, Min Value (val1, val2) | 40082, 40084 | 40212, 40214 | 0.00–14.00 pH; 0.00–10.00 mg/l |
| Max Duty Cycle, Min Duty Cycle (perc1, perc2) | 40086, 40088 | 40216, 40218 | 0–100 % |
| Dosing Mode | 40090 | 40220 | Proportional PWM, ON/OFF, Fixed PWM, Disabled |

## In Home Assistant

| Entity | Type | Device class | Icon | Registers | Read |
|---|---|---|---|---|---|
| pH Level | Sensor | `ph` | `ph` | 40002–40004 | Medium |
| Cl Level | Sensor, mg/L | | `flask-outline` | 40006–40008 | Medium |
| Temperature | Sensor, °C | `temperature` | `thermometer` | 40052 | Medium |
| pH Relay State, Cl Relay State | Sensors: On, Off, Disabled | `enum` | `pump` / `pump-off` | 40032, 40024 | Fast |
| pH Pulse 1/2 Rate, Cl Pulse Rate | Sensors, p/min | | `pulse` | 40026, 40028, 40030 | Medium |
| pH Probe Voltage, Cl Probe Voltage | Diagnostic sensors, mV | `voltage` | `sine-wave` | 40056, 40058 | Medium |
| RTC | Diagnostic sensor | `timestamp` | `clock-outline` | 40044–40048 | Slow |
| pH Pulse 1/2 Dosing Mode, Cl Pulse Dosing Mode | Selects | | `toggle-switch-outline` (ON/OFF), `chart-line-variant` (Proportional), `cancel` (Disabled), `tune-variant` otherwise | 40078, 40150, 40164 | Slow |
| pH Pulse 1/2 Max Value, Min Value | Numbers (input box), 0.01 steps | `ph` | `gauge-full`, `gauge-empty` | 40068, 40070 / 40140, 40142 | Slow |
| Cl Pulse Max Value, Min Value | Numbers (input box), mg/L, 0–10, 0.01 steps | | `gauge-full`, `gauge-empty` | 40154, 40156 | Slow |
| pH Pulse 1/2, Cl Pulse Max Rate, Min Rate | Numbers (input box) | | `speedometer`, `speedometer-slow` | 40072, 40074 / 40144, 40146 / 40158, 40160 | Slow |
| pH Pulse 1/2, Cl Pulse Pulse Speed | Numbers (input box), minutes | `duration` | `timer-cog-outline` | 40076 / 40148 / 40162 | Slow |
| pH Relay Dosing Mode, Cl Relay Dosing Mode | Selects: Proportional PWM, ON/OFF, Fixed PWM, Disabled | | `chart-line-variant`, `toggle-switch-outline`, `square-wave`, `cancel` | 40090, 40220 | Slow |
| pH Relay / Cl Relay Max Value, Min Value | Numbers (input box), 0.01 steps | `ph` (pH) | `gauge-full`, `gauge-empty` | 40082, 40084 / 40212, 40214 | Slow |
| pH Relay / Cl Relay Max Duty Cycle, Min Duty Cycle | Numbers (input box), % | | `speedometer`, `speedometer-slow` | 40086, 40088 / 40216, 40218 | Slow |
| Standby | Binary sensor | | `pause-circle` / `play-circle` | coil 11 | Fast |
| No Flow, Cl Tank Empty, pH Tank Empty | Binary sensors | `problem` | `water-off` / `water-check`, `cup-off-outline` / `cup-outline` | coils 6, 7, 8 | Fast |
| Alarm Coils | Diagnostic sensor, text | | `alert-circle-outline` | coils 1–16 | Fast |
| Feeding Delay | Number (input box), minutes | `duration` | `timer-cog-outline` | 40522 | Slow |
| Tau | Number (input box), 0–30 | | `chart-bell-curve-cumulative` | 40520 | Slow |
| Flow Time | Number (input box), minutes | `duration` | `timer-sand-complete` | 40524 | Slow |
| Flow Mode | Select: Disabled, Reverse, Direct | | `cancel`, `swap-horizontal`, `waves-arrow-right` | 40526 | Slow |
| Priority | Select: No priority, pH priority | | `equal`, `sort-variant` | coil 58 | Slow |
| Units | Diagnostic sensor: Europe, USA | `enum` | `earth` | coil 47 | Slow |
| Temperature °F | Sensor, °F (USA format only; no device class) | | `thermometer` | 40052 | Medium |

Entity ids follow the names: `select.dosing_pump_ph_pulse_1_dosing_mode`,
`number.dosing_pump_ph_pulse_2_max_value`, `sensor.dosing_pump_cl_pulse_rate`,
`select.dosing_pump_ph_relay_dosing_mode`, `sensor.dosing_pump_cl_relay_state`.
Earlier versions named the pulse outputs "pH Relay 1/2" and "Cl Relay" (and,
before that, "pH ...", "pH Output 2 ...", "Cl Relay 1 ...") and the relay states
"pH Relay" / "Cl Relay"; an installation from before keeps its unique ids, and
an entity still on the id made from an old name moves to the new id with its
history and statistics.
