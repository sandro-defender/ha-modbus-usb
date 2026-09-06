# Modbus USB Controller for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/sandro-defender/ha-modbus-usb)](https://github.com/sandro-defender/ha-modbus-usb/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A feature-rich Home Assistant custom integration that connects to any **Modbus RTU device over USB** (RS-485 serial adapter). Configure sensors, switches, binary sensors and number entities entirely from the UI — zero YAML required.

Includes a **custom sidebar panel** with live dashboard, entity table, connection details, and a built-in Modbus register diagnostic tool.

---

## Features

### Entity platforms
| Platform | Register types | Description |
|----------|----------------|-------------|
| **Sensor** | Holding, Input | Read numeric values with scale, unit, device class, state class |
| **Switch** | Coil, Holding | Toggle coils or holding registers with custom ON/OFF values |
| **Binary Sensor** | Coil, Discrete input | Read-only on/off state with HA binary sensor device class |
| **Number** | Holding | Writable register as a slider or text box (min/max/step/scale) |

### Sidebar Panel & UI Configuration
A dark, glassmorphism dashboard and management panel accessible directly from the HA sidebar:

- **🎛 Devices** — Manage separated, individual Modbus devices with their own Slave IDs (1–247), models, and manufacturers. Add entities directly or apply templates per device.
- **📑 Device Templates** — Device templates saved as individual `.yaml` files in `modbus_usb_templates/`. Create, edit (with built-in YAML editor), duplicate, and delete templates right from the sidebar.
- **📊 Live Dashboard** — Live entity tiles with current values, inline switch toggles, number controls, device filter chips, stats bar (total / per-type / unavailable counts), auto-refresh every 15 s.
- **📋 All Entities** — Complete table of all configured entities with device tags, type, register, address, data type, scale, unit, and inline edit/delete actions.
- **🔗 Hub & Connection** — Serial port parameters at a glance + direct "Edit Hub Settings" modal to reconfigure serial port, baud rate, parity, stop bits, and poll interval without opening HA options flow wizard.
- **🔬 Diagnostics** — One-shot live register reads and writes via custom HA services, with target slave ID selector.

### Services
Two custom services usable in automations and Developer Tools:

| Service | Description |
|---------|-------------|
| `modbus_usb.read_register` | One-shot live read with optional `slave_id` → fires `modbus_usb_register_read` event with the value |
| `modbus_usb.write_register` | Write a value to a coil or holding register with optional `slave_id` |

### Other
- **100% Sidebar UI Configurable** — add/edit/remove hubs, separated devices, entities, and templates directly from the sidebar UI without modal wizard popups
- **Separate Device Architecture** — each Modbus device is registered in HA Device Registry under the hub
- **Per-Device Slave ID** — supports multiple devices on the same RS-485 serial bus with distinct slave addresses (1–247)
- **Preloaded YAML Templates** — includes starter templates for Eastron SDM120, XY-MD02, 8-Channel Relay, and DDS238
- Adjustable poll interval (1–3600 s)
- Automatic reconnection if the USB device is temporarily disconnected
- HACS-compatible with GitHub Releases for update notifications
- `mdi:usb` icon in the integrations list

---

## Requirements

- Home Assistant 2024.1 or newer
- `pymodbus >= 3.6.0` (installed automatically)
- A USB-to-RS485 adapter and a Modbus RTU device

---

## Installation via HACS

1. In Home Assistant open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/sandro-defender/ha-modbus-usb` — category **Integration**.
3. Find **Modbus USB Controller** and click **Download**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration → Modbus USB Controller**.

---

## Manual installation

1. Download or clone this repository.
2. Copy `custom_components/modbus_usb/` into your HA `config/custom_components/` directory.
3. Restart Home Assistant.

---

## Setup

1. **Settings → Devices & Services → Add Integration → Modbus USB Controller**
2. Enter your serial port (e.g. `/dev/ttyUSB0` on Linux/HAOS, `COM3` on Windows), baud rate, parity, stop bits, slave/unit ID and poll interval.
3. After setup, click **Configure** on the integration card to add entities.

### Adding entities

Go to **Settings → Devices & Services → Modbus USB Controller → Configure** and choose:

- **Add a sensor** — specify register type (holding/input), address, data type, scale, unit, device class and state class.
- **Add a switch** — specify coil or holding register, address, and optional ON/OFF register values.
- **Add a binary sensor** — specify coil or discrete-input address and device class.
- **Add a number** — specify holding register, address, data type, scale, unit, min/max/step and display mode (slider or box).

### Finding your register map

Check your device's documentation or datasheet to identify:
- **Register type** — holding (read/write 16-bit), input (read-only 16-bit), coil (read/write 1-bit), discrete input (read-only 1-bit)
- **Address** — 0-based or 1-based depending on the device (subtract 1 from the datasheet address if it starts at 40001)
- **Data type** — uint16, int16, uint32, int32 or float32

---

## USB passthrough (Docker / HAOS)

Make sure the USB adapter is accessible inside the HA container:

**HAOS** — use the *Hardware* page to enable USB passthrough, or add the device in your `configuration.yaml`:
```yaml
homeassistant:
  usb_path: /dev/ttyUSB0
```

**Docker** — add `--device /dev/ttyUSB0:/dev/ttyUSB0` to your `docker run` command, or add to `docker-compose.yml`:
```yaml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

---

## Using the services in automations

### Read a register and act on the value
```yaml
automation:
  trigger:
    - platform: event
      event_type: modbus_usb_register_read
  condition:
    - condition: template
      value_template: "{{ trigger.event.data.success and trigger.event.data.address == 100 }}"
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: "Register 100 = {{ trigger.event.data.value }}"
```

### Write a register on a schedule
```yaml
automation:
  trigger:
    - platform: time
      at: "06:00:00"
  action:
    - service: modbus_usb.write_register
      data:
        entry_id: "YOUR_ENTRY_ID"   # find in Settings → Devices & Services → Integration → entry ID
        address: 10
        register_type: holding
        value: 1
```

---

## Releasing updates

1. Bump `"version"` in [`manifest.json`](custom_components/modbus_usb/manifest.json).
2. Commit and push.
3. Create a GitHub **Release** with a matching tag (e.g. `v1.1.1`).
4. HACS users will see an **Update available** notification.

---

## Contributing

Issues and pull requests are welcome at [github.com/sandro-defender/ha-modbus-usb](https://github.com/sandro-defender/ha-modbus-usb/issues).

---

## License

MIT — see [LICENSE](LICENSE).
