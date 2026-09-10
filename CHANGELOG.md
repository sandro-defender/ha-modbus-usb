# Changelog

## 2.1.30

### Board Debug Lab

- Added a read-only unknown-board probe for the four standard Modbus read functions: coils, discrete inputs, holding registers, and input registers.
- Each probe result now shows the RTU request format, returned value or error, and a careful interpretation of what the response proves.
- Added a separately guarded manual-write lab. It requires an explicit single-board acknowledgement, typing `ONE BOARD`, and two confirmation prompts. No automatic write, reset, or configuration sweep is performed.

## 2.1.29

### Fixes and settings

- Fixed R413E16 switches failing with `CONF_DEVICES is not defined`.
- Added a confirmed R413E16 factory-reset command. It restores the documented connection defaults: 9600 baud and slave ID 1; power-cycle the board after sending it.

## 2.1.28

### Eletechsup R413E16

- Added every documented per-channel Modbus action to the tested template: toggle, interlock, one-second momentary pulse, and timed actions from 0 to 255 seconds.
- Added live channel feedback for the normal CH-01 through CH-16 switches.
- Added a compact phone-friendly Actions dialog with confirmations for commands that activate hardware or turn other channels off.

## 2.1.27

### Safety

- Corrected the R413E16 baud-rate control to the documented safe range: 1200–19200 baud. Higher choices were removed because the documented code `5` performs a factory reset.
- Added a concise R413E16 protocol reference beside the tested template, including verified commands, wiring notes, safe limits, and future feature candidates.

## 2.1.26

### Eletechsup R413E16

- Added an **All Channels** switch to the tested R413E16 template. It controls all 16 physical outputs and keeps the last commanded ON/OFF state visible in Home Assistant.
- Added optional group switches: create one switch for several channels by entering addresses such as `1, 5, 6` in the entity editor.
- Added confirmed R413E16 communication settings for board baud rate and slave ID. These settings are stored in the R413E16 template and update the integration after the board accepts them.

### Notes

- The R413E16 is the only template confirmed tested with real hardware.
- Group and all-channel switches are command-state controls. Their displayed state is the last command sent, not a physical read-back from the board.
