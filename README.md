# Modbus USB Controller

<p align="center">
  <img src="custom_components/modbus_usb/brand/logo.png" width="160" alt="Modbus USB Controller logo">
</p>

<p align="center">
  <strong>Connect RS-485 Modbus devices to Home Assistant over a USB adapter — no entity YAML required.</strong><br>
  Energy meters, relay boards, temperature sensors, and remote I/O, managed from a visual sidebar panel.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom"></a>
  <a href="https://github.com/sandro-defender/ha-modbus-usb/releases"><img src="https://img.shields.io/github/v/release/sandro-defender/ha-modbus-usb" alt="Latest release"></a>
  <a href="https://github.com/sandro-defender/ha-modbus-usb/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT license"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-2024.1%2B-41BDF5.svg" alt="Home Assistant 2024.1+"></a>
</p>

---

## Why this integration?

Home Assistant's built-in Modbus integration is powerful but YAML-heavy: every register, scale factor, and slave ID is hand-written configuration. **Modbus USB Controller** takes a different approach:

- 🧭 **Guided setup** — pick your USB port and serial settings once, then manage everything visually.
- 🧩 **Board templates** — one click adds all channels of a known relay board or meter with sensible names.
- 🖥️ **Sidebar control panel** — dashboard, devices, templates, board tools, and diagnostics in one place.
- 🔍 **RS-485 scanner** — find an unknown slave ID or baud rate instead of guessing.
- 🩺 **Real diagnostics** — connection health, recent reads/writes, errors, and a live activity log.
- 🤖 **Automation-ready** — services for one-shot register reads/writes plus instant entity updates.
- 🔌 **Multi-device buses** — one USB adapter serves every device on its RS-485 network.

If you can wire A/B and know (or scan for) a slave ID, you can be reading live values in about five minutes.

## How it works

```text
USB-to-RS-485 adapter ── RS-485 bus (A/B) ──┬── Device 1 (slave ID 1, e.g. relay board)
                                            ├── Device 2 (slave ID 2, e.g. energy meter)
                                            └── Device 3 (slave ID 3, e.g. temp sensor)
        ▲
   One Hub in Home Assistant
   (serial port + defaults + poll interval)
```

- A **hub** is one config entry: a serial port, baud rate, data/parity/stop bits, a default slave ID, and a poll interval. Add a second hub only if you have a **second USB adapter**.
- **Devices** are logical boards on that hub's RS-485 bus. Each device has its own name, slave ID, and template.
- **Entities** (sensors, switches, numbers, binary sensors) belong to a device and map to Modbus registers.

> Most installations need exactly **one hub** and **one device per physical board** on the bus.

## Supported devices

13 starter templates ship with the integration. Only the R413E16 is confirmed against real hardware here; everything else is a starting point from vendor manuals or community maps — verify on your own device before relying on it in automations.

| Manufacturer | Model | What it is | Status |
|---|---|---|---|
| eletechsup | **R413E16** | 16-ch RS-485 I/O core (TTL outputs, needs relay stage) | ✅ **Tested** |
| eletechsup | R4D6F20 | 20-relay multifunction board + 2× DI, 4–20 mA, 0–10 V | 🧪 In testing (Command 1 + 2) |
| eletechsup | 23IOC24_NPN | 24DI / 24DO NPN remote I/O module | ⚪️ Untested starter |
| eletechsup | N4ROD08 | 8-ch RS-485 relay board (community map) | ⚪️ Untested starter |
| eletechsup | NT18B07 | 7-ch NTC temperature board (community map) | ⚪️ Untested starter |
| Eastron | SDM120 | Single-phase DIN-rail meter | ⚪️ Untested starter |
| Eastron | SDM230 | Single-phase meter (community map) | ⚪️ Untested starter |
| Eastron | SDM630 | Three-phase meter (community map) | ⚪️ Untested starter |
| Hiking | DDS238 | Single-phase DIN-rail meter | ⚪️ Untested starter |
| Waveshare | Modbus RTU Relay (D) | 8-ch relay board (community map) | ⚪️ Untested starter |
| Waveshare | Relay 8-CH | Generic 8-ch coil relay map | ⚪️ Untested starter |
| SHT20 | XY-MD02 | Temp + humidity RS-485 sensor | ⚪️ Untested starter |
| Generic | Modbus RTU Device | Blank sensor + setpoint + switch + alarm skeleton | ⚪️ Starting point |

Don't see your board? Open an [issue](https://github.com/sandro-defender/ha-modbus-usb/issues) with the model name, a manual link, and the register map — or build it by hand in minutes (see [Templates](#templates)).

## Requirements

- **Home Assistant 2024.1 or newer** (OS, Supervised, Container, or Core).
- A **USB-to-RS-485 adapter** visible to Home Assistant (typically `/dev/ttyUSB0` on HA OS/Linux, `COM3` on Windows).
- A **Modbus RTU device** with power and RS-485 wiring ready.
- Every device on the same bus must use a **unique slave ID** (1–247).

Wiring tips: connect A→A and B→B, add the 120 Ω termination resistor on long runs, and keep the USB adapter on a stable port path (see [Troubleshooting](#troubleshooting)).

## Installation

### Option A — HACS (recommended)

1. Open **HACS → Integrations**.
2. Open the menu (⋮) → **Custom repositories**.
3. Add `https://github.com/sandro-defender/ha-modbus-usb`, category **Integration**.
4. Click **Download** on **Modbus USB Controller**.
5. **Restart Home Assistant.**

### Option B — Manual

1. Copy `custom_components/modbus_usb` into `<config>/custom_components/`.
2. Restart Home Assistant.

Then continue with [Quick start](#quick-start).

## Quick start

**1. Add the hub.**
Settings → Devices & services → Add integration → **Modbus USB Controller**. Enter the serial port and settings from your device manual. Most boards use `9600 / 8 / N / 1`.

**2. Open the panel.**
Click **Modbus USB** in the sidebar.

**3. Add your first device from a template.**
Templates → pick your board → **Use Template** → set the device name and slave ID → save. All channels appear as Home Assistant entities automatically.

**4. Check the dashboard.**
Open **Dashboard** to see readings or toggle relays. Switches update immediately; sensors refresh on your poll interval (default 10 s).

**5. Don't know the slave ID or baud rate?**
Open **Diagnostics → Find RS-485 Devices** first and scan a small range (e.g. IDs 1–20 at 9600 baud). Then use the detected ID when adding the device.

That's it — no YAML, no restart needed after adding devices.

## The sidebar panel

The **Modbus USB** panel is the day-to-day home for this integration. It is phone-friendly and updates live.

| Tab | What you do there |
|---|---|
| **Dashboard** | Live readings, relay toggles, and setpoints for every device. |
| **Devices** | Add, edit, enable/disable, or remove devices and their entities. Disabled devices pause polling and show entities as unavailable. |
| **Templates** | Browse bundled templates by manufacturer, preview registers, **Use Template**, or **Save to HA** for an editable copy. |
| **Diagnostics & Debug** | Connection health, serial-port profile, RS-485 scanner, direct read/write tools, configured-device verification, safe unknown-board discovery, and the live activity log. |
| **Board Tools** | Template-aware hardware actions: R413E16 channel actions/baud/slave setup, R4D6F20 relay controls and serial setup, plus a documented custom-command form for other boards. |

The header also offers **Refresh**, hub selection (when you have more than one), **Add device**, and **Check update** (which becomes **Update → Restart** when a release is available via HACS).

## Templates

Templates are YAML files describing a board's registers, channels, names, units, and board-tool commands.

- **Bundled templates** work immediately and stay read-only inside the integration.
- **Saved in HA** templates are your editable copies in `<config>/modbus_usb_templates/`. Use **Save to HA** only when you want to customize a board — your copy then takes priority and shows a badge.
- Template application is **non-destructive to hardware**: it only creates Home Assistant entities (and, where documented, board-tool buttons). It never auto-writes configuration to the board.

### Special board behaviors

- **eletechsup R413E16 (tested).** Channels CH-01–CH-16 use verified FC06 holding-register commands (`0x0100` ON / `0x0200` OFF, registers 1–16) with live FC03 state feedback. The template includes a **Combined Switch** (all 16 channels) you can copy for any group, plus per-channel toggle, interlock, momentary pulse, and timed actions. The M0 jumper selects TTL polarity only (open = low-level, connected = high-level) — it does not change the Modbus protocol.
- **eletechsup R4D6F20 (in testing).** One template covers both documented modes: M0 open installs the Command 1 holding-register profile; M0 shorted installs the Command 2 coil + discrete-input profile. Match the physical jumper when applying the template.
- **Combined Switches.** Group any channels (e.g. CH-01 + CH-05 + CH-06) into one switch that sends the confirmed per-channel commands and mirrors live feedback.

### Customizing or requesting a template

1. **Save to HA**, edit the YAML (names, addresses, scales, units), and re-apply it to a device.
2. Keep `id`, `entity_type`, `register_type`, and `address` valid; the panel validates saves.
3. To request a bundled template, open an issue with the vendor manual and register map — photos of the board label help.

## Entities you can create

Add entities from the panel (**Devices → device → Add entity**) or from the integration's **Configure** options. No restart is required.

| Entity | Reads | Writes | Typical use |
|---|---|---|---|
| `sensor` | holding, input | — | Voltage, current, power, temperature, humidity |
| `switch` | coil, holding | coil, holding | Relays, outputs, combined groups |
| `number` | holding | holding | Setpoints, thresholds, dimmer levels |
| `binary_sensor` | coil, discrete | — | Digital inputs, alarms, door contacts |

- **Data types** (sensors/numbers): `uint16`, `int16`, `uint32`, `int32`, `float32`. 32-bit values span two registers.
- **Scaling:** `display value = raw × scale`. Use `0.1` for devices storing 23.5 °C as `235`.
- **Holding-register switches** need board-specific `on_value` / `off_value` (e.g. `256` / `512` on eletechsup boards). Coil switches use plain ON/OFF.
- **Device/state classes:** pick `power`, `energy`, `voltage`, `temperature`, etc. so Home Assistant graphs and the Energy dashboard work. `total_increasing` suits lifetime energy counters.

> **Off-by-one?** Manuals often print `40001`-style addresses; Modbus itself uses `0`-based addresses, so `40001` usually means address `0`. If every value looks shifted by one register, subtract 1.

## Diagnostics & troubleshooting

**Diagnostics & Debug** is the first place to look when something misbehaves:

- **Connection Health** — totals, failures, last success/error, and most recent operation across polling *and* switch writes.
- **Serial profile** — configured port settings, HA ownership, lock activity, and detected USB-adapter details.
- **Find RS-485 Devices** — scan slave IDs × baud/parity profiles under the integration's serial lock, then restore your configured client. **Use this target** hands a result to Board Tools.
- **Direct Modbus tools** — one-shot reads/writes for testing without creating entities.
- **Configured-device verification** — read-only check of every non-switch entity (outputs are never toggled).
- **Safe discovery** — read-only probes of the four standard functions plus an input watcher that highlights addresses that change when you flip a physical input.
- **Activity log** — the single history of requests, replies, timing, and errors, with one-click copy.

Writes that change hardware (board tools, bus scans, hex writes, relay tests) require an **administrator** session. Ordinary dashboard switch control and all read-only views remain available to normal users.

### Troubleshooting

| Symptom | Most likely cause | Fix |
|---|---|---|
| No response / timeouts | Wrong slave ID or baud rate | Scan IDs 1–20 at each documented baud; confirm parity/stop bits |
| No response at all | A/B swapped, no power, wrong port | Swap A/B **once**, check board power LEDs, verify the port path |
| `Permission denied` / port busy | OS or container can't open the adapter | Use `/dev/serial/by-id/...` (stable across reboots); on Docker, pass `--device /dev/ttyUSB0`; ensure no other program owns the port |
| Values shifted by one register | 1-based manual addresses | Subtract 1 from the manual address |
| Values 10×/100× off | Missing scale | Set `scale: 0.1` / `0.01` on the entity |
| Gibberish floats | Wrong data type or byte order | Compare `uint16` vs `float32` against the manual; verify with a direct read |
| Port changes after reboot | `ttyUSB0` vs `ttyUSB1` enumeration | Switch to the `/dev/serial/by-id/...` path in the hub settings |
| TX LED blinks, no reply | Request sent, device not answering | TX only proves HA transmitted — check ID/baud/wiring/power; watch the activity log |

A `/dev/serial/by-id/...` path is strongly preferred over `/dev/ttyUSB0` because it survives reboots and re-plugged adapters. For Docker:

```yaml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

<details>
<summary><strong>Register types, frame debugging, and serial reference</strong></summary>

- **Holding register** — 16-bit read/write value (FC03/FC06/FC16).
- **Input register** — 16-bit read-only value (FC04).
- **Coil** — 1-bit read/write value (FC01/FC05/FC15).
- **Discrete input** — 1-bit read-only value (FC02).

The panel's activity log shows decoded requests and results. For temporary raw frame logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.modbus_usb: debug
    pymodbus: debug
```

Remove or lower these levels after debugging — frame logs are verbose.

</details>

## Automations & services

The integration exposes two services. Both accept the hub's `entry_id` (find it in **Settings → Devices & services → Modbus USB Controller → ⋮ → Device info**, or pick it from a service-call target) and an optional `slave_id` that overrides the hub default.

| Service | Purpose |
|---|---|
| `modbus_usb.read_register` | One-shot read; result arrives as a `modbus_usb_register_read` event |
| `modbus_usb.write_register` | Write a coil (`0`/`1`) or holding register (integer); failures raise so automations can catch them |

**Poll a register every minute and react to the value:**

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

Prefer entities over services for anything polled regularly — entities get polling, retries, state history, and Energy-dashboard support for free.

## Multiple hubs & advanced notes

- **One adapter → one hub.** Devices on the same bus share the serial port; each device just uses a different slave ID.
- **Two adapters → two hubs.** Add the integration a second time with the second port.
- The integration holds a **single-owner serial lock** per hub: polling, board tools, scans, and services queue through it so frames never interleave. External tools must never open the same port while HA is connected.
- Polling is grouped where the template allows it (e.g. contiguous R4D6F20 blocks read in 3 requests instead of 24).
- Reloads are surgical: adding/removing devices rebuilds entities **without** closing the shared serial port, and an offline board can't block HA startup.

## Security

- Configuration changes and hardware writes from the panel require an **administrator** account.
- Entity saves are validated (entity type, address range, register types per entity type).
- The guarded **manual hex-write** lab accepts only complete RTU frames for FC05/FC06/FC0F/FC10, rejects bad CRCs and broadcast writes, and requires explicit acknowledgement plus confirmation.
- Never commit Home Assistant tokens, and never expose the panel or services to untrusted networks without HA authentication.

## Support & feedback

- 🐞 **Bug reports & template requests:** [issue tracker](https://github.com/sandro-defender/ha-modbus-usb/issues) — include device model, serial settings, slave ID, template used, and a copied Diagnostics log.
- 💬 **Questions:** open a discussion or issue with photos of wiring/labels where relevant.
- 📖 **Changelog:** [CHANGELOG.md](CHANGELOG.md) — every release lists user-facing changes.

## Contributing

Pull requests are welcome — especially new verified templates, diagnostics improvements, and test coverage.

Use Python 3.12 for the pinned Home Assistant 2025.1.4 test environment.
Python 3.11 uses Home Assistant 2024.3.3 as an older compatibility check.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements_test.txt
python -m pytest -q
ruff check custom_components tests
```

CI runs tests on both Python versions against Pymodbus 3.6.9 and 3.15.0,
including template validation, API authorization, numeric encoding, and reload
regressions. Tests mock serial I/O; they do not certify real board behavior.
Run a hardware smoke test before relying on a release for relay automations.

Template and entity saves reject invalid register spans, slave IDs, non-finite
numbers, and incompatible register types before changing configuration. Template
writes are atomic: validation or disk-write failures leave the previous file
intact. REST template edits and direct panel switch writes require an admin;
normal Home Assistant entity controls continue to use HA's permissions.

Template contributions should cite the vendor manual (link + page/register table) and note whether the map was verified on hardware. Community-sourced maps stay marked **untested** until someone confirms them.

<details>
<summary><strong>Maintainer notes — publishing a release</strong></summary>

1. Bump `version` in `custom_components/modbus_usb/manifest.json`.
2. Bump the matching panel cache version in `custom_components/modbus_usb/__init__.py` (`modbus-panel.html?v=…`).
3. Add a user-facing entry under that version in `CHANGELOG.md`.
4. Merge to `main`. GitHub Actions validates the integration and creates **one** tag + release per manifest version; the matching changelog section becomes the release notes.

Every release needs a new manifest version — re-pushing an existing version updates code but cannot create a second tag.

</details>

## Acknowledgements

Register maps and inspiration from community sources: [modbus_connect](https://github.com/dmatscheko/modbus_connect) (MIT) for SDM230 and Waveshare Relay (D) starters, [home-assistant_sdm630](https://github.com/ticapix/home-assistant_sdm630) (Apache-2.0) for the SDM630 starter, and [drp-modbus-boards](https://github.com/r-renato/drp-modbus-boards) for eletechsup community maps. Vendor documentation links live in each template's `info_url` / `.md` reference.

## License

[MIT License](LICENSE).
