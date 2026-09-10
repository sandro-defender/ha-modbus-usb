# Changelog

## 2.1.34

### Combined Switches and panel stability

- Kept the Refresh button’s size stable during automatic updates; its icon now spins without replacing the button label.
- Replaced the R413E16 template’s legacy All Channels switch with a Combined Switch that sends the confirmed per-channel ON/OFF commands to channels 1–16.
- Added a phone-friendly R413E16 channel picker for Combined Switches. Create as many combinations as needed, such as CH-01 + CH-05 + CH-06, without typing commas.
- Existing legacy All Channels switches now offer a one-click conversion to the new Combined Switch behavior.

## 2.1.33

### Board Debug Lab

- Added Automatic Safe Discovery: it checks all four standard read functions at address 0, then automatically expands only the functions that answer to addresses 1–3.
- Added plain-language next-step advice for detected discrete inputs, input registers, holding registers, and coils, without making unsafe assumptions about writes.
- Improved discovery feedback with a clear two-step progress status and an independent stop control.

## 2.1.32

### Board Debug Lab

- Added a read-only input and sensor watcher. It repeatedly checks a selected function and highlights addresses whose reply changes when a board input changes.
- Added a live traffic feed to Board Debug showing recent RS-485 reads, writes, replies, timing, and errors while the page is open.

## 2.1.31

### Board Debug Lab

- Added a Stop button for a running safe probe. It stops queued requests after the current serial read returns.
- Simplified the manual-write guard: use one clear single-board checkbox instead of typing a confirmation phrase, while retaining the two final safety warnings.

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
