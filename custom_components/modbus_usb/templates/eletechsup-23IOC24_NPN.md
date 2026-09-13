# Eletechsup 23IOC24 NPN protocol reference

This concise reference supports `eletechsup-23IOC24_NPN.yaml`. It is based on
the manufacturer’s 23IOXXX command documents and is not a replacement for the
complete manufacturer manual.

## Board and connection

- 24 NPN optically isolated digital inputs and 24 NPN Darlington-transistor
  digital outputs. Each output is rated for a maximum of 300 mA.
- Default serial setup: 9600 baud, 8 data bits, no parity, 1 stop bit.
- Default Modbus slave ID: 1. The board DIP switches select IDs 1–63.
- This template uses the factory-default **Command 1** map with **M0 open**:
  function 03 reads holding registers and function 06 writes one holding
  register.
- Shorting M0 enables Command 2, a distinct standard coil/discrete-input map.
  Do not apply this template to a board configured for Command 2.

## Template register map

| Function | Holding registers | Value |
| --- | ---: | --- |
| Outputs CH-01 to CH-24 commands | 0 to 23 | `0x0100` ON, `0x0200` OFF |
| Outputs CH-01 to CH-24 state | 0 to 23 | `1` ON, `0` OFF |
| Inputs DI-01 to DI-24 | 128 to 151 / `0x0080` to `0x0097` | `1` active, `0` inactive |

## Other documented commands

| Purpose | Holding register | Value |
| --- | ---: | ---: |
| Output bitmap CH-01 to CH-24 | 112 to 114 / `0x0070` to `0x0072` | one bit per output; 1=ON |
| All outputs ON | 0 | `0x0700` |
| All outputs OFF | 0 | `0x0800` |
| Input/output relationship | 250 / `0x00FA` | 0=unrelated, 1=self-locking, 2=all-channel interlock, 3=momentary, 4=two-channel interlock, 5=output follows input |
| Input auto-report interval | 248 / `0x00F8` | 0=disabled, otherwise seconds |
| Board slave ID read | 253 / `0x00FD` | FC03 read, one board only for broadcast query |
| Baud rate | 254 / `0x00FE` | 0=1200 through 7=115200 |
| Parity | 255 / `0x00FF` | 0=none, 1=odd, 2=even |
| Factory reset | 251 / `0x00FB` | write `0` to the board address or broadcast `0xFF` |

The initial template deliberately exposes only normal Home Assistant switches
and input entities. It does not expose the board-wide output modes, remote-I/O
sender/receiver, or configuration registers until this board is tested with the
integration.

## Sources

- [Eletechsup 23IOC24_NPN product page](https://eletechsup.com/products/23ioa08-23iob16-23ioc24-23ioe48-8-16-24-32-48ch-multifunction-rs485-remote-io-module-plc-di-do-expansion-board-din-rail-box-standard-modbus-rtu-protocol)
- [Eletechsup 23IOXXX manufacturer manual archive](https://485io.com/eletechsup/23IOA08_23IOB16_23IOC24_23IOD32_23IOE48.rar)
