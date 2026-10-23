# Pool controller

Pool controller by Optika ir technologija for filtration, the filter valve
(backwash), the pool cover, the pool light and the water level. In the
integration "Pool equipment" (domain `pool_modbus`, version 2026.10.2) it is the
device type **Pool controller**; a new device is offered the name "Controller"
and Modbus ID 1.

## Communication

- Modbus RTU, 38400 8N1, on a serial port or through a Modbus TCP connection
  (for example a Modbus TCP-to-RS-485 gateway).
  When a serial device is added, the form offers 9600 baud, 8 data bits, no
  parity and 1 stop bit; choose 38400 8N1.
- Holding registers 40017–40040, sent as wire addresses 16–39 (address =
  register − 40001).
- Function 03 reads. The whole map, addresses 16–39, fits in one request of 24
  registers; each scan group reads the registers it needs in one request
  within that range.
- Function 06 writes one register (the command word 16, the backwash days 17).
  Function 16 writes several registers in one request (the backwash time 18–19,
  the clock 32–35).

## Register map

| Register | Address | Read/write | Content |
|---|---|---|---|
| 40017 | 16 | R/W | Switches and command pulses, below |
| 40018 | 17 | R/W | Backwash days as written: bit 0 Monday … bit 6 Sunday, 0 = off (0–127) |
| 40019 | 18 | R/W | Backwash hour as written (0–23) |
| 40020 | 19 | R/W | Backwash minute as written (0–59) |
| 40025 | 24 | R | Status bits, below |
| 40026 | 25 | R | Controller time: hour |
| 40027 | 26 | R | Controller time: minute |
| 40028 | 27 | R | Backwash days as saved by the controller (same bits as 40018) |
| 40029 | 28 | R | Backwash hour as saved |
| 40030 | 29 | R | Backwash minute as saved |
| 40033–40036 | 32–35 | W | Clock, layout used to set it (high byte / low byte): second / weekday (1 = Monday), hour / minute, month / day, century / year in the century |
| 40037–40040 | 36–39 | R | Clock, layout as read (high byte / low byte): second / minute, hour / weekday (1 = Monday), day / month, year in the century / century |

The schedule written to 40018–40020 counts only after the "save backwash
timers" pulse (40017 bit 15); until then 40028–40030 still show the schedule
the controller keeps.

Register 40017 (address 16):

| Bit | Kind | Meaning | Held |
|---|---|---|---|
| 0 | Switch | Filtration enabled | |
| 1 | Pulse | Manual filter backwash | 3.1 s |
| 2 | Pulse | Open the cover | 3.1 s |
| 3 | Pulse | Close the cover | 3.1 s |
| 4 | Pulse | Toggle the light | 0.5 s |
| 5 | Switch | Block filling up | |
| 14 | Pulse | Reset alarms | 0.5 s |
| 15 | Pulse | Save backwash timers | 0.5 s |

Register 40025 (address 24):

| Bit | Meaning |
|---|---|
| 0 | Filter pump running |
| 1 | Filter valve in filtration position |
| 2 | Filter valve in backwash position |
| 3 | Cover open |
| 4 | Filling up |
| 5 | Alarm: room flooding |
| 6 | Water level minimum |
| 7 | Water level low |
| 8 | Water level normal |
| 9 | Water level maximum |
| 10 | Water level monitoring off |
| 11 | Light on |

## Entities

Entity ids are `<platform>.pool_controller_<name>`; a device with the entity
id prefix `<prefix>` gets `<platform>.pool_controller_<prefix>_<name>`. Read
groups: fast every 5 s, slow every 15 s by default (set per device in
Configure). Section: Controls and Sensors have no entity category;
Configuration and Diagnostic are Home Assistant's entity categories. Every
entity is enabled by default.

| Entity | Entity id | Platform | Register / bit | Values | Read | Section | Optional part |
|---|---|---|---|---|---|---|---|
| Filtration | `switch.pool_controller_filtration` | Switch (`switch`) | 40017 bit 0 | on / off | Fast | Controls | |
| Block Filling | `switch.pool_controller_block_filling` | Switch (`switch`) | 40017 bit 5 | on / off | Fast | Controls | |
| Cover | `cover.pool_controller_cover` | Cover (`shutter`) | 40017 bits 2–3, 40025 bit 3 | open / closed | Fast | Controls | |
| Light | `light.pool_controller_light` | Light, on/off only | 40017 bit 4, 40025 bit 11 | on / off | Fast | Controls | |
| Start Backwash | `button.pool_controller_start_backwash` | Button | 40017 bit 1 | | Fast | Controls | |
| Filtration Mode | `sensor.pool_controller_filtration_mode` | Sensor (`enum`) | 40025 bits 1–2 | Backwashing, Filtering, Off | Fast | Sensors | |
| Water Level | `sensor.pool_controller_water_level` | Sensor (`enum`) | 40025 bits 6–10 | Off, Minimum, Low, Normal, Maximum, Unknown | Fast | Sensors | |
| Filter Pump | `binary_sensor.pool_controller_filter_pump` | Binary sensor (`running`) | 40025 bit 0 | on / off | Fast | Sensors | |
| Filling | `binary_sensor.pool_controller_filling` | Binary sensor (`running`) | 40025 bit 4 | on / off | Fast | Sensors | |
| Flooding Alarm | `binary_sensor.pool_controller_flooding_alarm` | Binary sensor (`problem`) | 40025 bit 5 | on / off | Fast | Sensors | |
| Water Level Monitoring | `binary_sensor.pool_controller_water_level_monitoring` | Binary sensor | 40025 bit 10, inverted | on / off | Fast | Sensors | |
| Backwash Monday … Backwash Sunday | `switch.pool_controller_backwash_monday` … `switch.pool_controller_backwash_sunday` | Switch (`switch`), one per weekday | 40018 bits 0–6; save pulse 40017 bit 15 | on / off | Slow | Configuration | Backwash schedule |
| Backwash Time | `time.pool_controller_backwash_time` | Time | 40019–40020; save pulse 40017 bit 15 | 00:00–23:59, any minute | Slow | Configuration | Backwash schedule |
| Saved Backwash Schedule | `sensor.pool_controller_saved_backwash_schedule` | Sensor | 40028–40030 | Text, below | Slow | Diagnostic | Backwash schedule |
| RTC | `sensor.pool_controller_rtc` | Sensor (`timestamp`) | 40037–40040 | Date and time | Slow | Diagnostic | Clock |
| Sync RTC | `button.pool_controller_sync_rtc` | Button | 40033–40036 | | Slow | Diagnostic | Clock |
| Reset Alarms | `button.pool_controller_reset_alarms` | Button | 40017 bit 14 | | Fast | Diagnostic | |

Values:

- **Filtration Mode:** Backwashing if 40025 bit 2 is set, else Filtering if
  bit 1 is set, else Off.
- **Water Level:** the first of Off (bit 10, monitoring off), Minimum (bit 6),
  Low (bit 7), Normal (bit 8), Maximum (bit 9) whose bit is set; Unknown if
  none is set.
- **Water Level Monitoring:** on while 40025 bit 10 is clear.
- **Cover:** closed while 40025 bit 3 is clear.
- **Backwash Monday … Sunday** and **Backwash Time** show the schedule as
  written (40018–40020).
- **Saved Backwash Schedule:** the schedule the controller keeps (40028–40030)
  as text: the days, then the time, e.g. "Friday 06:00", "Monday, Wednesday,
  Friday 06:00". Monday to Friday together read "Weekdays", Saturday and Sunday
  together "Weekend" (e.g. "Friday, Weekend 06:00"), all seven days "Every
  day", no day "Off".
- **RTC:** the clock from 40037–40040 (century × 100 + year), in the local time
  zone of Home Assistant; unknown when the registers do not hold a valid date.

## Optional parts

Each device has two optional parts, both on by default and switched in
Configure. While a part is off, its entities do not exist and the regular polls
read only the parts that are on (the read when a device is set up reads all of
them).

- **Backwash schedule:** Backwash Monday … Sunday, Backwash Time, Saved Backwash
  Schedule.
- **Clock:** RTC, Sync RTC.

With both parts off, the controller has no slow-group values.

## Writing

- **Register 40017 is written as a whole word.** The new word is computed from
  the integration's last read of the register block, with only the bits of
  the command or switch changed, and written with function 06. The integration
  does not use a read-modify-write inside the write: behind a gateway that
  caches reads, such a read can be stale and set a just-cleared bit again.
  Each write is confirmed by reading the block back until the bit shows as
  written (up to 5 s).
- **Switches** (Filtration, Block Filling) are not written when the read
  already shows them as asked.
- **Commands are pulses:** the bit is set, held, then cleared. The cover and
  the manual backwash are held 3.1 s, as the controller needs more than 3 s;
  the light toggle, alarm reset and schedule save 0.5 s. The clearing write is
  always sent, also when the set was not confirmed or a read does not show the
  bit set, so a command bit does not stay set.
- **The cover** is only pulsed when it is not already open or closed as asked.
  It takes a while to move, so the integration does not wait for the status;
  the cover entity follows 40025 bit 3.
- **The light** toggles, so it is only pulsed when 40025 bit 11 does not
  already show it as asked. The integration then waits up to 3 s for bit 11 to
  follow. Whether the light switches is the controller's decision; a toggle it
  does not carry out is no error, and the light entity keeps showing the real
  state.
- **Backwash days:** a weekday switch adds its day to, or takes it out of, the
  days as written in 40018; the new value is written to 40018 with function 06.
- **Backwash time:** hour and minute are written to 40019–40020 in one function
  16 request.
- **Saving the schedule:** after each days or time write the integration sends
  the save pulse (40017 bit 15), then reads until 40028–40030 show the new
  value (up to 5 s); a schedule the controller does not keep raises an error.
  Nothing is written when both the written and the saved schedule already hold
  the value asked for. Out-of-range days (above 127) and invalid times are
  refused before any request.
- **Sync RTC** writes the local time of Home Assistant to 40033–40036 in one
  function 16 request, then reads until the clock (40037–40040) is within 15 s
  of it (up to 5 s).

The read before a write is retried once after 0.5 s if it gets no answer.
Writes to one device run one at a time, so two commands cannot interleave
their writes of 40017. After a write the integration reads every scan group,
so all entities show the result.

## Not included

- 40026–40027 (controller time, hour and minute): no entity; the RTC sensor
  shows the full clock.
- The weekday in 40038 (low byte): no entity.
