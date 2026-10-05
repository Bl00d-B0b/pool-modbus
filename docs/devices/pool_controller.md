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
- **Controller Last Update:** the clock from 40037–40040.

Read groups in Home Assistant: the switches and status bits fast (40017 and
40025, one request); the backwash schedule and the clock slow. Optional parts,
both on by default: **backwash schedule** (Backwash Day, Backwash Time, Saved
Backwash Schedule) and **clock** (Controller Last Update).

## Writing (not supported yet)

Switch bits must be written as a read-modify-write of 40017, so the other bits
are kept. Pulses set a bit, wait, and clear it: the cover and backwash need
more than 3 s, the light, alarm reset and schedule save about 0.5 s.
