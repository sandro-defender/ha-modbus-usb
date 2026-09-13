# Changelog

## 2.1.83

### Collapsible device cards

- Device cards now start collapsed, with their contents available from an
  accessible header control.
- The device cards a user expands remain expanded through automatic updates and
  browser-panel reloads.

## 2.1.82

### R4D6F20 testing status

- Marked the Eletechsup R4D6F20 template as **Testing** based on current
  integration work. It remains subject to hardware verification.

## 2.1.81

### Template-driven M0 settings

- Made the Add Device M0 checkbox generic: it appears for every template that
  declares `device_controls.m0`.
- Moved M0 labels, explanations, profile behavior, and R413E16 polarity details
  into the matching template YAML files instead of model checks in the panel.

## 2.1.80

### M0 selection while adding a board

- Added the appropriate M0 checkbox directly to **Add Device from YAML
  Template** for R4D6F20 and R413E16.
- Creating an R4D6F20 with M0 shorted immediately installs its Command 2 coil
  and discrete-input profile. Creating an R413E16 records only its selected
  low- or high-level TTL output polarity.

## 2.1.79

### Combined R4D6F20 M0 templates

- Combined the documented R4D6F20 Command 1 (M0 open) and Command 2 (M0
  shorted) profiles in one template.
- The M0 checkbox now replaces the board's entity profile without writing to
  hardware: Command 1 uses holding-register relays; Command 2 uses relay coils
  and discrete inputs.
- Command 2 polling reads the 20 relay coils, two inputs, and two analog
  values in three grouped requests. Its all-relay controls use the documented
  coil map, while Command 1-only relay actions are hidden.
- Added R413E16 M0 wiring metadata and a board-tools checkbox: M0 open is
  low-level TTL output and M0 connected is high-level TTL output. It does not
  select a different Modbus protocol or send a board command.

## 2.1.78

### Faster R4D6F20 polling and M0 safety

- Reduced standard R4D6F20 Command 1 polling from 24 serial requests to three
  contiguous holding-register reads.
- Added an M0-short checkbox. The existing Command 1 template suspends reads
  and board commands when M0 is shorted, preventing an unsafe protocol mismatch.

## 2.1.77

### Discovery to Board Tools workflow

- Changed **Use this target** in Find RS-485 Devices to open Board Tools with
  a non-sending discovered target. It now clearly offers the next choice:
  select a configured board for template commands, or explicitly open the
  documented custom-command form.

## 2.1.76

### Template controls and discovered targets

- Added **Restore standard template controls** for existing devices created
  before their template command metadata was saved. This restores R4D6F20 and
  R413E16 board tools without changing the device's working entities.
- Scan results now include **Use this target**, which transfers the discovered
  slave ID and baud rate into Board Tools and the custom-command workspace.

## 2.1.75

### R4D6F20 product image

- Replaced the Eletechsup R4D6F20 multifunction relay-board image with the
  supplied current board illustration in the saved template and device card.

## 2.1.74

### Unified board diagnostics and tools

- Combined the visible unknown-board discovery workflow into one safe probe and
  signal watcher, while keeping the RS-485 activity log as the single source
  of request and error history.
- Added a board-tools launcher that uses saved template controls for R413E16
  and R4D6F20 boards, and routes boards without a command template to the
  documented custom-command form.
- Replaced the R4D6F20 prompt sequence with an accessible control dialog for
  board-ID readback, relay actions, baud rate, parity, and factory reset.
- Added an explicit confirmation before a custom Modbus write is sent.

## 2.1.73

### Serial connection stability

- Fixed an RS-485/USB serial disconnect that could occur when adding, deleting,
  or applying a board template. Home Assistant now rebuilds the affected
  entities without closing the active shared serial connection.
- Improved reconnecting after serial-configuration changes: the adapter now
  retries briefly while the operating system releases the previous port handle.

## 2.1.72

### Eletechsup R4D6F20 board controls

- Added a control menu for the R4D6F20 template: all-relay on/off, board-ID
  readback, baud-rate and parity settings, per-channel actions, and factory reset.
- Updated the bundled R4D6F20 protocol reference so its documented controls
  match the template and device card.
- All-relay commands, configuration changes, and reset require confirmation to
  help prevent accidental changes to a connected board.
- The R4D6F20 remains marked as untested. Its available documentation does not
  conclusively document a software slave-ID write, so this release safely reads
  the ID rather than offering an unsupported write.

## 2.1.71

### Reliable device toggles

- Fixed Enable / Disable so it no longer reloads the full integration or closes
  the shared serial port.
- Disabled-device entities now become unavailable in place and resume normally
  when the device is enabled again.

## 2.1.70

### Per-device enable control

- Added **Enable device** / **Disable device** controls on every device card.
- Disabled devices are excluded from RS-485 polling and their Home Assistant
  entities become unavailable until enabled again.
- Integration reload no longer waits for every Modbus register to answer, so
  an offline board cannot hold up Home Assistant startup.

## 2.1.67

### Brand-organized template browser

- Added bundled product or representative device images for every built-in template;
  the browser no longer depends on remote image links.
- Grouped templates by manufacturer in accessible, collapsed-by-default sections.
  A section keeps its expanded state while the panel refreshes, and manufacturers
  containing saved Home Assistant templates remain first.
- Added a purpose-built local illustration for the Generic Modbus template.

## 2.1.66

### Eletechsup 23IOC24 NPN template

- Added an **untested** factory-default (Command 1 / M0 open) template for
  Eletechsup's 23IOC24_NPN 24DI / 24DO RS-485 remote I/O board.
- The template creates 24 live-feedback NPN output switches and 24 NPN digital
  input entities from the manufacturer’s documented holding-register map.
- Included a concise command reference with the manufacturer product and manual
  links, while leaving untested board-wide control modes disabled.

## 2.1.65

### Eletechsup R4D6F20 template

- Added an **untested** factory-default (Command 1 / M0 open) template for
  Eletechsup's R4D6F20 20-channel RS-485 relay and I/O board.
- The template adds all 20 relay channels with documented FC03 state feedback,
  two PNP digital inputs, a 4–20 mA input, and a 0–10 V input.
- Included a concise register-map reference beside the template, including the
  official manufacturer manual and product-page links.

## 2.1.64

### Compact devices and saved-template priority

- Templates saved in Home Assistant now appear first and show a **Saved in HA**
  badge.
- Combined Switch channel lists stay on one compact line with a focusable,
  horizontal scroll area, preventing long channel selections from making device
  cards excessively tall.

## 2.1.63

### Restart after update

- After a successful in-panel HACS update, the single action button now becomes
  **Restart Home Assistant**. It restarts Home Assistant directly from the
  panel after a clear confirmation.
- Restart requests are limited to Home Assistant administrators.

## 2.1.62

### Accurate RS-485 connection health

- Fixed the diagnostic health counters to include every logged Modbus request,
  including switch writes, instead of only scheduled polling reads.
- Added the most recent operation and its time to the Connection Health cards,
  so accepted R413E16 commands immediately appear as real serial activity.

## 2.1.61

### Mobile header redesign

- Rebuilt the header for phones: connection status stays beside the controller
  name, the hub selector gets its own row, and the core actions form a clear,
  touch-friendly action row.
- The refresh action is an accessible icon button on small screens, preserving
  space for **Check update** and **Add device** without horizontal overflow.
- Made mobile navigation tabs a consistent grid so they stay readable and do
  not create a horizontal scrolling menu.

## 2.1.60

### Simpler update action

- Replaced the separate **Check update** and **Update** buttons with one stable
  action. It says **Check update** until a release is found, then becomes
  **Update <version>**.

## 2.1.59

### HACS-aware update check

- The panel's **Check update** button now refreshes this repository's HACS
  information first, so HACS receives the latest release and update-entity
  state before the update result is shown.
- The normal GitHub check still works when HACS is not installed or the user
  does not have permission to refresh HACS.

### Faster live switch state

- The panel now listens directly for Home Assistant state-change events for
  its configured entities. Switches, sensors, and dashboard cards update
  immediately after a state changes, without waiting for the next full refresh.

## 2.1.57

### HACS update fallback

- Fixed the Update button when GitHub has published a release before HACS has
  refreshed its update entity. It now opens the verified GitHub release page
  instead of reporting a failed HACS update.

## 2.1.56

### Reliability and safety audit

- Fixed semantic-version comparison for the update check, including releases
  with a shortened version such as `2.1`.
- Restricted installation of an update to Home Assistant administrators.
- Modbus write-service failures now fail the calling automation instead of
  only writing a log message.
- Prevented unhandled background refresh errors during device disconnects or
  integration reloads.

## 2.1.55

### Manual update check

- Added **Check update** to the controller header. It compares the installed
  version with the latest GitHub release and reveals an Update button only
  when a newer release exists.
- Update starts through the matching HACS update entity when available; on
  installations without HACS, it opens the verified GitHub release page.

## 2.1.54

### Direct selected-entity state publication

- Combined Switch now directly publishes ON/OFF to the exact loaded HA channel
  entities it controls after all writes succeed. This bypasses delayed
  coordinator callbacks; the existing full board refresh still reconciles the
  state with the physical device.

## 2.1.53

### Full device state read-back after switching

- Every successful R413E16 switch command now schedules a complete device
  refresh. HA reads all configured channel states from the board after a
  Combined Switch command instead of relying only on the accepted write.
- Panel-originated switch commands wait for the same full refresh before they
  report success. No sidebar UI changes were made.

## 2.1.52

### Targeted Combined Switch entity updates

- After a successful Combined Switch command, the backend now explicitly
  writes the Home Assistant state of each selected CH entity. The sidebar UI
  is unchanged; this affects only the actual HA entities controlled by the
  Combined Switch.

## 2.1.51

### Existing template-state compatibility

- Normalize saved switch settings before recognizing the R413E16 protocol.
  Combined Switch commands now synchronize the individual CH entities even if
  an older sidebar edit stored addresses or command values as text.
- Normalize `state_on_value` before calculating an HA switch state, fixing
  existing configured channels without requiring them to be recreated.

## 2.1.50

### Stability rollback

- Reverted the 2.1.49 per-channel callback change, which could disrupt normal
  Home Assistant switch updates. Restored the proven 2.1.48 entity-feedback
  path while retaining the R413E16 coordinator synchronization fixes.

## 2.1.49

### Combined Switch channel synchronization

- Combined Switch commands now publish a confirmed per-channel state map to
  every affected Home Assistant switch. The 16 individual CH entities update
  immediately after a combined ON/OFF command.
- Combined Switches are no longer treated as a physical channel; their state
  is derived only from the selected channel feedback.

## 2.1.48

### Immediate R413E16 entity feedback

- R413E16 switches now subscribe directly to verified board-state updates.
  A manual **Read channel states**, panel command, or combined command writes
  the updated state through each real Home Assistant entity immediately.

## 2.1.47

### Reliable Home Assistant state synchronization

- Fixed R413E16 state refreshes being skipped when the raw coordinator data
  looked unchanged. Verified channel reads now always notify Home Assistant
  entities, so CH and Combined Switch states follow the board response.
- Removed the unsupported direct state-machine override that could be replaced
  by a later entity update.
- Preserve numeric switch command/state values after editing a template entity,
  preventing string values from breaking R413E16 state matching.
- Apply a template address offset to every address in a Combined Switch.

## 2.1.46

### Forced Home Assistant state-machine synchronization

- R413E16 channel feedback now explicitly writes the verified `on`/`off` state to every registered Home Assistant CH switch and Combined Switch, preserving entity attributes.
- This closes the remaining gap where the custom panel displayed correct Modbus feedback but Home Assistant cards retained an old state.

## 2.1.45

### Immediate Home Assistant R413E16 updates

- Added an explicit state publication path for R413E16 channel reads and commands. When the board reports channel states, HA immediately rewrites the state of every affected CH switch and its Combined Switch.
- This fixes cases where the sidebar correctly showed verified Modbus feedback but Home Assistant dashboards still displayed old switch states.

## 2.1.44

### R413E16 verified feedback display

- Channel-state reads now publish a complete updated coordinator snapshot, immediately updating Home Assistant’s individual CH-01 through CH-16 entities.
- The device panel now receives and prioritizes verified R413E16 function-03 feedback while Home Assistant updates its normal state snapshot. A channel read as `1` displays ON; `0` displays OFF.

## 2.1.43

### R413E16 switch-state correction

- Fixed the device panel to use each switch’s exact Home Assistant entity ID instead of guessing from its name, preventing old or similarly named entities from showing the wrong state.
- **Read channel states** now refreshes the panel after synchronizing the board feedback, so all CH-01 through CH-16 toggles immediately show `1 = ON` and `0 = OFF`.
- Fixed R413E16 Combined Switches to derive their state from the live status of their selected channels. A group is ON only when every selected channel is ON.

## 2.1.42

### Unified Diagnostics & Debug workspace

- Combined the separate Diagnostics and Board Debug tabs into one **Diagnostics & Debug** workspace, reducing phone navigation to six tabs.
- Placed connection health, device scanning, direct Modbus tools, and the complete live RS-485 log before the safe board-discovery tools.
- Removed the duplicate traffic feed: the activity log is now the single source for every read, write, reply, and error.
- Kept automatic discovery, safe read probes, and input watching together below a clear safe-tools heading; the hardware-risk manual write is folded by default.

## 2.1.41

### R413E16 real state synchronization

- Confirmed the tested eletechsup R413E16 function-03 response: `1` is ON and `0` is OFF.
- **Read channel states** now synchronizes all configured CH-01 through CH-16 switches with the board’s live feedback, replacing any stale command state.
- Regular automatic polling now also uses this live feedback, so manual or external channel changes appear in Home Assistant after the configured scan interval.

## 2.1.40

### Diagnostic log zero values

- Fixed the RS-485 diagnostic log copy action: a valid Modbus response of `0` is now preserved and copied as `0` instead of appearing blank.
- The on-screen log and copied log now use the same response formatting.

## 2.1.39

### R413E16 raw state check

- Added **Read channel states** to request function-03 holding-register data from all 16 channels on demand.
- The device card now shows every raw reply, including errors, so physical feedback support can be verified instead of inferred from switch UI state.
- Kept confirmed command state separate from raw device read-back to prevent a known false-OFF reply from making an energized channel impossible to turn off in Home Assistant.

## 2.1.38

### R413E16 command-state control

- Fixed boards that physically switch correctly but return a false OFF read-back value. R413E16 channel switches now retain the last accepted integration command when the board’s status register is unreliable.
- All ON/OFF, Combined Switches, and interlock now propagate their confirmed result to every affected CH-01 through CH-16 switch immediately, so individual OFF controls remain available after a group command.
- The displayed R413E16 state is now explicitly command state after an integration command; manually changing hardware outside Home Assistant cannot be detected reliably on affected board revisions.

## 2.1.37

### R413E16 reliable all-channel control

- Changed R413E16 All ON/OFF to send the proven per-channel FC06 commands (`0x0100` / `0x0200`) to channels 1–16. This avoids the optional register-0 broadcast command, which is not implemented consistently by every board revision.
- Fixed R413E16 live state recognition: both a read-back value of `1` and `256` now report a channel as ON, matching the board’s two observed status representations.

## 2.1.36

### R413E16 state synchronization

- Fixed Combined Switches: after sending confirmed `0x0100` ON or `0x0200` OFF commands to every selected channel, the integration immediately reads the individual channel states.
- Fixed R413E16 All ON/OFF, toggle, and interlock actions so CH-01 through CH-16 update from their state registers immediately after the command.

## 2.1.35

### R413E16 documentation

- Added a visible Documentation button to every configured R413E16 device.
- Added an in-panel reference covering confirmed wiring defaults, channel commands, live state reads, baud/slave settings, factory-reset safety, and links to the manufacturer’s documentation.

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
