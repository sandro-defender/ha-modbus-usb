# Changelog

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
