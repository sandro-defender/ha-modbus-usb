# Eletechsup R4D6F20 protocol reference

This concise reference supports `eletechsup-R4D6F20.yaml`. It is based on the
manufacturer's R4D6F20 Command 1 and Command 2 documents and is not a
replacement for the full manufacturer manual.

## Board and connection

- 20 relay outputs, 2 PNP active-high digital inputs, one 4–20 mA input, and
  one 0–10 V input.
- Factory serial configuration: 9600 baud, 8 data bits, no parity, 1 stop bit.
- Factory Modbus slave ID: 1. The board's DIP switches select IDs 1–63.
- This combined template has two profiles selected from **R4D6F20 board
  tools**. Set the checkbox to match the physical M0 jumper, then save the
  mode. Saving changes the Home Assistant entity map only; it does not write to
  the board.
  - **Command 1 / M0 open** uses FC03 holding-register reads and FC06 writes.
  - **Command 2 / M0 shorted** uses FC01 relay-coil reads/writes, FC02 digital
    input reads, and FC03 holding-register reads for analog and configuration
    values.

## Template register map

| Function | Holding register | Value or scale |
| --- | ---: | --- |
| Relay CH-01 to CH-20 commands | 0 to 19 | `0x0100` (256) ON, `0x0200` (512) OFF |
| Relay CH-01 to CH-20 state | 0 to 19 | `1` ON, `0` OFF |
| PNP input DI-01 | 128 / `0x0080` | `1` active, `0` inactive |
| PNP input DI-02 | 129 / `0x0081` | `1` active, `0` inactive |
| Current input | 160 / `0x00A0` | raw value × 0.01 mA |
| Voltage input | 161 / `0x00A1` | raw value × 0.01 V |

The integration control menu exposes the documented commands below. The board
is still untested with this integration, so use the menu only with a safe test
load and confirm the selected device before applying a board-wide command.

## Useful documented board commands

## Command 2 register map (M0 shorted)

| Function | Address range | Meaning |
| --- | ---: | --- |
| FC01 / FC05 / FC15 | coils 0–19 | Relay CH-01 to CH-20 state and ON/OFF control |
| FC02 | discrete inputs 0–1 | PNP DI-01 and DI-02 state |
| FC03 / FC04 | input registers 0–1 | Current and voltage input values |
| FC03 / FC06 / FC16 | holding registers 128–255 | Board special functions, including serial settings and reset |

The Command 2 profile reads current and voltage from input registers 0 and 1
with the same ×0.01 scaling. It deliberately does not offer Command 1's toggle,
interlock, momentary, or timed action registers: they are not part of Command
2. Use the individual relay switches for Command 2 ON/OFF control.

These commands are available from **R4D6F20 controls** in the device card:

| Purpose | Holding register | Value |
| --- | ---: | ---: |
| All relays ON | 0 | `0x0700` |
| All relays OFF | 0 | `0x0800` |
| Read board slave ID | 253 / `0x00FD` | FC03 read |
| Set baud rate | 254 / `0x00FE` | 0=1200, 1=2400, 2=4800, 3=9600, 4=19200, 5=38400, 6=57600, 7=115200 |
| Set parity | 255 / `0x00FF` | 0=none, 1=odd, 2=even |
| Restore factory settings | 251 / `0x00FB` | write `0` to the board address or broadcast `0xFF` |

Baud-rate and parity changes take effect after a power cycle. Restore settings
only with one board connected, particularly when using the broadcast address.

## Sources

- [Eletechsup R4D6F20 product page](https://eletechsup.com/products/r4d6f20-smart-20-channel-rs485-relay-board-with-plc-io-module-modbus-for-multifunction-control)
- [Eletechsup R4D6F20 manufacturer manual archive](https://485io.com/eletechsup/R4D6F20-1.rar)
