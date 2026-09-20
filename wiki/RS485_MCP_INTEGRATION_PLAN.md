# RS-485 MCP integration plan

## Goal

Use MCP tools to speed up template development, serial diagnostics, and UI
testing without allowing two applications to drive the same RS-485 adapter.

## Non-negotiable safety rule

Home Assistant owns a configured live adapter.  An MCP server must never open
that same serial port while the integration is connected.  Concurrent masters
can interleave RTU frames, cause timeouts, change an unintended board setting,
or leave a USB adapter busy.

Use one of these modes:

1. **Test-bench mode (first):** MCP owns a separate USB-to-RS-485 adapter and
   one non-critical board.
2. **Live-observer mode (later):** MCP calls Home Assistant's authenticated
   API and this integration performs the serial work under its existing lock.
   MCP itself does not open a port.

## Installed MCP roles

| Tool | Use in this project | Live-bus policy |
| --- | --- | --- |
| `modbus-mcp` | Read/write known Modbus RTU registers and simulate a slave for tests. | Test adapter only. Writes require an approved test plan. |
| `mcp-rs485` | Inspect ports and capture/send raw bytes for vendor-protocol investigation. | Test adapter only. No raw write guessing. |
| `serial-mcp` | Detect baud settings, collect a buffered serial log, and inspect DTR/RTS. | Test adapter only; do not toggle control lines on a live RS-485 adapter. |
| `playwright` | Exercise the Home Assistant panel without serial access. | Safe for UI tests. |
| `context7` | Look up current Home Assistant and PyModbus documentation. | No device access. |

## Phase 1 — bench workflow

1. Label a dedicated test USB-to-RS-485 adapter and connect one board with a
   fused, non-critical load.
2. Stop or disconnect Home Assistant from that adapter before opening it with
   an MCP tool.
3. Use `serial-mcp` or `mcp-rs485` to list ports and verify baud rate, parity,
   stop bits, and the expected slave ID. Save a redacted capture with no
   credentials or personal network details.
4. Use `modbus-mcp` for documented *read* functions first. Compare each reply
   with the vendor manual and the matching YAML template.
5. Only after a human-reviewed test sheet, perform one documented write at a
   time. Record the function code, address, old value, new value, result, and
   physical board behavior.
6. Promote verified mappings into the YAML template, add a regression fixture,
   then test the same flow through Board Tools.

## Phase 2 — automated regression lab

1. Run a disposable Modbus RTU slave with `modbus-mcp` (or PyModbus) using the
   same register maps as a target template.
2. Add pytest coverage for grouped reads, timeout/reconnect handling, template
   controls, and command confirmation.
3. Add Playwright smoke tests for collapsed cards, discovery-to-Board-Tools,
   M0 template fields, and the explicit write-confirmation path.
4. Run these tests in CI with no physical serial adapter attached. Hardware
   tests stay manual and are clearly marked as such.

## Phase 3 — optional live Home Assistant MCP companion

Build a small companion MCP server only after Phase 2 is stable. It should
talk to Home Assistant over an authenticated WebSocket/REST interface, never
to `COMx` directly.

Initial read-only tools:

- `list_hubs`
- `get_bus_health`
- `get_recent_transactions`
- `list_templates`
- `discover_devices` (uses the integration's existing queue and serial lock)

Write tools should be added last and require all of:

- a configured template command or an explicit raw command mode;
- a human-visible summary of target, function, address, and value;
- a short-lived confirmation token returned by a separate prepare step;
- an activity-log record and a post-write readback when supported.

The server must use a least-privilege Home Assistant token, bind only to the
local network, and make write capability opt-in.  Never put a Home Assistant
token in this repository, a template, or an MCP configuration committed to
Git.

## Completion criteria

- A bench test can reproduce an R4D6F20 and R413E16 read safely.
- A failed MCP connection cannot interrupt Home Assistant polling.
- Every new template command has vendor evidence, a test capture/fixture, and
  a clear confirmation rule.
- CI covers the panel workflow without requiring hardware.
