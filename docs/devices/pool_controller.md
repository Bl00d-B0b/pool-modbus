# Pool controller

A controller for filtration, the filter valve (backwash), the pool cover, the
pool light and the water level. Verified through a Modbus TCP-to-RS-485
gateway; the register map comes from the controller's register sheet and was
checked against live data.

## Communication

- Modbus RTU, 38400 8N1.
- Holding registers 40017–40040, sent as addresses 16–39 (address = register −
  40001). The controller's own register sheet numbers them by address (16–39).
- The whole register map reads in one request of 24 registers.

## Register map

| Register | Address | Read/write | Content |
|---|---|---|---|
| 40017 | 16 | R/W | Switches and command pulses, below |
| 40018 | 17 | R/W | Backwash days as written: bit 0 Monday … bit 6 Sunday |
| 40019 | 18 | R/W | Backwash hour as written (0–23) |
| 40020 | 19 | R/W | Backwash minute as written (0–59) |
| 40021–40024 | 20–23 | | Unused |
| 40025 | 24 | R | Status bits, below |
| 40026 | 25 | R | Controller time: hour |
| 40027 | 26 | R | Controller time: minute |
| 40028–40030 | 27–29 | R | Backwash days, hour and minute as saved by the controller |
| 40031–40032 | 30–31 | | Unused |
| 40033–40036 | 32–35 | R/W | Clock, layout used to set it: second/weekday, hour/minute, month/day, century/year (high/low byte) |
| 40037–40040 | 36–39 | R | Clock, layout as read: second/minute, hour/weekday, day/month, year/century (high/low byte) |

The written schedule (40018–40020) only takes effect after the "save backwash
timers" pulse; until then 40028–40030 still show the old schedule.

Register 40017 (address 16):

| Bit | Kind | Meaning |
|---|---|---|
| 0 | Switch | Filtration enabled |
| 1 | Pulse | Manual filter backwash |
| 2 | Pulse, over 3 s | Open the cover |
| 3 | Pulse, over 3 s | Close the cover |
| 4 | Pulse | Toggle the light |
| 5 | Switch | Block filling up |
| 14 | Pulse | Reset alarms |
| 15 | Pulse | Save backwash timers |

Register 40025 (address 24):

| Bit | Meaning |
|---|---|
| 0 | Filter pump running |
| 1 | Filter valve in filtration position |
| 2 | Filter valve in backwash position |
| 3 | Cover open |
| 4 | Filling up |
| 5 | Alarm: room flooding |
| 6 | Alarm: water level minimum |
| 7 | Water level normal (low) |
| 8 | Water level normal |
| 9 | Alarm: water level maximum |
| 10 | Water level monitoring off |
| 11 | Light on |

## Values shown by this library

- **Filtration Mode:** Backwashing if 40025 bit 2 is set, else Filtering if
  bit 1, else Off.
- **Water Level:** the first match of Off (monitoring off), Minimum, Low,
  Normal, Maximum; Unknown if none is set.
- **Backwash Day / Time:** the schedule as written (40018–40020).
- **Saved Backwash Schedule:** what the controller keeps (40028–40030), e.g.
  "Friday 06:00", or "Off".
- **RTC:** the clock from 40037–40040.

Read groups in Home Assistant: the switches and status bits fast (40017 and
40025, one request); the backwash schedule and the clock slow. Optional parts,
both on by default: **backwash schedule** (Backwash Day, Backwash Time, Saved
Backwash Schedule) and **clock** (RTC, Sync RTC).

## Writing

- **Switches** (40017 bits 0 and 5) are written as a read-modify-write of 40017,
  so the other bits are kept, and confirmed by reading them back.
- **Commands** are pulses: the bit is set, held, then cleared, each with a
  read-modify-write, and the clearing is confirmed by reading it back, so the
  next command starts from the current word even behind a gateway that answers
  reads from a cache. Open and close the cover (bits 2 and 3) and the manual
  backwash (bit 1) are held 3.1 s, as the controller needs more than 3 s; the
  light toggle (bit 4), alarm reset (bit 14) and schedule save (bit 15) 0.5 s.
- **The cover** is only told to move when it is not already open or closed as
  asked; it takes a while to move, so the status is not waited for.
- **The light** toggles, so it is only pulsed when it is not already as asked;
  the integration then waits up to 3 s for 40025 bit 11 to follow. Whether the
  light switches is the controller's decision: if the status does not follow,
  that is no error, the light keeps showing its real state, and a switch in
  Home Assistant goes back to it.
- **The backwash schedule**: the day is written to 40018 (one day bit, or 0 for
  off), the time to 40019–40020 in one request, then the save pulse; the write
  is confirmed when the controller shows it in 40028–40030.
- **The clock** is set by writing the local time to 40033–40036 in one request
  (second/weekday with 1 = Monday, hour/minute, month/day, century/year) and
  confirmed in 40037–40040.

Writes to one device run one at a time, so two commands cannot interleave their
read-modify-writes of 40017.

## In Home Assistant

| Entity | Type | Device class | Icon | Registers | Read |
|---|---|---|---|---|---|
| Filtration | Switch | `switch` | `air-filter` / `water-pump-off` | 40017 bit 0 | Fast |
| Block Filling | Switch | `switch` | `water-off` / `water-plus-outline` | 40017 bit 5 | Fast |
| Cover | Cover: open/close | `gate` | `pool` / `gate` (closed) | 40017 bits 2–3, 40025 bit 3 | Fast |
| Light | Light, on/off | | `lightbulb-on` / `lightbulb-off` | 40017 bit 4, 40025 bit 11 | Fast |
| Filtration Mode | Sensor: Backwashing, Filtering, Off | `enum` | `rotate-left`, `air-filter`, `water-pump-off` | 40025 bits 1–2 | Fast |
| Water Level | Sensor: Off, Minimum, Low, Normal, Maximum, Unknown | `enum` | `water-off`, `water-alert`, `water-minus`, `water-check`, `water-plus` | 40025 bits 6–10 | Fast |
| Filter Pump, Filling | Binary sensors | `running` | `water-pump`, `water-plus` | 40025 bits 0, 4 | Fast |
| Flooding Alarm | Binary sensor | `moisture` | `home-flood` / `home` | 40025 bit 5 | Fast |
| Water Level Monitoring | Binary sensor | | `water` / `water-off` | 40025 bit 10 | Fast |
| Start Backwash, Reset Alarms | Buttons | | `rotate-left`, `restore-alert` | 40017 bits 1, 14 | Fast |
| Backwash Day | Select: Off, Monday … Sunday | | `calendar-clock` | 40018, 40017 bit 15 | Slow |
| Backwash Time | Time | | `clock-edit` | 40019–40020, 40017 bit 15 | Slow |
| Saved Backwash Schedule | Sensor | | `calendar-check` | 40028–40030 | Slow |
| RTC | Diagnostic sensor | `timestamp` | `clock` | 40037–40040 | Slow |
| Sync RTC | Diagnostic button | | `home-clock` | 40033–40036 | Slow |
