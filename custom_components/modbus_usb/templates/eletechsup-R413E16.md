# Eletechsup R413E16 — verified protocol reference

This note documents the commands used by `eletechsup-R413E16.yaml`. It is a concise implementation reference, not a replacement for the manufacturer’s complete command document.

## Board and connection

- 16 output channels, 5 V TTL outputs; this is a control core, not a relay board by itself.
- RS-485 Modbus RTU and TTL serial are available. The Home Assistant template uses Modbus RTU only.
- Default serial setup: 9600 baud, 8 data bits, no parity, 1 stop bit.
- Default Modbus slave ID: 1. Valid configured IDs are 1–247.
- The M0 jumper selects the physical output polarity. It is a hardware setting and cannot be changed by Modbus.

## Confirmed Modbus registers

All commands use function code 06 (write one holding register) unless noted.

| Purpose | Holding register | Value |
| --- | ---: | ---: |
| Channel 1–16 ON | 1–16 | `0x0100` / 256 |
| Channel 1–16 OFF | 1–16 | `0x0200` / 512 |
| Channel 1–16 toggle | 1–16 | `0x0300` / 768 |
| Channel 1–16 interlock | 1–16 | `0x0400` / 1024 |
| Channel 1–16 momentary | 1–16 | `0x0500` / 1280 |
| Channel 1–16 delayed action | 1–16 | `0x0600 + seconds` (0–255 seconds) |
| All channels ON | 0 | `0x0700` / 1792 |
| All channels OFF | 0 | `0x0800` / 2048 |
| Set baud rate | 254 / `0x00FE` | 0=1200, 1=2400, 2=4800, 3=9600, 4=19200 |
| Set slave ID | 255 / `0x00FF` | 1–247 |

**Important:** the detailed command guide says baud value `5` performs a factory reset. This integration intentionally does not offer it.

## Status reads

Function code 03 can read the output state. Read holding registers 1–16; `1` means ON and `0` means OFF. A broadcast read of the slave ID uses address `0xFF`, register `0x00FF`; only one board may be connected while doing that broadcast query.

## Current template coverage

The template includes individual channel switches, an **All Channels** switch, configurable group switches, a safe baud-rate selector, and slave-ID configuration. It uses assumed state for output switches because earlier tested hardware behavior was command-oriented.

## Safe next additions

1. **Live channel feedback:** poll registers 1–16 and display the actual ON/OFF status instead of only the last command.
2. **Momentary buttons:** trigger one channel for the board’s fixed one-second pulse.
3. **Timed buttons:** expose the documented 0–255 second delayed command.
4. **Toggle and interlock buttons:** useful for manual panels, but should not replace normal ON/OFF switches.

Do not add factory reset to the regular UI. It can erase the working communication setup.

## Sources

- Eletechsup’s [R413E16 basic-command post](https://eletechsup.com/blogs/news/eletechsup-r413e16-r413d08-multi-function-io-control-some-basic-command)
- [16 Channel Multifunction RS485 Module command guide](https://www.scribd.com/document/721734180/16-Channel-Multifunction-RS485-Module-Commamd) (third-party copy of the detailed command guide)
- [R413E16 product/manual overview](https://manuals.plus/asin/B0DDXSNH4W) (independent overview)
