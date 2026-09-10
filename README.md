# Modbus USB Controller

<p align="center">
  <img src="custom_components/modbus_usb/brand/logo.png" width="160" alt="Modbus USB Controller">
</p>

<p align="center">
  Connect RS-485 Modbus devices to Home Assistant with a simple visual setup.<br>
  No hand-written entity YAML required.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom"></a>
  <a href="https://github.com/sandro-defender/ha-modbus-usb/releases"><img src="https://img.shields.io/github/v/release/sandro-defender/ha-modbus-usb" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache 2.0 license"></a>
</p>

Modbus USB Controller lets you connect energy meters, relay boards, temperature sensors, and other Modbus RTU equipment through a USB-to-RS-485 adapter. Add one hub, then add as many devices as your RS-485 network contains.

## What you can do

- Add and manage multiple Modbus devices from Home Assistant
- Create sensors, switches, numbers, and binary sensors from a friendly interface
- Start quickly with built-in board templates
- Use the live dashboard to control relays and see current values
- Find an unknown slave ID or baud rate with the RS-485 scanner
- Check connection health, recent reads/writes, and errors in Diagnostics
- Use direct register reads and writes when testing a device

The integration includes starter templates for popular energy meters, relay boards, temperature/humidity sensors, and the **eletechsup R413E16** 16-channel I/O board.

## Before you begin

You need:

- Home Assistant 2024.1 or newer
- A USB-to-RS-485 adapter connected to Home Assistant
- A Modbus RTU device with its wiring and power ready

Make sure the RS-485 A/B wires are connected correctly and that every device on the same bus uses a different slave ID.

## Install

### HACS

1. Open **HACS → Integrations**.
2. Select the menu (⋮), then **Custom repositories**.
3. Add `https://github.com/sandro-defender/ha-modbus-usb` and choose **Integration**.
4. Download **Modbus USB Controller**.
5. Restart Home Assistant.

### Manual installation

Copy the `custom_components/modbus_usb` folder into your Home Assistant `config/custom_components` folder, then restart Home Assistant.

## Quick start

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **Modbus USB Controller**.
3. Choose your USB serial port and enter your device’s serial settings. Most devices use `9600`, `8` data bits, `None` parity, and `1` stop bit.
4. Open **Modbus USB** from the Home Assistant sidebar.
5. Open **Templates**, select your board, and choose **Use Template**.
6. Set the device name and slave ID, then save.
7. Open **Dashboard** to see readings or control your relays.

If you do not know the correct slave ID or baud rate, use **Diagnostics → Find RS-485 Devices** first.

## Templates

Templates make adding a known board much faster. They contain its usual registers, channels, and sensible names.

Bundled templates are available immediately, but are not automatically copied into your Home Assistant files. Select **Save to HA** only when you want your own editable copy. Your saved templates live in `config/modbus_usb_templates`.

### Tested and confirmed

✅ **eletechsup R413E16** — confirmed working with this integration. The template creates channels named **CH-01** through **CH-16**; set the slave ID to match the address configured on your board.

All other bundled templates are clearly marked **Not yet tested**. They are useful starting points based on published register maps, but please verify them with your own device before relying on them in automations.

Community templates imported from GitHub currently include the **Eastron SDM230**, **Eastron SDM630**, **Waveshare Modbus RTU Relay (D)**, **eletechsup N4ROD08**, and **eletechsup NT18B07**. They remain untested here.

## Diagnostics when something does not work

Open **Modbus USB → Diagnostics**. It shows whether the serial connection is available, recent successful commands, timeouts, and errors.

For a quick check:

1. Confirm the serial settings match the device manual.
2. Use the scanner to search a small slave-ID range, such as 1–20.
3. Try a direct read or write using the detected ID.
4. If there is no response, swap A and B once, check power, and verify USB access in Home Assistant.

An adapter’s transmit LED only confirms that Home Assistant sent a request. A received response or a successful diagnostic result confirms that the device answered.

## Support and feedback

Please report a problem or request a device template on the [issue tracker](https://github.com/sandro-defender/ha-modbus-usb/issues). Include your device model, serial settings, slave ID, and a copied Diagnostics log whenever possible.

<details>
<summary><strong>Advanced setup and technical reference</strong></summary>

### Serial settings

The serial port is commonly `/dev/ttyUSB0` on Home Assistant OS/Linux or `COM3` on Windows. A `/dev/serial/by-id/...` path is preferable because it stays stable after reboots.

For Docker installations, pass the adapter through to the container:

```yaml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

### Register types

- **Holding register** — 16-bit read/write value
- **Input register** — 16-bit read-only value
- **Coil** — one-bit read/write value
- **Discrete input** — one-bit read-only value

Device manuals may use addresses such as `40001`. This usually represents Modbus address `0`; if values appear one register off, try subtracting one from the address in the manual.

### Supported entity values

Sensors and numbers support `uint16`, `int16`, `uint32`, `int32`, and `float32`. Switches can use coils or holding registers; holding-register relay boards may require board-specific ON/OFF values from their manual.

### Automations and services

The integration provides `modbus_usb.read_register` and `modbus_usb.write_register` for automations and Developer Tools. Both accept the integration `entry_id`; `slave_id` is optional and overrides the hub default.

### Raw frame debugging

The sidebar activity log shows decoded requests and results, not raw serial frames. For temporary frame-level logging, add this to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.modbus_usb: debug
    pymodbus: debug
```

</details>

<details>
<summary><strong>Maintainer notes</strong></summary>

### Publishing a release

1. Update the version in `custom_components/modbus_usb/manifest.json`.
2. Update the matching panel cache version in `custom_components/modbus_usb/__init__.py`.
3. Add a clear user-facing entry under that version in `CHANGELOG.md`.
4. Commit and push to `main`.
5. GitHub Actions validates the integration and creates one release for that version. The matching changelog entry becomes the release description shown to update users.

Every release requires a new manifest version. Pushing another commit with an existing version updates the code but cannot create a second release tag.

### Local checks

```bash
python -m pip install -r requirements_test.txt
python -m pytest
```

</details>

## License

[Apache License 2.0](LICENSE).
