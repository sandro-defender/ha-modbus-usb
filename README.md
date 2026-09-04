# Modbus USB Controller for Home Assistant

Custom HACS integration that talks to a Modbus RTU controller connected over USB
(serial) and lets you add **sensors** and **switches** entirely from the UI —
no YAML. Each entity has its own configurable register address, name, data type,
etc., and updates automatically via HACS.

## Features

- Serial (Modbus RTU over USB) connection: port, baud rate, parity, stop bits, slave/unit ID
- Config UI to add/edit/remove sensors and switches at any time (Settings → Devices & Services → Modbus USB Controller → Configure)
- Sensors: read holding or input registers, with data type (uint16/int16/uint32/int32/float32), scale, unit, device class, state class
- Switches: backed by a coil, or by a holding register with custom ON/OFF values
- Adjustable poll interval
- Ships as a HACS custom repository with GitHub Releases for update notifications

## Installation via HACS

1. Push this repository to your own GitHub account (e.g. `github.com/yourname/ha-modbus-usb`).
2. In Home Assistant: **HACS → Integrations → ⋮ (top right) → Custom repositories**.
3. Add your repo URL, category **Integration**.
4. Find "Modbus USB Controller" in HACS and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration → Modbus USB Controller**.
7. Enter your USB serial port (e.g. `/dev/ttyUSB0`), baud rate, parity, slave ID, etc.
8. After setup, click **Configure** on the integration card to add sensors/switches
   with their register address, name, and type.

## Releasing updates (so HACS shows "Update available")

1. Bump `"version"` in `custom_components/modbus_usb/manifest.json`.
2. Commit and push.
3. Create a GitHub **Release** with a matching tag, e.g. `v1.0.1`.
4. HACS polls releases (not just commits) — users will see an update prompt.

## Finding your Modbus register map

You'll need your controller's documentation to know which addresses map to
which values (temperature, relay 1, etc.) and whether it's a coil, discrete
input, holding register, or input register — this integration is a generic
Modbus RTU client, not device-specific.

## Notes

- Requires the `pymodbus` Python package (installed automatically by HA from `manifest.json`).
- If Home Assistant runs in Docker/HAOS, make sure the USB device is passed
  through to the container (e.g. `/dev/ttyUSB0` mapped in your HAOS USB
  passthrough or Docker `--device`).
