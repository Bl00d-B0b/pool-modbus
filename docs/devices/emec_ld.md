# EMEC LD series

The EMEC LD series pH/chlorine controllers (dosing pumps), such as the
**LDPHCL** (firmware 5.1.4). On an LDPHCL, channel 1 measures pH and channel 2
free chlorine in mg/L. This page describes the device type as released in
version 2026.10.0 of the Pool equipment integration (`pool_modbus`).

Registers are numbered as in EMEC's Modbus protocol document (`4xxxx`); the
wire address is the address a request sends.

## Setup

- Device type: EMEC LD series pH/Cl controller. Default name **Dosing pump**,
  default Modbus ID 1.
- Entity ids start with `dosing_pump`, followed by the device's optional entity
  id prefix and the entity's name: `sensor.dosing_pump_ph_level`, or with prefix
  `2` `sensor.dosing_pump_2_ph_level`. The ids below are for a device without
  a prefix.
- When a device is added, the integration reads it once and accepts it as an
  EMEC LD only if both measurement divisors (40004, 40008) are 1, 10, 100 or 1000.
- Modbus RTU on the controller's RS-485 port, directly or through a Modbus TCP
  gateway. When a serial device is added, the form offers 9600 baud, 8 data
  bits, no parity and 1 stop bit; set the line settings the controller uses.
  The integration keeps at least 100 ms between requests, as the
  protocol document asks.

### Optional parts

Each optional part can be switched off with its checkbox when the device is
added or later in **Configure**. All are on by default. A part that is off has
no entities, and the regular polls read only the parts that are on (the read
when a device is set up reads all of them).

| Part | Checkbox | Entities |
|---|---|---|
| Dosing settings | Read the dosing settings (outputs, alarms, timing) | the dosing modes and settings of the five outputs; timing, flow and priority; dosing alarm, probe failure and out of range settings |
| Alarms and standby | Read the alarms and standby | Standby and the 13 alarm binary sensors |
| mA outputs | Read the mA outputs | the mA output modes and ranges |
| Data logging | Read the data logging settings | Logging Data, Log Start Time, Log Interval |
| Label and PIN | Read the label and PIN | Label, PIN |
| Probe voltages | Read probe voltages | pH Probe Voltage, Cl Probe Voltage |
| Clock | Read the clock | RTC |

The measurements (pH Level, Cl Level, Temperature), the pulse rates and the
relay states belong to no optional part and always exist.

### Read groups

Each value is read in one of three groups, each with its own polling interval
set in Configure:

- **Fast**: the relay states, Standby and the alarm coils.
- **Medium**: pH, Cl, temperature, the pulse rates and the probe voltages.
- **Slow**: all settings, the RTC, the label and the PIN.

Each group reads only its own values, and every group also reads both
measurement divisors (40004, 40008) to check the reply (see Read checks).

### Entity sections

Entities appear in Home Assistant's device page sections as follows:

- **Sensor**: the measurements, pulse rates, relay states, probe voltages and
  Standby (no entity category).
- **Control**: the dosing modes and setpoint settings of the five outputs (no
  entity category).
- **Configuration**: all other writable settings.
- **Diagnostic**: the 13 alarm binary sensors, the RTC, the Label and the PIN.

All entities are enabled by default except **RTC** and **PIN**.

### Settings a mode does not use

As in the controller's own menu, a setting the current working mode does not
use has no entity (for example Pulse Speed while a pulse output is in
Proportional mode). At setup the integration creates only the entities of the
settings in use and removes the registry entries of the others. When the
settings in use change, the entry reloads and the entities follow, with the
same entity ids: at once after a write from Home Assistant, and after two slow
reads in a row that agree for a change made on the keypad. The values of a
setting without an entity stay stored in the controller.

## Communication and addressing

### Functions

| Function | Use |
|---|---|
| 03 Read holding registers | all values |
| 06 Write single register | every writable value, one value per request |
| 01 Read coils | alarm and configuration coils, always 8 at a time |
| 05 Write single coil | the configuration coils 44, 48–53, 58–63, 75 and 90 only |

The integration sends no function 16 or multi-coil writes to this device type.

### Byte addressing

The controller is byte-addressed: every value takes two addresses and sits at an
odd wire address.

```
wire address = register - 40001         (40002 -> 1, 40052 -> 51)
```

A read of N registers from an odd wire address returns the N values at
`a, a+2, a+4, ...`, so consecutive values of a read are two wire addresses
apart. The integration numbers values by index, `index = (register - 40002) / 2`,
and sends a request for index i to wire address `2 * i + 1`. A request reads up
to 125 values.

The read when a device is set up reads every value, in three holding register
reads and seven coil reads:

| Read | Wire address, count | Registers or coils |
|---|---|---|
| Holding | 1, 111 | 40002–40222 |
| Holding | 427, 58 | 40428–40542 |
| Holding | 843, 14 | 40844–40870 (label) |
| Coils | 0, 8 and 8, 8 | alarm coils 0–13 |
| Coils | 40, 8 | 44 |
| Coils | 48, 8 and 56, 8 | 48–63 |
| Coils | 72, 8 | 75 |
| Coils | 88, 8 | 90 |

The regular polls read per group (fast, medium, slow) only the registers and
coils of that group's values, for the parts that are on.

### Coil reads

Coils are read 8 at a time, starting at a multiple of 8. The controller returns
each block of 8 with its bits in reverse order: coil `start + n` is in bit
`7 - n` of the reply. Coils are addressed as in the protocol document; the
alarm coils sit at the document's alarm number − 1.

### Writes

Every write is checked and confirmed:

1. The value is checked against its range; a value outside it is refused.
2. The device is read (once more after 0.5 s if that read fails). If it already
   holds the value, nothing is written.
3. The value is written with function 06 (registers) or 05 (coils).
4. The device is read every 0.25 s until it shows the new value, for up to 5 s;
   otherwise the write fails with an error.

A write that gets no reply (a gateway exception 0x0B, "gateway target device
failed to respond", or a timeout) is decided by the read-back, since the
controller can store a value after its gateway has stopped waiting. After every
write all read groups are read again.

### Value formats

| Format | Values | Decode |
|---|---|---|
| 16-bit word | readings, divisors, temperature, probe voltages, setpoints | the word, then scaled |
| One byte in the high half | relay states (40024, 40032), pulse rates (40026–40030) | `word >> 8` |
| Two bytes packed | clock (40044–40048) | high byte and low byte |
| Two characters | label (40844–40870) | first character in the low byte |

The readings, temperature and probe voltages are signed (a probe voltage can be
negative). The divisors, log times, PIN and label words are unsigned.
A pulse rate that reads `0xFF` is shown as 0.

### Read checks

The controller now and then answers a read with zeros. The integration refuses
a read in which a measurement divisor is 0. A value that was not 0 and reads 0
(pH and Cl readings, temperature, probe voltages, relay states) is refused once
and accepted if the next read also gives 0. Pulse rates are not checked, as
they drop to 0 whenever dosing stops. A refused or failed read keeps the last
values for up to 60 s before the entities become unavailable.

## Measurements

All are sensors with state class measurement, read with function 03 in the
medium group, with no entity category.

| Entity | Entity id | Register (wire) | Scaling, unit |
|---|---|---|---|
| pH Level | `sensor.dosing_pump_ph_level` | 40002 reading (1), 40004 divisor (3) | reading / divisor; device class pH |
| Cl Level | `sensor.dosing_pump_cl_level` | 40006 reading (5), 40008 divisor (7) | reading / divisor, mg/L |
| Temperature | `sensor.dosing_pump_temperature` | 40052 (51) | ÷ 10, °C; device class temperature |

The divisor is 1, 10, 100 or 1000.

## Outputs

The controller has three pulse outputs and two switching relays, named as on
the controller.

### Pulse outputs

| Output | Controller name | Settings registers (wire) | Rate register (wire) |
|---|---|---|---|
| pH Pulse 1 | CH1 PH PULSE 1 | 40068–40078 (67–77) | 40026 (25) |
| pH Pulse 2 | CH1 PH PULSE 2 | 40140–40150 (139–149) | 40028 (27) |
| Cl Pulse | CH2 CL2 PULSE | 40154–40164 (153–163) | 40030 (29) |

Each output has these entities (shown for pH Pulse 1; the others use
`ph_pulse_2` and `cl_pulse`):

| Entity | Entity id | Platform | Register (pH Pulse 1 / pH Pulse 2 / Cl Pulse) | Range, unit | Exists |
|---|---|---|---|---|---|
| pH Pulse 1 Rate | `sensor.dosing_pump_ph_pulse_1_rate` | sensor | 40026 / 40028 / 40030, high byte | p/min | always |
| pH Pulse 1 Dosing Mode | `select.dosing_pump_ph_pulse_1_dosing_mode` | select | 40078 / 40150 / 40164 | ON/OFF (0), Proportional (1), Disabled (2) | always |
| pH Pulse 1 On Value | `number.dosing_pump_ph_pulse_1_on_value` | number | 40068 / 40140 / 40154 | ÷ 100; pH 0–14, Cl 0–10 mg/L, step 0.01 | mode not Disabled |
| pH Pulse 1 Off Value | `number.dosing_pump_ph_pulse_1_off_value` | number | 40070 / 40142 / 40156 | as On Value | mode not Disabled |
| pH Pulse 1 On Rate | `number.dosing_pump_ph_pulse_1_on_rate` | number | 40072 / 40144 / 40158 | 0–180 p/min | Proportional |
| pH Pulse 1 Off Rate | `number.dosing_pump_ph_pulse_1_off_rate` | number | 40074 / 40146 / 40160 | 0–180 p/min | Proportional |
| pH Pulse 1 Pulse Speed | `number.dosing_pump_ph_pulse_1_pulse_speed` | number | 40076 / 40148 / 40162 | 0–99 min | ON/OFF |

The Rate sensors are measurements in the medium group, with no optional part.
The Dosing Mode selects and the numbers belong to the dosing settings, are read
in the slow group and appear among the controls. The pH values have device
class pH; Pulse Speed has device class duration.

On Rate is the rate at the On Value and Off Rate the rate at the Off Value. In
Proportional mode one end runs at 0 p/min, so setting one rate sets the other
to 0: On Rate writes the On Rate, then Off Rate = 0; Off Rate writes the Off
Rate, then On Rate = 0.

A change of Dosing Mode writes these registers in this order, as the keypad
does; values the controller already holds are skipped, and nothing is written
if the output is already in that mode:

| New mode | Writes, in order |
|---|---|
| ON/OFF | On Rate = 100, Off Rate = 0, Pulse Speed = 1 if it is 0, mode = 0 |
| Proportional | Pulse Speed = 0, mode = 1 |
| Disabled | Pulse Speed = 0, mode = 2 |

### Switching relays

| Output | Controller name | Settings registers (wire) | State register (wire) |
|---|---|---|---|
| pH Relay | CH1 PH RELAY | 40082–40088 (81–87), mode 40092 (91) | 40032 (31) |
| Cl Relay | CH2 CL2 RELAY | 40212–40218 (211–217), mode 40222 (221) | 40024 (23) |

Each relay has these entities (shown for pH Relay; the Cl Relay uses
`cl_relay`):

| Entity | Entity id | Platform | Register (pH Relay / Cl Relay) | Range, unit | Exists |
|---|---|---|---|---|---|
| pH Relay State | `sensor.dosing_pump_ph_relay_state` | sensor (enum) | 40032 / 40024, high byte | Disabled (0), On (1), Off (2) | always |
| pH Relay Dosing Mode | `select.dosing_pump_ph_relay_dosing_mode` | select | 40092 / 40222 | Proportional PWM (0), ON/OFF (1), Fixed PWM (2), Disabled (3) | always |
| pH Relay On Value | `number.dosing_pump_ph_relay_on_value` | number | 40082 / 40212 | ÷ 100; pH 0–14, Cl 0–10 mg/L, step 0.01 | mode not Disabled |
| pH Relay Off Value | `number.dosing_pump_ph_relay_off_value` | number | 40084 / 40214 | as On Value | mode not Disabled |
| pH Relay Max Duty Cycle | `number.dosing_pump_ph_relay_max_duty_cycle` | number | 40086 / 40216 | 0–100 % | Proportional PWM |
| pH Relay Min Duty Cycle | `number.dosing_pump_ph_relay_min_duty_cycle` | number | 40088 / 40218 | 0–100 % | Proportional PWM |
| pH Relay On Period | `number.dosing_pump_ph_relay_on_period` | number | 40086 / 40216 | 0–250 s | Fixed PWM |
| pH Relay Off Period | `number.dosing_pump_ph_relay_off_period` | number | 40088 / 40218 | 0–250 s | Fixed PWM |

The State sensors have no optional part and are read in the fast group. The
other entities belong to the dosing settings, are read in the slow group and
appear among the controls.

The relay switches on at the On Value and off at the Off Value. The duty cycle
is the share of the relay's 100 s cycle it is on, at the On Value (Max) and at
the Off Value (Min). On Period and Off Period are Fixed PWM's on and off times;
they use the same registers as the duty cycles.

Writes:

- A duty cycle above 0 is written, then the other duty cycle is set to 0. A duty
  cycle of 0 is written alone. On Period and Off Period are written alone.
- A change of Dosing Mode to ON/OFF or Disabled first writes Max Duty Cycle
  (On Period) = 100 and Min Duty Cycle (Off Period) = 0, then the mode. A change
  to Proportional PWM or Fixed PWM writes only the mode.

## Alarms

The alarm coils are read with function 01 (two reads of 8 coils, from 0 and
from 8) in the fast group. All are binary sensors with device class problem,
in the diagnostic section, except Standby, which has no device class and no
entity category. They belong to the "Alarms and standby" part.

| Entity | Entity id | Coil address | Protocol alarm |
|---|---|---|---|
| pH Tank 1 Empty | `binary_sensor.dosing_pump_ph_tank_1_empty` | 0 | 01 Ch1 level 1 |
| pH Tank 2 Empty | `binary_sensor.dosing_pump_ph_tank_2_empty` | 1 | 02 Ch1 level 2 |
| Cl Tank Empty | `binary_sensor.dosing_pump_cl_tank_empty` | 2 | 03 Ch2 level |
| No Flow | `binary_sensor.dosing_pump_no_flow` | 3 | 04 Flow |
| pH Out of Range | `binary_sensor.dosing_pump_ph_out_of_range` | 4 | 05 Out of range alarm 1 |
| Cl Out of Range | `binary_sensor.dosing_pump_cl_out_of_range` | 5 | 06 Out of range alarm 2 |
| Cl Pulse Dosing Alarm | `binary_sensor.dosing_pump_cl_pulse_dosing_alarm` | 6 | 07 Dosing alarm ch2 (out IS) |
| Cl Relay Dosing Alarm | `binary_sensor.dosing_pump_cl_relay_dosing_alarm` | 7 | 08 Dosing alarm ch2 (out relay) |
| pH Pulse 1 Dosing Alarm | `binary_sensor.dosing_pump_ph_pulse_1_dosing_alarm` | 8 | 09 Dosing alarm ch1 (out IS1) |
| pH Relay Dosing Alarm | `binary_sensor.dosing_pump_ph_relay_dosing_alarm` | 9 | 10 Dosing alarm ch1 (out relay) |
| pH Pulse 2 Dosing Alarm | `binary_sensor.dosing_pump_ph_pulse_2_dosing_alarm` | 10 | 11 Dosing alarm ch1 (out IS2) |
| pH Probe Fail | `binary_sensor.dosing_pump_ph_probe_fail` | 11 | 12 Probe fail ch1 |
| Cl Probe Fail | `binary_sensor.dosing_pump_cl_probe_fail` | 12 | 13 Probe fail ch2 |
| Standby | `binary_sensor.dosing_pump_standby` | 13 | 14 Stby |

No Flow is on while the controller sees no flow and holds dosing. Standby is on
while the controller is held in standby, with its outputs off.

## Alarm settings

All belong to the dosing settings part, are read in the slow group and are
configuration entities. Registers use functions 03/06, coils 01/05.

### Dosing alarm

A time per channel after which the controller raises the channel's dosing
alarm, and what it then does.

| Entity | Entity id | Platform | Register or coil (wire) | Range, options | Exists |
|---|---|---|---|---|---|
| pH Dosing Alarm Time | `number.dosing_pump_ph_dosing_alarm_time` | number | 40428 (427) | 0 = off, 1–999 min | always |
| Cl Dosing Alarm Time | `number.dosing_pump_cl_dosing_alarm_time` | number | 40430 (429) | 0 = off, 1–999 min | always |
| pH Dosing Alarm Action | `select.dosing_pump_ph_dosing_alarm_action` | select | coil 44 | Dose (off), Stop (on) | while the time is above 0 |
| Cl Dosing Alarm Action | `select.dosing_pump_cl_dosing_alarm_action` | select | coil 59 | Dose (off), Stop (on) | while the time is above 0 |

### Probe failure

The same pattern, with times of 0 (off) or 100–999 min; 1–99 is refused.

| Entity | Entity id | Platform | Register or coil (wire) | Range, options | Exists |
|---|---|---|---|---|---|
| pH Probe Failure Time | `number.dosing_pump_ph_probe_failure_time` | number | 40444 (443) | 0 = off, 100–999 min | always |
| Cl Probe Failure Time | `number.dosing_pump_cl_probe_failure_time` | number | 40446 (445) | 0 = off, 100–999 min | always |
| pH Probe Failure Action | `select.dosing_pump_ph_probe_failure_action` | select | coil 75 | Dose (off), Stop (on) | while the time is above 0 |
| Cl Probe Failure Action | `select.dosing_pump_cl_probe_failure_action` | select | coil 90 | Dose (off), Stop (on) | while the time is above 0 |

### Out of range

Per channel a High and a Low limit, each switched on by its coil, and a time
and action shared by both. Limits are stored × 100. Shown for pH; the Cl
entities use `cl_range_…`.

| Entity | Entity id | Platform | pH register or coil (wire) | Cl register or coil (wire) | Range, options | Exists |
|---|---|---|---|---|---|---|
| pH Range High | `switch.dosing_pump_ph_range_high` | switch | coil 51 | coil 61 | on/off | always |
| pH Range High Value | `number.dosing_pump_ph_range_high_value` | number | 40438 (437) | 40454 (453) | pH 0.10–14.00; Cl 0.10–10.00 mg/L; step 0.01 | while High is on |
| pH Range Low | `switch.dosing_pump_ph_range_low` | switch | coil 52 | coil 62 | on/off | always |
| pH Range Low Value | `number.dosing_pump_ph_range_low_value` | number | 40442 (441) | 40458 (457) | pH 0.00–13.90; Cl 0.00–9.90 mg/L; step 0.01 | while Low is on |
| pH Range Time | `number.dosing_pump_ph_range_time` | number | 40440 (439) | 40456 (455) | 0–99 min | while High or Low is on |
| pH Range Action | `select.dosing_pump_ph_range_action` | select | coil 53 | coil 63 | Dose (off), Stop (on) | while High or Low is on |

The alarm itself shows as pH Out of Range and Cl Out of Range (alarm coils 4
and 5).

## Timing, flow and priority

All belong to the dosing settings part, are read in the slow group and are
configuration entities.

| Entity | Entity id | Platform | Register or coil (wire) | Range, options | Exists |
|---|---|---|---|---|---|
| Tau | `number.dosing_pump_tau` | number | 40520 (519) | 0–30, no unit | always |
| Feeding Delay | `number.dosing_pump_feeding_delay` | number | 40522 (521) | 0–60 min | always |
| Flow Time | `number.dosing_pump_flow_time` | number | 40524 (523) | 0–99 min | Flow Mode Reverse or Direct |
| Flow Mode | `select.dosing_pump_flow_mode` | select | 40526 (525) | Disabled (0), Reverse (1, N.O. contact), Direct (2, N.C. contact) | always |
| Priority | `select.dosing_pump_priority` | select | coil 58 | No priority (on), pH priority (off) | always |

Tau is the controller's measurement filter setting. Feeding Delay is how long
dosing waits after flow or power returns. Flow Time is how long the flow
contact must hold its new state before the controller acts on it. Setting Flow
Mode to Disabled first writes Flow Time = 0, then the mode; with Flow Mode
Disabled the controller doses without flow.

## mA outputs

Per channel the output range and the readings at its ends: Max is the reading
at 20 mA, Min the reading at 0 or 4 mA. All belong to the "mA outputs" part,
are read in the slow group and are configuration entities.

| Entity | Entity id | Platform | Register or coil (wire) | Scaling, range |
|---|---|---|---|---|
| pH mA Output Mode | `select.dosing_pump_ph_ma_output_mode` | select | coil 49 | 0/20 mA (off), 4/20 mA (on) |
| pH mA Output Max | `number.dosing_pump_ph_ma_output_max` | number | 40124 (123) | ÷ 100, pH 0–14, step 0.01 |
| pH mA Output Min | `number.dosing_pump_ph_ma_output_min` | number | 40126 (125) | ÷ 100, pH 0–14, step 0.01 |
| Cl mA Output Mode | `select.dosing_pump_cl_ma_output_mode` | select | coil 60 | 0/20 mA (off), 4/20 mA (on) |
| Cl mA Output Max | `number.dosing_pump_cl_ma_output_max` | number | 40196 (195) | ÷ 100, 0–10 mg/L, step 0.01 |
| Cl mA Output Min | `number.dosing_pump_cl_ma_output_min` | number | 40198 (197) | ÷ 100, 0–10 mg/L, step 0.01 |
| Temperature mA Output Mode | `select.dosing_pump_temperature_ma_output_mode` | select | coil 50 | 0/20 mA (off), 4/20 mA (on) |
| Temperature mA Output Max | `number.dosing_pump_temperature_ma_output_max` | number | 40128 (127) | ÷ 10, 0–99.9 °C, step 0.1 |
| Temperature mA Output Min | `number.dosing_pump_temperature_ma_output_min` | number | 40130 (129) | ÷ 10, 0–99.9 °C, step 0.1 |

## Data logging

All belong to the "Data logging" part, are read in the slow group and are
configuration entities. The times are stored as hour × 100 + minute.

| Entity | Entity id | Platform | Register or coil (wire) | Range | Exists |
|---|---|---|---|---|---|
| Logging Data | `switch.dosing_pump_logging_data` | switch | coil 48 | on/off | always |
| Log Start Time | `time.dosing_pump_log_start_time` | time | 40530 (529) | 00:00–23:59 | while logging is on |
| Log Interval | `time.dosing_pump_log_interval` | time | 40532 (531) | 00:00–23:59 | while logging is on |

Log Start Time is the time logging starts, Log Interval the time between log
records (the menu's "Every").

## Label and PIN

Both belong to the "Label and PIN" part, are read in the slow group and are
diagnostic entities.

| Entity | Entity id | Platform | Register (wire) | Format | Enabled by default |
|---|---|---|---|---|---|
| Label | `sensor.dosing_pump_label` | sensor (read only) | 40844–40870 (843–869) | 28 characters, 2 per register | yes |
| PIN | `text.dosing_pump_pin` | text (password) | 40542 (541) | 4 digits "0000"–"9999", stored as the number | no |

The label is the instrument name set in the controller's RS485 setup, shown on
its display as 2 lines of 14 characters. Each register holds two characters,
the first in the low byte; unused characters are `-` and the integration shows
the label without the trailing `-`.

The PIN is the keypad password. It is written with function 06 as the number
(0042 is stored as 42); a stored value above 9999 shows as unknown. In the
integration's bus traffic log its value is written as 0 (the address is kept),
in both debug lines and traffic warnings.

## Probe voltages

Sensors with device class voltage and state class measurement, in the
"Probe voltages" part, read in the medium group, no entity category.

| Entity | Entity id | Register (wire) | Unit |
|---|---|---|---|
| pH Probe Voltage | `sensor.dosing_pump_ph_probe_voltage` | 40056 (55) | mV, signed |
| Cl Probe Voltage | `sensor.dosing_pump_cl_probe_voltage` | 40058 (57) | mV, signed |

## Clock

| Entity | Entity id | Platform | Registers (wire) |
|---|---|---|---|
| RTC | `sensor.dosing_pump_rtc` | sensor, device class timestamp | 40044–40048 (43–47) |

In the "Clock" part, read in the slow group, diagnostic, disabled by default.

The RTC is read in Europe format, as date and 24 h time: 40044 holds the month
(high byte) and day (low byte), 40046 the hour (high byte, 24 h) and the
year − 2000 (low byte), 40048 the minute (low byte). The controller has no
seconds over Modbus. The time is taken as local time; an invalid date shows as
unknown.

## Differences from the protocol document

The integration relies on these points where the controller differs from
EMEC's protocol document:

- The relays' working mode is at 40092 (pH Relay) and 40222 (Cl Relay), not at
  40090 and 40220.
- The Cl out of range Low coil is 62 and its Dose/Stop coil 63; the document
  prints 63 and 64.
- The Cl out of range time is at 40456, which the document does not list.
- The temperature mA output's Max and Min are 40128 and 40130, which the
  document labels ch2.
- The alarm coils sit at the document's alarm number − 1 (alarm 01 is coil
  address 0, alarm 14 coil address 13).
- The label's first character of each register is in the low byte.
- Relay states and pulse rates are one byte in the high half of the register,
  not 16-bit values.

## Not included

**Setting the clock** is not included. The controller's clock setpoint is
41624–41628, but on firmware 5.1.4 every write through it resets the clock's
year to 2000, which only the keypad can correct.

**12/24 h and date format.** The AM/PM coils 45 and 46, the Europe/USA format
coil 47 and the log time AM/PM coils 54 and 55 are not included. The
integration reads the controller in Europe format: the date as day and month,
the time in 24 h and the temperature in °C. The clock has no seconds over
Modbus.

**Writing the label** is not included: the label is read only. The
controller's name editor keeps its own copy and writes it back when its menu is
left.

**Not readable over Modbus.** The controller does not make these readable over
Modbus, so they are not included: the log records, the firmware version, the
temperature probe voltage, the output manager, the mA Outputs menu's Enable /
Disable on alarm and the log's output setting.

**Compensation.** The compensation settings are not included. Cl compensation
by pH shows in coils 233 and 241, which the protocol document does not list;
pH compensation by temperature is not readable over Modbus.
