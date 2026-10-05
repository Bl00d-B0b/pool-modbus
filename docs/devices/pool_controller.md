# Pool controller

A controller for filtration, the filter valve (backwash), the pool cover, the
pool light and the water level. Verified through a Modbus TCP-to-RS-485
gateway; the register map comes from the controller's register sheet and was
checked against live data.

## Communication

- Modbus RTU, 38400 8N1.
- Standard addressing: register *n* is address *n*.
- The library reads registers 16–39 in one request.

## Register map

| Address | Read/write | Content |
|---|---|---|
| 16 | R/W | Switches and command pulses, below |
| 17 | R/W | Backwash days as written: bit 0 Monday … bit 6 Sunday |
| 18 | R/W | Backwash hour as written (0–23) |
| 19 | R/W | Backwash minute as written (0–59) |
| 20–23 | | Unused |
| 24 | R | Status bits, below |
| 25 | R | Controller time: hour |
| 26 | R | Controller time: minute |
| 27–29 | R | Backwash days, hour and minute as saved by the controller |
| 30–31 | | Unused |
| 32–35 | R/W | Clock, layout used to set it: second/weekday, hour/minute, month/day, century/year (high/low byte) |
| 36–39 | R | Clock, layout as read: second/minute, hour/weekday, day/month, year/century (high/low byte) |

The written schedule (17–19) only takes effect after the "save backwash
timers" pulse; until then 27–29 still show the old schedule.

Register 16:

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

Register 24:

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

- **Filtration Mode:** Backwashing if bit 2 is set, else Filtering if bit 1,
  else Off.
- **Water Level:** the first match of Off (monitoring off), Minimum, Low,
  Normal, Maximum; Unknown if none is set.
- **Backwash Day / Time:** the schedule as written (17–19).
- **Saved Backwash Schedule:** what the controller keeps (27–29), e.g.
  "Friday 06:00", or "Off".
- **Controller Last Update:** the clock from registers 36–39.

## Writing (not supported yet)

Switch bits must be written as a read-modify-write of register 16, so the other
bits are kept. Pulses set a bit, wait, and clear it: the cover and backwash
need more than 3 s, the light, alarm reset and schedule save about 0.5 s.
