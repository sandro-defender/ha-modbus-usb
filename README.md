# Modbus USB Controller

<p align="center">
  <img src="https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-18bcf2?style=for-the-badge&logo=homeassistant&logoColor=white" alt="Home Assistant">
  <img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge" alt="HACS">
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom"></a>
  <a href="https://github.com/sandro-defender/ha-modbus-usb/releases"><img src="https://img.shields.io/github/v/release/sandro-defender/ha-modbus-usb" alt="GitHub Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
  <img src="https://img.shields.io/badge/HA-2024.1%2B-green.svg" alt="Home Assistant 2024.1+">
  <img src="https://img.shields.io/badge/Modbus-RTU%20%2F%20USB-purple.svg" alt="Modbus RTU USB">
</p>

Connect **Modbus RTU devices on a USB–RS-485 adapter** to Home Assistant. One serial hub, many slaves. Sensors, switches, binary sensors, and numbers are created from the UI — no YAML entity definitions required.

A **sidebar panel** (`Modbus USB`) is the main control surface: live values, device management, YAML templates, hub settings, and register diagnostics.

---

## Why this integration

Home Assistant’s built-in Modbus integration is YAML-first and treats the serial port as a bag of registers. This project models the bus the way it actually works:

| Layer | What it is | Home Assistant |
| --- | --- | --- |
| **Hub** | USB serial adapter (port, baud, parity, poll interval) | Config entry + hub device |
| **Device** | One Modbus slave (ID 1–247), model, manufacturer | Child device *via* the hub |
| **Entity** | A register or coil mapped to a platform | Sensor / switch / binary sensor / number |

Multiple meters, relays, and sensors can share the same RS-485 cable. Each device keeps its own slave ID; entities inherit it automatically.

---

## Features

### Platforms

| Platform | Register types | Notes |
| --- | --- | --- |
| **Sensor** | Holding, Input | Scale, unit, device class, state class (`measurement` / `total` / `total_increasing`) |
| **Switch** | Coil, Holding | Optional custom ON / OFF values for holding registers |
| **Binary sensor** | Coil, Discrete input | Read-only; HA binary-sensor device classes |
| **Number** | Holding | Slider or box; min / max / step / scale |

**Data types:** `uint16`, `int16`, `uint32`, `int32`, `float32` (32-bit values use two registers, big-endian / high word first).

### Sidebar panel

Open **Modbus USB** in the Home Assistant sidebar after the integration is loaded.

- **Devices** — Add, edit, and remove slaves. Apply a template or add entities on a specific device.
- **Templates** — YAML files in `config/modbus_usb_templates/`. Create, edit, duplicate, and delete from the panel. Bundled templates stay read-only until you choose **Save to HA**.
- **Dashboard** — Live tiles, switch toggles, number controls, device chips, counts (total / by type / unavailable), refresh ~15 s.
- **Entities** — Full table (device, type, register, address, data type, scale, unit) with inline edit / delete.
- **Hub** — Serial settings and poll interval (1–3600 s) without walking the options-flow wizard.
- **Diagnostics** — One-shot register read / write with slave ID, backed by the same HA services used in automations.

### Runtime

- Automatic retry if the USB adapter is missing at startup or disconnects later
- Devices and entities reload when options change
- `modbus_usb.read_register` / `modbus_usb.write_register` for automations and Developer Tools
- HACS + GitHub Releases for update notifications

---

## Bundled templates

Bundled templates are available in the **Templates** tab but are not copied into `config/modbus_usb_templates/` automatically. Choose **Save to HA** when you want to store a template inside Home Assistant; it then becomes editable. Treat templates as starting points — verify addresses against your datasheet.

| File | Device | Entities |
| --- | --- | --- |
| `sdm120.yaml` | Eastron SDM120 | Voltage, current, power, PF, frequency, energy |
| `dds238.yaml` | Hiking DDS238-2 ZN/S | Energy, V, I, P, Q, PF, frequency |
| `xy_md02.yaml` | XY-MD02 (temp / humidity) | Temperature, humidity |
| `relay_8ch.yaml` | 8-ch Modbus relay | 8 coil switches + 2 discrete inputs |
| `eletechsup-R413E16.yaml` | eletechsup R413E16 | 16 command switches (holding registers 1–16) |
| `generic_meter.yaml` | Generic RTU device | Sensor, number, switch, binary sensor |

Template files look like this:

```yaml
id: xy_md02
name: XY-MD02 Temperature & Humidity
manufacturer: SHT20
model: XY-MD02
default_slave_id: 1
description: RS485 Modbus RTU temperature and humidity environmental sensor
entities:
  - name: Temperature
    entity_type: sensor
    register_type: input
    address: 1
    data_type: int16
    scale: 0.1
    unit_of_measurement: "°C"
    device_class: temperature
    state_class: measurement
```

### Creating a board template

The simplest reliable source is the board's Modbus register map. In the **Templates** tab, duplicate `generic_meter.yaml`, then change the device details and add one entity for every register or coil you need. A template can contain sensors, switches, binary sensors, and numbers.

For a holding-register relay that needs special command values, use this pattern:

```yaml
id: my_relay
name: My RS-485 Relay
manufacturer: My Manufacturer
model: My Relay Model
default_slave_id: 1
entities:
  - name: Relay 1
    entity_type: switch
    register_type: holding
    address: 1
    data_type: uint16
    on_value: 256
    off_value: 512
    assumed_state: true
```

`assumed_state: true` is for command-only boards that cannot safely report their current state. For normal coils, omit the custom values and use `register_type: coil`.

Templates can optionally include a read-only `fingerprint`. After **Find RS-485 Devices** finds a responding slave, the panel suggests a template only when every fingerprint value is within its expected range:

```yaml
fingerprint:
  - register_type: input
    address: 0
    data_type: float32
    min_value: 80
    max_value: 300
```

This is a helpful hint, not proof of the model—different devices can return similar values. Do not add a fingerprint that writes to the device. Command-only boards such as the R413E16 are deliberately not auto-suggested because a safe scan cannot identify them.

---

## Requirements

- Home Assistant **2024.1.0** or newer
- **pymodbus ≥ 3.6.0** (installed automatically)
- A USB-to-RS-485 adapter and at least one Modbus RTU slave

---

## Installation

### HACS

1. **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/sandro-defender/ha-modbus-usb` as category **Integration**
3. Download **Modbus USB Controller**
4. Restart Home Assistant
5. **Settings → Devices & Services → Add Integration → Modbus USB Controller**

### Manual

1. Copy `custom_components/modbus_usb/` into `config/custom_components/`
2. Restart Home Assistant
3. Add the integration as above

---

## Setup

### 1. Create the hub

**Settings → Devices & Services → Add Integration → Modbus USB Controller**

| Field | Typical values |
| --- | --- |
| Serial port | `/dev/ttyUSB0` (HAOS / Linux), `COM3` (Windows) |
| Baud rate | 1200–115200 (default **9600**) |
| Data bits | 7 or 8 (default **8**) |
| Parity | None / Even / Odd (default **N**) |
| Stop bits | 1 or 2 (default **1**) |
| Default slave ID | 1–247 (fallback when a device has no ID of its own) |
| Poll interval | 1–3600 seconds (default **10**) |

The adapter does not need to be connected on first save; the coordinator keeps retrying.

### 2. Add devices and entities

Prefer the **Modbus USB** sidebar:

1. Open **Devices** and add a slave (name, slave ID, optional manufacturer / model).
2. Apply a template, or add sensors / switches / binary sensors / numbers on that device.
3. Confirm live values on the **Dashboard**.

You can still use **Configure** on the integration card (options flow) to add or edit entities one at a time.

### Register addresses

Use the device datasheet:

| Type | Meaning |
| --- | --- |
| Holding | 16-bit read / write |
| Input | 16-bit read-only |
| Coil | 1-bit read / write |
| Discrete | 1-bit read-only |

Many manuals use **1-based** or **40001-style** numbering. If the sheet says holding `40001`, the Modbus address is usually **0**. If reads look shifted by one, subtract 1 from the documented address.

---

## USB access (HAOS / Docker)

The serial device must be visible inside Home Assistant.

**Home Assistant OS / Supervised** — plug in the adapter and pick the port from **Settings → System → Hardware** (often `/dev/ttyUSB0` or `/dev/serial/by-id/...`). Prefer a `/dev/serial/by-id/` path so the port stays stable across reboots.

**Docker** — pass the device through:

```yaml
# docker-compose.yml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

Or: `docker run --device /dev/ttyUSB0:/dev/ttyUSB0 ...`

---

## Services

Both services take `entry_id` (config entry ID from **Settings → Devices & Services → Modbus USB Controller**). Optional `slave_id` overrides the hub default.

## Diagnostics and troubleshooting

Open **Modbus USB → Diagnostics** to confirm the serial port is connected, inspect successful and failed reads, and run a direct register read. The sidebar keeps the most recent 200 decoded Modbus transactions (reads and writes) for the current Home Assistant session. It shows the slave ID, address, returned value or error, and response time; use **Copy** to include the log in a support request.

Use **Find RS-485 Devices** when the slave ID or baud rate is unknown. Start with IDs `1–20` and common speeds such as `9600,19200,38400`; expand to `1–247` only if needed, because a full scan can take several minutes. The scanner pauses normal Modbus traffic while it probes and restores the configured connection when finished. A detected *exception response* still confirms that a device answered at that slave ID and baud rate.

The activity log is not a raw byte capture. For raw Pymodbus frame-level output, enable debug logging for `pymodbus` and `custom_components.modbus_usb` in Home Assistant's logger configuration.

```yaml
# configuration.yaml — temporary diagnostics logging
logger:
  default: warning
  logs:
    custom_components.modbus_usb: debug
    pymodbus: debug
```

If the port is disconnected, check the adapter path, USB permissions/passthrough, A/B polarity, serial settings, bus termination, and that every device has a unique slave ID.

### `modbus_usb.read_register`

Reads once and fires event `modbus_usb_register_read`.

```yaml
action:
  - service: modbus_usb.read_register
    data:
      entry_id: "YOUR_ENTRY_ID"
      address: 0
      register_type: holding   # holding | input | coil | discrete
      data_type: uint16        # holding/input only
      slave_id: 1              # optional, 1–247
```

React to the result:

```yaml
automation:
  trigger:
    - platform: event
      event_type: modbus_usb_register_read
  condition:
    - condition: template
      value_template: "{{ trigger.event.data.success and trigger.event.data.address == 0 }}"
  action:
    - service: notify.persistent_notification
      data:
        message: "Register 0 = {{ trigger.event.data.value }}"
```

### `modbus_usb.write_register`

Writes a coil or holding register.

```yaml
automation:
  trigger:
    - platform: time
      at: "06:00:00"
  action:
    - service: modbus_usb.write_register
      data:
        entry_id: "YOUR_ENTRY_ID"
        address: 10
        register_type: holding   # holding | coil
        value: 1                 # coils: 0 = off, 1 = on
        slave_id: 1
```

---

## Releasing updates

1. Bump `"version"` in [`custom_components/modbus_usb/manifest.json`](custom_components/modbus_usb/manifest.json).
2. Commit and push the version bump to `main`.
3. GitHub Actions validates the source, creates the matching tag, and creates the GitHub Release with generated release notes automatically.
4. HACS users receive an **Update available** notice.

Each manifest version is released once. Pushing more commits with the same version does not modify the existing release; bump the version for the next release.

## Development tests

Install the development dependencies and run the regression suite before releasing changes:

```bash
python -m pip install -r requirements_test.txt
python -m pytest
```

The tests cover Modbus diagnostics, serial request serialization, reconnect handling, slave IDs, and the bundled R413E16 template.

---

## Contributing

Issues and pull requests: [github.com/sandro-defender/ha-modbus-usb](https://github.com/sandro-defender/ha-modbus-usb/issues).

---

## License

[Apache License 2.0](LICENSE).

---

## Changelog

### 2026-09-06
- Rewrote README: hub → device → entity model, sidebar-first setup, bundled templates, services, USB passthrough, Apache 2.0 badge (was incorrectly listed as MIT).
- Date modified: 2026-09-06

### 2026-09-08
- Added reliable, serialized RS-485 diagnostics with connection health, activity filtering, log export, and direct read results.
