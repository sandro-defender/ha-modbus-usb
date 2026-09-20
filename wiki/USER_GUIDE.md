# Modbus USB Controller — User Guide

Step-by-step instructions for installing, configuring, and operating the
Modbus USB Controller integration. For the short overview, see the
[README](../README.md).

**Contents**

1. [Getting started](#1-getting-started)
2. [Panel tour](#2-panel-tour)
3. [Adding devices](#3-adding-devices)
4. [Managing entities](#4-managing-entities)
5. [Templates](#5-templates)
6. [Live Dashboard](#6-live-dashboard)
7. [Diagnostics & Debug](#7-diagnostics--debug)
8. [Board tools](#8-board-tools)
9. [Automations & services](#9-automations--services)
10. [Multiple devices & hubs](#10-multiple-devices--hubs)
11. [Maintenance](#11-maintenance)
12. [Troubleshooting & FAQ](#12-troubleshooting--faq)

---

## 1. Getting started

### What you need

- Home Assistant 2024.1 or newer.
- A USB-to-RS-485 adapter connected to the machine running Home Assistant.
- A Modbus RTU device, powered and wired (A→A, B→B).
- Every device on one RS-485 bus must use a **unique slave ID** (1–247).

### Install the integration

**Via HACS (recommended):**

1. Open **HACS → Integrations**.
2. Open the menu (⋮) → **Custom repositories**.
3. Add `https://github.com/sandro-defender/ha-modbus-usb`, category **Integration**.
4. Click **Download** on **Modbus USB Controller**.
5. Restart Home Assistant.

**Manual install:** copy `custom_components/modbus_usb` into
`<config>/custom_components/`, then restart Home Assistant.

### Add your first hub

A hub is one USB adapter: its serial port, communication settings, default
slave ID, and poll interval.

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **Modbus USB Controller**.
3. Fill in the form:
   - **Integration name** — e.g. `Workshop RS-485`.
   - **Serial port** — e.g. `/dev/ttyUSB0` (Linux/HA OS) or `COM3` (Windows).
     Prefer a `/dev/serial/by-id/...` path: it survives reboots and re-plugged
     adapters. Use the panel's **Hub & Serial** tab later to find candidates.
   - **Baud rate / Data bits / Parity / Stop bits** — from your device manual.
     Most boards use `9600 / 8 / N / 1`.
   - **Slave / Unit ID** — your device's Modbus address (usually `1`).
     If you don't know it, leave `1` for now and scan later (chapter 7).
   - **Poll interval** — how often registers are read (default 10 s).
4. Save. A **Modbus USB** entry appears in the sidebar — that panel is where
   everything else happens.

> You need a second hub only if you have a **second USB adapter**. All boards
> on one RS-485 bus share a single hub as separate *devices*.

---

## 2. Panel tour

Open **Modbus USB** in the sidebar. The header shows connection status, the
hub selector (when you have several hubs), and the **Refresh**, **Check
update**, and **Add device** actions.

| Tab | Purpose |
|---|---|
| **Devices** | Your configured boards: expand a card to test, edit, enable/disable, or delete it. |
| **Device Templates** | Browse bundled templates by manufacturer; preview registers; **Use Template** or **Save to HA**. |
| **Live Dashboard** | Live readings, relay toggles, and setpoints for every device, with filters. |
| **All Entities** | Flat list of every Home Assistant entity with quick edit/delete. |
| **Hub & Serial** | View and edit the hub's serial settings and poll interval; scan USB ports. |
| **Diagnostics & Debug** | Health, scanner, direct tools, verification, board tools, discovery, activity log. |
| **Traffic Inspector** | Live-streaming RS-485 traffic: decoded RTU request **and real captured response** frames (with CRC16 pass/fail) plus per-transaction latency waterfalls; pausable live stream. |
| **Template Designer** | Draft a custom YAML template, live-test every register and fingerprint probe against the board, then save **and apply to a device in one step**. |

Device cards and diagnostics cards start **collapsed** — click a card header
to expand it. The panel remembers what you expanded, and switches update
live without waiting for the next poll.

> Changing configuration or writing to hardware from the panel requires a
> Home Assistant **administrator** account. Viewing and normal switch control
> work for all users.

---

## 3. Adding devices

There are three ways to add a board. In all cases the result is the same: a
device card plus real Home Assistant entities — no YAML, no restart.

### Way 1 — From a template (fastest, recommended)

1. Open **Device Templates**, expand your board's manufacturer section.
2. Review the entity list, then click **🚀 Use Template**.
3. Enter a **device name** (e.g. `Garage relays`) and the board's **slave ID**.
4. If the template asks about the **M0 jumper** (R413E16/R4D6F20), match the
   physical jumper on your board — this selects the correct register map.
5. Save, then open **Live Dashboard** to verify readings or toggle relays.

### Way 2 — Blank device + manual entities

1. Click **Add device** in the header (or on the Devices tab).
2. Enter a name and slave ID, save.
3. Expand the new card → **Add entity**, and create each register mapping by
   hand (see chapter 4).

### Way 3 — Scan first, then add

Use this when the slave ID or baud rate is unknown:

1. Open **Diagnostics & Debug → 🔎 Find RS-485 Devices**.
2. Pick the baud rate(s) from the manual (or try `9600` first) and a small
   slave-ID range such as 1–20.
3. Run the scan. Each answering board is listed with its slave ID, baud
   rate, parity, and template suggestions.
4. Click **Use this target** to hand the result to Board Tools, or just note
   the slave ID and add the device via Way 1 or 2.

---

## 4. Managing entities

Entities are the Home Assistant objects created from Modbus registers. Add
them from a device card (**Add entity**) or from **All Entities**; edit them
from either place. Changes apply without restarting.

### Which entity type do I need?

| I want to… | Entity type | Register types |
|---|---|---|
| Read a measurement (V, A, W, °C, …) | `sensor` | holding, input |
| Switch a relay/output on and off | `switch` | coil, holding |
| Enter a setpoint or threshold | `number` | holding |
| Monitor a digital input or alarm | `binary_sensor` | coil, discrete, holding |

### Field reference

- **Name** — shown in Home Assistant (e.g. `Voltage`, `CH-01`).
- **Register type** — `holding` (16-bit read/write), `input` (16-bit
  read-only), `coil` (1-bit read/write), `discrete` (1-bit read-only).
- **Address** — the Modbus address (0–65535). Manuals often print `40001`-style
  numbers; those usually mean address `0`, so subtract 1 if every value looks
  shifted by one register.
- **Data type** (sensors/numbers) — `uint16`, `int16`, `uint32`, `int32`,
  `float32`. 32-bit types occupy two consecutive registers.
- **Scale** — display value = raw × scale. Use `0.1` when the device stores
  `235` for 23.5 °C.
- **Unit / Device class / State class** — e.g. `V` + `voltage` +
  `measurement`; use `total_increasing` for lifetime energy counters so the
  Energy dashboard works.
- **ON / OFF values** (holding-register switches) — the exact words your
  board expects, from its manual (e.g. `256` / `512` on eletechsup boards).
  Coil switches ignore these.
- **Min / Max / Step / Mode** (numbers) — slider or input-box limits.

### Group (Combined) switches

To switch several channels together (e.g. CH-01 + CH-05 + CH-06), create one
`switch` with an **addresses** list instead of a single address. On R413E16
boards the panel offers a visual channel picker and a one-click
**Combined Switch** creator. A group is ON only when every selected channel
reports ON.

### Enable / disable

Each device card has **Enable device / Disable device**. Disabled devices
are skipped during polling and their entities show as unavailable — useful
when a board is temporarily disconnected. No reload or port reopening happens.

---

## 5. Templates

Templates are YAML files describing a board's registers, names, units, and
board-tool commands. They only *create Home Assistant entities* — applying
one never writes configuration to the physical board.

### Bundled vs. saved templates

- **Bundled** templates ship with the integration and are read-only.
- **⇩ Save to HA** copies one to `<config>/modbus_usb_templates/` where you
  can edit it. Saved copies take priority and show a **Saved in HA** badge.
  Only save when you intend to customize.

You can also edit, duplicate, and delete saved templates from the
**Device Templates** tab (pencil/duplicate/trash icons) without a file editor.

### Writing your own template

Create `<config>/modbus_usb_templates/<id>.yaml`. Minimal example:

```yaml
id: my-board            # must match the filename stem
name: My Board Meter
manufacturer: MyVendor
model: MB-100
default_slave_id: 1
description: Single-phase Modbus RTU meter.
info_url: "https://vendor.example/mb-100-manual"

entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
    unit_of_measurement: "V"
    device_class: voltage
    state_class: measurement

  - name: Relay 1
    entity_type: switch
    register_type: coil
    address: 0
```

Validation rules (enforced by `pytest tests/test_templates.py` and CI):

- `id` equals the filename stem and is unique.
- Non-empty `entities` list; each entity has `name`, a valid `entity_type`,
  an allowed `register_type`, and `address` (0–65535) or a non-empty
  `addresses` list.
- `data_type` on sensors/numbers must be a known type.
- `default_slave_id` within 1–247; `status`, if present, is one of
  `tested` / `testing` / `untested`.

### Interactive Template Designer (live validation)

Instead of hand-testing with Developer Tools, use the panel's **Template
Designer** tab (since v2.5.0):

1. Paste (or type) your draft YAML — **📄 Load sample** provides a starter.
2. Optionally override the **test slave ID** if the board on your bench uses
   a different address than the template's `default_slave_id`.
3. Click **▶ Validate & test reads**. The integration:
   - structurally validates the YAML (same rules as saving),
   - reads **every** entity's registers from the live bus,
   - decodes each response as *all* compatible data types, so you can spot a
     wrong `data_type` (e.g. a float32 field declared as uint16) at a glance,
   - **probes every declared `fingerprint` entry** (since v2.6.0) and reports
     *match / no match / error* per probe with the value it read, so you can
     certify that the board really is the device your template claims —
     the same checks the RS-485 scanner uses for suggestions.
4. Fix any ❌ rows (bad address, wrong register type, unreachable slave),
   re-run, and once everything shows **pass**, either:
   - enter a filename and **💾 Save template** (file only), or
   - use **🚀 Save & apply to device** (since v2.6.0): pick an existing
     device in **Apply to** — or keep *➕ Create new device…* and optionally
     type a device name — and the template is saved **and** applied in one
     step, creating the entities immediately. A blank filename is derived
     from the template name automatically.
   Saving warns you if the draft hasn't passed live validation.

> Test reads and fingerprint probes are real bus traffic. Leave **Live test
> reads** enabled for the certification workflow; disable it only for an
> offline structure check (fingerprint probes are skipped too).

### Fingerprints (scan suggestions)

Add read-only checks so **Find RS-485 Devices** can suggest your template for
a matching board:

```yaml
fingerprint:
  - register_type: input
    address: 0
    data_type: float32
    min_value: 80
    max_value: 300
```

Pick registers with stable, distinctive values (e.g. mains voltage range).
See `sdm120.yaml` and `xy_md02.yaml` for working examples. Since v2.6.0 the
**Template Designer** test-runs every fingerprint entry live and reports
match/no-match per probe before you save a template.

### M0 jumper templates (R413E16 / R4D6F20)

These templates declare an `m0` block under `device_controls`, which makes
the panel show an M0 checkbox when adding the device:

- **R413E16:** M0 selects TTL output polarity only (open = low-level,
  connected = high-level). It does not change the Modbus protocol.
- **R4D6F20:** M0 selects the register map — open installs the Command 1
  holding-register profile, shorted installs the Command 2 coil +
  discrete-input profile. Always match the physical jumper.

### Requesting or contributing a template

- **Request:** open an [issue](https://github.com/sandro-defender/ha-modbus-usb/issues)
  with the model name, a manual link, and the register map.
- **Contribute:** add the YAML under
  `custom_components/modbus_usb/templates/` (plus a photo under
  `www/images/` if you have one), run the template tests, and open a PR.
  New maps ship marked **untested** until confirmed on hardware — see
  [Contributing](../README.md#contributing).

---

## 6. Live Dashboard

The **Live Dashboard** tab is the daily driver: every device's current values
and controls on one page.

- Sensors show live values and update each poll interval.
- Switches toggle immediately and reflect verified board feedback.
- Numbers open an editor for setpoints.
- Use the filter buttons (e.g. all/switches/sensors) to narrow long lists.

If a value looks stale, check the device card on the **Devices** tab or the
**📡 RS-485 Connection Health** card — a silent board usually means a wiring
or addressing problem, not a dashboard problem.

---

## 7. Diagnostics & Debug

This tab is the first stop whenever something misbehaves. Cards start
collapsed with a one-line live summary; expand any card for details.

| Card | What it does |
|---|---|
| **📡 RS-485 Connection Health** | Totals, failures, last success/error, and the most recent operation across polling *and* writes. |
| **🔌 Serial port profile** | Configured port settings, HA ownership, lock activity, detected USB-adapter details. Non-invasive — it never opens the port itself. |
| **🔎 Find RS-485 Devices** | Scans slave IDs × baud/parity under the serial lock, then restores your client. **Use this target** prefills Board Tools. |
| **🔬 Live Modbus Read / Write** | One-shot reads/writes for testing, without creating entities. |
| **✅ Template read verification** | Read-only check of every non-switch entity (outputs are never toggled). |
| **📜 RS-485 Activity & Error Log** | History of requests, replies, timing, and errors, with one-click JSON/CSV/Text export, sensitive data redaction, and multi-field filtering (slave ID, function code, errors). |
| **🛠 Board tools** | Launcher for template-aware hardware actions (chapter 8). |
| **Automatic safe discovery** | Checks the four standard read functions at address 0, then expands only functions that answer. Read-only, with a Stop button. |
| **Safe board discovery** | Read-only unknown-board probe across functions/addresses, with plain-language interpretation of each reply. |
| **Watch inputs and sensors** | Repeatedly reads a function/address and highlights values that change — flip a physical input to find its register. |
| **Dangerous manual write** | Guarded hex-frame writer for experts (admin only, double confirmation, CRC-checked, FC05/FC06/FC0F/FC10 only). |

### Traffic Inspector (frame analyzer)

For wire-level analysis, open the **Traffic Inspector** tab (since v2.5.0).
It lists the hub's recent RS-485 transactions — **streamed live** over a
WebSocket subscription while the tab is open (since v2.6.0) — and clicking
one decodes its RTU frames byte by byte:

- **Paired request/response frames**: the **Request frame (TX)** analyzer
  plus, when pymodbus transaction tracing is available, a **Response frame
  (RX)** analyzer built from the *real bytes received from the board* —
  byte-count read responses, write echoes, and exception responses. Rows
  carry an `RX` / `RX EXC` / `no RX` badge, and the stats bar counts
  captured responses. Response bytes are captured from the wire, never
  invented; without a tracing hook the inspector says so and shows only the
  request side.
- **Slave ID** and **Function Code** with the plain-language name
  (FC01–FC06, FC0F, FC10, and exception responses).
- **Address**, **Count**, **Byte Count**, and the **Data payload** in hex.
- **CRC16 low/high bytes** with a computed checksum and **PASS/FAIL** badge —
  a failing CRC usually means wiring noise, a baud mismatch, or a bus
  collision.
- A **latency waterfall** splitting each transaction into bus lock wait, port
  connect, inter-frame delay, and the serial request itself, with
  avg/p95/max duration indicators per hub and per slave. Slow `request`
  stages point at the board; slow `lock wait` stages point at very aggressive
  polling or long batch scans.
- **⏸ Pause stream / ▶ Resume**: freeze the list to study a frame while the
  bus keeps talking; the button counts transactions buffered while paused,
  and resuming re-syncs the full view so nothing is lost. **↻ Reload
  traffic** forces a full refresh at any time.

### Recommended debugging workflow

1. Check **Connection Health** — are requests failing or just slow?
2. Open the **Activity Log** — the exact failing operation, slave, and error.
3. For timing or CRC questions, open the **Traffic Inspector** and inspect
   the failing transaction's request/response pair, checksums, and latency
   waterfall — an exception RX frame tells you *why* the board refused.
4. Run **Find RS-485 Devices** on a narrow range to confirm ID/baud/parity.
5. Use **Live Read** with the detected settings to prove the register map.
6. For unknown boards, use **Safe discovery** + **Watch inputs** — never guess
   writes; use the guarded writer only with a documented frame.

---

## 8. Board tools

**🛠 Board tools** (inside Diagnostics) exposes hardware actions:

- **Boards with template controls** (R413E16, R4D6F20) get a dialog with the
  documented operations: all-relay on/off, per-channel actions, baud/parity
  setup, slave-ID setup (where the vendor documents it), board-ID readback,
  and factory reset. Destructive actions ask for confirmation.
- **Other boards** get a documented custom-command form instead.
- A discovered scan target can be loaded with **Use this target** — nothing
  is sent until you explicitly run a command.
- Devices created before template commands existed offer
  **Restore standard template controls** to add the buttons without
  touching working entities.

Notable board specifics:

- **R413E16** — per-channel ON/OFF, toggle, interlock, 1-second pulse, and
  timed actions; **Read channel states** for on-demand FC03 feedback; baud
  rates limited to the documented safe range (1200–19200); factory reset
  restores 9600 baud / slave ID 1 (power-cycle afterwards).
- **R4D6F20** — all-relay controls, per-channel actions, board-ID readback,
  baud/parity setup, factory reset. The available controls follow the active
  Command 1 / Command 2 profile.

---

## 9. Automations & services

Five services cover one-shot access from automations, scripts, and Developer
Tools. All need the hub's `entry_id` (find it under
**Settings → Devices & services → Modbus USB Controller → ⋮ → Device info**,
or pick it in the service UI) and accept an optional `slave_id` override.

| Service | Purpose |
|---|---|
| `modbus_usb.read_register` | One-shot read; the result arrives as a `modbus_usb_register_read` event. |
| `modbus_usb.write_register` | Write a coil (`0`/`1`) or holding register (integer). Failures raise, so automations can catch them. |
| `modbus_usb.batch_write` | Write several coils/holding registers while holding the bus lock for the whole batch — no polling interleaves between writes. Aborts on the first failed write. |
| `modbus_usb.boost_polling` | Temporarily shorten the poll interval (`scan_interval`, default 1 s) for `duration` seconds (5–3600). The original interval restores itself; stacking boosts extends the window. |
| `modbus_usb.reset_circuit_breaker` | Manually restore a degraded/offline slave to `healthy` so it is polled immediately again. Omit `slave_id` to reset every tracked slave. |

**Poll a register every minute and alert on its value:**

```yaml
automation:
  - alias: "Check inverter temperature"
    trigger:
      - platform: time_pattern
        minutes: "/1"
    action:
      - service: modbus_usb.read_register
        data:
          entry_id: "YOUR_ENTRY_ID"
          address: 1
          register_type: input
          data_type: int16

  - alias: "Alert on high temperature"
    trigger:
      - platform: event
        event_type: modbus_usb_register_read
        event_data:
          address: 1
    condition:
      - condition: template
        value_template: "{{ (trigger.event.data.value | float(0)) * 0.1 > 60 }}"
    action:
      - service: notify.persistent_notification
        data:
          title: "Modbus alert"
          message: "Inverter over 60 °C!"
```

**Pulse a relay from a script:**

```yaml
script:
  pulse_relay_1:
    sequence:
      - service: modbus_usb.write_register
        data:
          entry_id: "YOUR_ENTRY_ID"
          address: 0
          register_type: coil
          value: 1
      - delay: "00:00:02"
      - service: modbus_usb.write_register
        data:
          entry_id: "YOUR_ENTRY_ID"
          address: 0
          register_type: coil
          value: 0
```

> Prefer entities over services for anything polled regularly — entities get
> polling, retries, state history, and Energy-dashboard support for free.

**Set several registers atomically (single bus lock):**

```yaml
script:
  apply_setpoints:
    sequence:
      - service: modbus_usb.batch_write
        data:
          entry_id: "YOUR_ENTRY_ID"
          slave_id: 2
          writes:
            - address: 10
              value: 65            # uint16 holding register
            - address: 12
              value: 21.5
              data_type: float32   # written as two registers
            - address: 0
              register_type: coil
              value: 1
```

**High-frequency monitoring for two minutes (e.g. while commissioning):**

```yaml
script:
  commissioning_boost:
    sequence:
      - service: modbus_usb.boost_polling
        data:
          entry_id: "YOUR_ENTRY_ID"
          scan_interval: 1    # poll every second…
          duration: 120       # …for two minutes, then restore automatically
```

**Recover an unresponsive slave without restarting HA:**

```yaml
automation:
  - alias: "Reset Modbus slave 3 when the user asks"
    trigger:
      - platform: state
        entity_id: input_button.reset_modbus
    action:
      - service: modbus_usb.reset_circuit_breaker
        data:
          entry_id: "YOUR_ENTRY_ID"
          slave_id: 3
```

---

## 10. Multiple devices & hubs

- **One adapter → one hub.** All boards on that RS-485 bus become separate
  devices on the hub, each with its own slave ID. Add them one by one from
  templates; each gets its own card and entities.
- **Second adapter → second hub.** Add the integration again with the second
  port. Switch hubs from the panel header selector.
- Keep slave IDs unique **per bus** (two hubs may each have a slave ID 1 —
  they are separate wires).
- The poll interval is per hub; heavy buses with many devices may need a
  longer interval to keep every read inside one cycle.

---

## 11. Maintenance

### Updating

1. Click **Check update** in the panel header. If a release exists, the
   button becomes **Update \<version\>**.
2. Click it (admin only). On HACS installs it runs the HACS update;
   otherwise it opens the verified GitHub release page.
3. After a successful in-panel update the button becomes
   **Restart Home Assistant** — click it after the confirmation.

### Backup

Your configuration lives in Home Assistant's config entry storage plus
`<config>/modbus_usb_templates/` (your saved templates). Include both in
your regular HA backup — no other files carry your setup.

### Renaming / moving hardware

- Rename devices/entities from their edit dialogs; Home Assistant keeps
  history by unique ID.
- Moving an adapter to another USB port? Switch the hub to its
  `/dev/serial/by-id/...` path first (Hub & Serial tab), and the move
  becomes a non-event.

---

## 12. Troubleshooting & FAQ

### Quick symptom table

| Symptom | Most likely cause | Fix |
|---|---|---|
| Timeouts / no response | Wrong slave ID or baud rate | Scan IDs 1–20 at each documented baud; confirm parity/stop bits |
| Nothing answers at all | A/B swapped, no power, wrong port | Swap A/B **once**, check board power LEDs, verify the port path |
| `Permission denied` / port busy | OS/container can't open the adapter | Use `/dev/serial/by-id/...`; on Docker pass `--device /dev/ttyUSB0`; ensure nothing else owns the port |
| Values shifted by one register | 1-based manual addresses | Subtract 1 from the manual address |
| Values 10×/100× off | Missing scale | Set `scale: 0.1` / `0.01` on the entity |
| Gibberish floats | Wrong data type | Compare `uint16` vs `float32` against the manual; verify with Live Read |
| Port changes after reboot | `ttyUSB0` vs `ttyUSB1` enumeration | Switch to the `/dev/serial/by-id/...` path |
| TX LED blinks, no reply | Request sent, device not answering | TX only proves HA transmitted — check ID/baud/wiring/power and the activity log |
| Panel button does nothing / "unauthorized" | Not an admin session | Sign in as an administrator for configuration and hardware writes |

### FAQ

**Do I need to restart after adding devices or entities?**
No. Only integration install/update requires a restart. Device and entity
changes apply immediately.

**Can two programs use the same USB adapter?**
No. Home Assistant owns a configured adapter exclusively — a second program
opening the same port interleaves RTU frames and causes timeouts or wrong
writes. Use a separate adapter + board for bench testing.

**Why does my relay board need ON=256 / OFF=512?**
Many holding-register relay boards (all eletechsup models here) use vendor
command words instead of plain 0/1: `0x0100` (256) = ON, `0x0200` (512) =
OFF. The templates set these automatically; for manual switches, copy them
from the board manual.

**How do I capture logs for a bug report?**
Temporarily add frame-level logging, reproduce the issue, then remove it:

```yaml
logger:
  default: warning
  logs:
    custom_components.modbus_usb: debug
    pymodbus: debug
```

Include the Diagnostics activity-log copy plus your device model, serial
settings, slave ID, and template in the
[issue](https://github.com/sandro-defender/ha-modbus-usb/issues).

**Where is my data stored?**
Devices/entities live in the config entry (`.storage/`), saved templates in
`<config>/modbus_usb_templates/`. Uninstalling the integration removes the
 former; back up the latter yourself if you customized templates.
