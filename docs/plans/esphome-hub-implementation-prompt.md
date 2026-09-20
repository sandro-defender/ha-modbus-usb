# Prompt: v2.8.0 — ESPHome device as Modbus hub (RTU-over-TCP bridge + native API)

> Copy everything below the line into a new session. It is written for an agent
> working in `sandro-defender/ha-modbus-usb` on a branch off latest `main`
> (v2.7.1, PR #12 merged or rebased on top of it).

---

## Goal

Add a second hub transport to the Modbus USB Controller integration so an
**ESPHome device with an RS-485 module** can act as the Modbus hub instead of a
USB adapter attached to the Home Assistant host. Two transports, both
selectable in the hub setup:

1. **`esphome_tcp` (primary, recommended)** — ESPHome runs a UART↔TCP stream
   server; the integration talks **Modbus RTU framing over a TCP socket** with
   pymodbus (`ModbusTcpClient(framer=FramerType.RTU)`). Every existing feature
   (poll, writes, scanner, board tools, inspector capture via `trace_packet`)
   keeps working because it is still a pymodbus client.
2. **`esphome_api` (fallback)** — talk to the ESPHome native API with
   `aioesphomeapi`: send a raw RTU frame through a user-defined ESPHome service
   (`modbus_send`) and receive the reply through an ESPHome event
   (`esphome.modbus_rx`). No extra ESPHome component needed, higher latency,
   request/response pairing done by us. Implemented as a **pymodbus-compatible
   client shim** so the coordinator does not care which transport is active.

Deliverables: backend + config flow + panel + example ESPHome YAML for a
**generic ESP32 + MAX485 / auto-direction RS-485 module (UART2, GPIO16/17,
optional DE/RE flow-control pin)** + tests + docs + `manifest.json` → `2.8.0`.

## Repository conventions (do not break)

- Pure, HA-free Python modules for logic (`capture.py`, `inspector.py`,
  `designer.py`, `diagnostics.py` pattern). New transport logic goes into a new
  pure module (`transport.py` / `esphome_bridge.py`); HA glue stays thin.
- Mutating WebSocket commands are `@websocket_api.require_admin`; read-only
  ones are not.
- Panel JS lives in `custom_components/modbus_usb/www/panel/*.js`, loaded by
  `<script>` tags in `www/modbus-panel.html`: `state.js` first, `main.js` last.
  No inline scripts/styles in the HTML (`tests/test_panel.py` enforces this).
- Every new code path gets pytest coverage; `ruff check` + `ruff format` clean
  (py312, line 88, rules E,F,I,UP,B,RUF100; B905 → always pass `strict=` to
  `zip`); `node --check` on every panel script; `python -m compileall`.
- Tooling in the sandbox: create `.venv-test` (already gitignored) with
  `requirements_test.txt`; global pip is PEP 668 locked. CI matrix: py3.11/3.12
  × pymodbus 3.6.9/3.15.0 — **anything you add must work on pymodbus 3.6.x**
  (no `FramerType` there; use `ModbusRtuFramer` fallback) and 3.15.
- `CHANGELOG.md`: add `## 2.8.0` under `## Unreleased` in the existing style
  (`### <Area>` headings, bullet lists, `### Development` last).
  `wiki/USER_GUIDE.md`: update setup chapter, Diagnostics table, Traffic
  Inspector "capture support" paragraph. Rename
  `tests/test_new_services.py::test_manifest_version_is_271` → `_280`.
- Deliver as a PR against `main` with a full description; wait for CI green.

## Where the current code assumes a serial port (all must become transport-aware)

| Location | Today | Change |
|---|---|---|
| `__init__.py::async_setup_entry` L107-120 `_build_client()` | `ModbusSerialClient(port, baudrate, …, timeout=3)` | call `transport.build_client(entry.data)` |
| `__init__.py` L134-140 `serial_config={…}` | 5 serial keys | pass the whole `connection` dict (see data model) |
| `coordinator.py::reconfigure_serial` L431-450 | rebuilds `ModbusSerialClient` | rebuild via `transport.build_client` |
| `coordinator.py::_connect_with_retries` L401-429 | resolves `/dev/serial/by-id` paths | only for `transport == "serial"`; TCP/API skip path resolution |
| `coordinator.py::scan_bus` L518-555 | probe `ModbusSerialClient` per baud/parity | serial: unchanged; `esphome_tcp`: one probe client (baud/parity are fixed on the ESP side — scan slave IDs only and report `baudrate: null`); `esphome_api`: use the API client's `set_serial(baud, parity)` service if the YAML exposes it, else slave-only scan |
| `coordinator.py::get_diagnostics` L947-955 `"serial": {…}` | port/baud/… | add `"transport"`, `"host"`, `"port"`, `"esphome_device"`; keep old keys (panel + tests read them) |
| `coordinator.py::_ensure_connected` | `UpdateFailed("Could not open the configured serial port")` | message per transport |
| `config_flow.py::_hub_schema` + `async_step_user` | one form with serial fields, unique_id `f"{port}_{slave_id}"` | menu/first step chooses transport, then a transport-specific form; unique_id `serial:<port>`, `esphome_tcp:<host>:<tcp_port>`, `esphome_api:<host>` |
| `api/hub.py::ws_save_hub` L126-160 | copies 5 serial keys | copy transport keys too; validate host/port |
| `api/hub.py::ws_get_serial_status` | matches USB adapter by port | for ESPHome transports return `adapter: null` + `esphome: {...}` (reachability probe: TCP connect or `aioesphomeapi` `device_info()`) |
| `api/helpers.py::_format_entry_data` `hub` dict | serial keys | add `transport`, `host`, `tcp_port`, `api_password`/`encryption_key` **never echoed** (mask) |
| `www/panel/hub.js` `renderHubTab`, `openEditHubModal`, `submitSaveHub` | port/baud form | transport selector; show/hide serial vs ESPHome field groups; "Test connection" button |
| `www/modbus-panel.html` `#modal-edit-hub` L1260-1320 | serial inputs | add `#hub-form-transport`, `#hub-form-host`, `#hub-form-tcp-port`, `#hub-form-api-key`, `#hub-esphome-fields`, `#hub-serial-fields` |
| `capture.py::install_response_capture` | hooks `client.transaction/ctx.trace_packet` | works unchanged for `ModbusTcpClient`; the API shim must expose `trace_packet` attribute and call it with `(sending, bytes)` so the inspector keeps real RX bytes |
| `strings.json` + `translations/en.json` | `user` step texts | new steps/fields/errors |
| `config_flow.py::async_step_capture_info` | pymodbus hook status | append transport line |

`bus.py::call_modbus_on_client` (slave/device_id keyword shim) must keep
working for the API client shim — give the shim methods a `slave` parameter.

## Data model (config entry `data`)

```python
CONF_TRANSPORT = "transport"            # "serial" | "esphome_tcp" | "esphome_api"
TRANSPORT_SERIAL = "serial"
TRANSPORT_ESPHOME_TCP = "esphome_tcp"
TRANSPORT_ESPHOME_API = "esphome_api"
CONF_HOST = "host"                      # hostname / IP / mDNS name
CONF_TCP_PORT = "tcp_port"              # default 8899 (stream server)
CONF_API_PORT = "api_port"              # default 6053
CONF_API_ENCRYPTION_KEY = "api_encryption_key"   # ESPHome noise PSK, optional
CONF_API_PASSWORD = "api_password"      # legacy, optional
CONF_ESPHOME_SERVICE = "esphome_service"  # default "modbus_send"
CONF_ESPHOME_EVENT = "esphome_event"      # default "esphome.modbus_rx"
CONF_RESPONSE_TIMEOUT = "response_timeout"  # seconds, default 1.5 (api), 3 (tcp)
```

Migration: entries without `transport` are `serial` — add
`async_migrate_entry` (bump `ConfigFlow.VERSION` to 2) that writes
`transport: "serial"` so nothing else needs `.get()` defaults. Baud/parity/
bytesize/stopbits stay in `data` for all transports (they describe the RS-485
side and are shown in the UI; for ESPHome transports they must match the
`uart:` block and are informational unless the API `set_serial` service
exists).

## Backend design

### `custom_components/modbus_usb/transport.py` (pure, HA-free)

```python
def transport_of(config: Mapping[str, Any]) -> str            # default "serial"
def build_client(config: Mapping[str, Any], *, timeout: float | None = None,
                 retries: int | None = None) -> Any
    # serial      -> ModbusSerialClient(...)
    # esphome_tcp -> ModbusTcpClient(host, port=tcp_port, framer=<RTU framer>,
    #                timeout=..., retries=...)  with pymodbus 3.6 fallback:
    #                try: from pymodbus.framer import FramerType; framer=FramerType.RTU
    #                except ImportError: from pymodbus.transaction import ModbusRtuFramer; framer=ModbusRtuFramer
    # esphome_api -> EsphomeApiModbusClient(...)   (see below)
def describe_transport(config) -> dict     # for diagnostics/panel: label, endpoint, capture_support
def unique_id_for(config) -> str
def connection_error_message(config) -> str
```

Unit-test `build_client` with `unittest.mock.patch` on the pymodbus classes
(assert framer kwarg, host/port, timeout) — do not open sockets.

### `custom_components/modbus_usb/esphome_bridge.py` (pure framing + pairing)

- `build_rtu_request(slave, function_code, address, count_or_value, values=None) -> bytes`
  (reuse `diagnostics.modbus_crc16`; support FC01–06, 0F, 10).
- `parse_rtu_response(frame: bytes, expected_slave, expected_fc) -> ModbusResponseLike`
  → a small dataclass with `.registers`, `.bits`, `.isError()`, `.exception_code`,
  `.function_code`, `.address`, `.value`, matching what `coordinator.py`,
  `boards/*.py` and `inspector.py` read from pymodbus results (`.registers`,
  `.bits`, `.isError()`, `exception_code` are the only attributes used today —
  verified with grep). CRC mismatch → raise `ModbusIOException`-compatible
  error.
- `FramePairer`: thread-safe queue keyed by (slave, fc) with timeout; drops
  stale/unsolicited frames; bounded (reuse the `MAX_RESPONSE_FRAMES` idea).
- No `aioesphomeapi` import here → fully unit-testable.

### `EsphomeApiModbusClient` (in `esphome_api_client.py`, HA-aware allowed)

A sync façade with the pymodbus surface the coordinator uses:
`connect() -> bool`, `close()`, `connected` property, `read_coils`,
`read_discrete_inputs`, `read_holding_registers`, `read_input_registers`,
`write_coil`, `write_register`, `write_coils`, `write_registers` (all accept
`slave=`), `timeout`, `retries`, `trace_packet` attribute (callable
`(sending: bool, data: bytes) -> bytes`, invoked for TX and each RX frame so
`capture.py` sees real bytes → `capture_hook = "trace_packet"`).

Implementation: it owns an `aioesphomeapi.APIClient` running on the HA event
loop; sync methods are called from the serial executor thread and use
`asyncio.run_coroutine_threadsafe(...).result(timeout)`. Flow per request:
1. `frame = build_rtu_request(...)`; `trace_packet(True, frame)`.
2. `await api.execute_service(service, {"data": list(frame)})` (service found
   via `list_entities_services()`; cache; re-resolve on reconnect).
3. Wait on the pairer future fed by `subscribe_home_assistant_states`? No —
   ESPHome events are delivered as HA events on the bus
   (`homeassistant.fire_event` in YAML). So subscribe with
   `hass.bus.async_listen(CONF_ESPHOME_EVENT, handler)` filtered by
   `device_id`/`device_name`; the handler decodes `data.frame` (hex string),
   calls `trace_packet(False, bytes)`, and resolves the pairer.
4. Parse, return response-like object; on timeout raise
   `pymodbus.exceptions.ModbusIOException("No response received")` (the
   scanner relies on the "no response received" substring — keep it).

Keep the RS-485 semantics: strictly one in-flight request (the coordinator's
`_serial_lock` already guarantees this; add an internal lock anyway).

### Coordinator changes

- `serial_config` → keep the attribute name (tests/APIs read it) but store the
  full connection dict incl. `transport`.
- `reconfigure_serial` → rename internally to `reconfigure_connection`, keep
  `reconfigure_serial` as an alias (callers: `api/hub.py`, tests).
- Path resolution and USB-specific log wording gated on
  `transport_of(self.serial_config) == "serial"`.
- `scan_bus` behaviour per transport as in the table above; the result dict
  gains `"transport"` and, for ESPHome, `"baudrate_fixed": true`.
- `get_diagnostics()["serial"]` adds `transport`, `endpoint`
  (`/dev/ttyUSB0` | `host:8899` | `host (ESPHome API)`), `capture_support`.

### Config flow

- `async_step_user` → transport chooser (`vol.In` with labels via strings),
  then `async_step_serial` (existing form), `async_step_esphome_tcp`
  (name, host, tcp_port=8899, baud/parity/bytesize/stopbits for display,
  slave_id, scan_interval), `async_step_esphome_api` (name, host, api_port,
  encryption key (password selector), service/event names, response_timeout,
  slave_id, scan_interval).
- Validate reachability in the flow (executor): TCP → `socket.create_connection`
  with 3 s timeout; API → `aioesphomeapi.APIClient.connect()` + `device_info()`
  and assert the `modbus_send` service exists (error keys:
  `cannot_connect`, `invalid_auth`, `service_missing`). Show the ESPHome
  device name/MAC in the success title.
- Optional: ESPHome **zeroconf discovery** (`_esphomelib._tcp.local.`) →
  `async_step_zeroconf` pre-filling host; add `"zeroconf": ["_esphomelib._tcp.local."]`
  to the manifest only if implemented and tested.
- Options flow `settings` step unchanged; `capture_info` appends the transport
  line.

### Manifest

```json
"requirements": ["pymodbus>=3.6.0,<4.0.0", "pyserial==3.5", "aioesphomeapi>=24.0.0"]
```
Pin to a version range that installs on the CI Python matrix; `aioesphomeapi`
is already shipped by HA core's `esphome` integration, so version-align with
the current HA release. Keep the import lazy (inside the API client) so the
serial and TCP transports never import it. Add `"after_dependencies": ["esphome"]`
is **not** needed (we do not use the esphome integration's entry).

## Panel (www/panel)

- `hub.js`: `renderHubTab` shows a **Transport** card (`Serial (USB)` /
  `ESPHome · RTU over TCP` / `ESPHome · native API`) and endpoint; for
  ESPHome transports the "Serial Port" card becomes "ESPHome endpoint" and a
  **📶 Test connection** button calls new read-only WS
  `modbus_usb/test_hub_connection` (returns `{reachable, latency_ms,
  esphome: {name, version, mac}}`).
- Edit-hub modal: `#hub-form-transport` select toggles `#hub-serial-fields`
  vs `#hub-esphome-fields` (`hub-form-host`, `hub-form-tcp-port`,
  `hub-form-api-port`, `hub-form-api-key` (type=password, never prefilled;
  blank = keep), `hub-form-service`, `hub-form-event`, `hub-form-timeout`).
  `submitSaveHub` sends only the fields of the active transport.
- `diagnostics.js` scanner card: hide baud/parity checkboxes when
  `hub.transport !== 'serial'` and show "Baud rate is fixed by the ESPHome
  `uart:` block" note; reads `result.baudrate_fixed`.
- `core.js` standalone mock: add one `esphome_tcp` entry so the preview shows
  the new cards.
- CSS: `.transport-badge`, field-group toggling — no inline `<style>`.

## Example ESPHome YAML (ship at `custom_components/modbus_usb/esphome/` and link from the wiki)

### `esphome/modbus_bridge_esp32.yaml` — TCP stream bridge (primary)

```yaml
# ESP32 + MAX485 (or auto-direction RS-485 module) as a transparent
# Modbus RTU ↔ TCP bridge for Home Assistant "Modbus USB Controller".
# HA hub settings: transport = ESPHome · RTU over TCP, host = <this device>,
# tcp_port = 8899. Baud/parity below MUST match the HA hub form.
substitutions:
  name: modbus-bridge
  friendly_name: Modbus RS-485 Bridge
  uart_tx: GPIO17
  uart_rx: GPIO16
  uart_baud: "9600"
  uart_parity: "NONE"      # NONE | EVEN | ODD
  uart_stop_bits: "1"
  flow_control_pin: GPIO4  # remove the line + `flow_control_pin` below for auto-direction modules
  tcp_port: "8899"

esphome:
  name: ${name}
  friendly_name: ${friendly_name}

esp32:
  board: esp32dev
  framework:
    type: esp-idf

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  power_save_mode: none          # latency matters for Modbus timeouts
  ap:
    ssid: "${name} Fallback"

api:
  encryption:
    key: !secret api_encryption_key
ota:
  - platform: esphome
logger:
  baud_rate: 0                   # keep UART0 free / avoid log jitter
  level: INFO

external_components:
  - source: github://oxan/esphome-stream-server
    components: [stream_server]

uart:
  id: modbus_uart
  tx_pin: ${uart_tx}
  rx_pin: ${uart_rx}
  baud_rate: ${uart_baud}
  parity: ${uart_parity}
  stop_bits: ${uart_stop_bits}
  data_bits: 8
  rx_buffer_size: 512

stream_server:
  uart_id: modbus_uart
  port: ${tcp_port}
  buffer_size: 512
  # flow_control_pin: ${flow_control_pin}   # uncomment for DE/RE-driven MAX485 boards

binary_sensor:
  - platform: stream_server
    connected:
      name: "${friendly_name} Client Connected"
sensor:
  - platform: stream_server
    connection_count:
      name: "${friendly_name} Connections"
  - platform: wifi_signal
    name: "${friendly_name} WiFi Signal"
    update_interval: 60s
button:
  - platform: restart
    name: "${friendly_name} Restart"
```

Document: one TCP client at a time (HA owns the bridge; do not point a second
tool at it), `power_save_mode: none`, static IP/DHCP reservation recommended,
`flow_control_pin` semantics, and that the HA-side inter-frame delay option
still applies.

### `esphome/modbus_api_bridge_esp32.yaml` — native API fallback

Same `esphome/esp32/wifi/api/ota/logger/uart` blocks, no external component,
plus:

```yaml
globals:
  - id: rx_buf
    type: std::vector<uint8_t>
  - id: last_rx_ms
    type: uint32_t
    initial_value: "0"

api:
  encryption:
    key: !secret api_encryption_key
  services:
    # Called by HA: data = raw RTU frame bytes incl. CRC.
    - service: modbus_send
      variables:
        data: int[]
      then:
        - lambda: |-
            id(rx_buf).clear();
            std::vector<uint8_t> out(data.begin(), data.end());
            id(modbus_uart).write_array(out);
            id(modbus_uart).flush();
    # Optional: let HA's RS-485 scanner switch baud/parity at runtime.
    - service: set_serial
      variables:
        baud: int
        parity: string
      then:
        - lambda: |-
            id(modbus_uart).set_baud_rate(baud);
            id(modbus_uart).set_parity(parity == "E" ? esphome::uart::UART_CONFIG_PARITY_EVEN
                                     : parity == "O" ? esphome::uart::UART_CONFIG_PARITY_ODD
                                     : esphome::uart::UART_CONFIG_PARITY_NONE);
            id(modbus_uart).load_settings();

interval:
  - interval: 5ms          # RTU 3.5-char silence detector → one HA event per frame
    then:
      - lambda: |-
          while (id(modbus_uart).available()) {
            uint8_t b; id(modbus_uart).read_byte(&b);
            id(rx_buf).push_back(b);
            id(last_rx_ms) = millis();
          }
          if (!id(rx_buf).empty() && millis() - id(last_rx_ms) >= 4) {
            std::string hex;
            char tmp[4];
            for (auto b : id(rx_buf)) { snprintf(tmp, sizeof(tmp), "%02X ", b); hex += tmp; }
            if (!hex.empty()) hex.pop_back();
            api::global_api_server->fire_homeassistant_event("esphome.modbus_rx",
              {{"frame", hex}, {"device", App.get_name()}});
            id(rx_buf).clear();
          }
```

Document the limits clearly: ~20–60 ms extra round trip, event payload size
(< 1 kB → frames up to 253 bytes are fine), one request in flight, silence
threshold 4 ms works ≥ 9600 baud (use ≥ 8 ms for 2400/4800), and that the HA
event name must match `esphome_event` in the hub form.

## Tests to add

- `tests/test_transport.py`: `transport_of` defaults, `build_client` per
  transport (patched pymodbus classes; assert RTU framer on both pymodbus 3.6
  and 3.15 code paths by patching the import), `unique_id_for`,
  `describe_transport`, legacy entry without `transport`.
- `tests/test_esphome_bridge.py`: request builders for every FC (CRC checked
  against `modbus_crc16` vectors), response parsing (read/write/exception/CRC
  fail/short/slave mismatch), `FramePairer` timeout/stale/unsolicited/bounded,
  thread-safety smoke.
- `tests/test_esphome_api_client.py`: fake `APIClient` + fake event bus; TX/RX
  `trace_packet` calls, "No response received" on timeout, retries, reconnect,
  service missing → clear error.
- `tests/test_config_flow.py`: transport chooser → each form; unique_id per
  transport; `cannot_connect`/`service_missing`; **migration v1→v2** adds
  `transport: serial`; options `capture_info` shows transport.
- `tests/test_coordinator_diagnostics.py`: `get_diagnostics()["serial"]`
  transport keys; `scan_bus` on `esphome_tcp` reports `baudrate_fixed`;
  `_connect_with_retries` skips path resolution for TCP.
- `tests/test_ws_commands.py`: `save_hub` with transport fields (secrets not
  echoed), `test_hub_connection` read-only, `get_serial_status` for ESPHome.
- `tests/test_panel.py`: ids `#hub-form-transport`, `#hub-esphome-fields`,
  `#hub-serial-fields`, `#btn-test-hub-connection`; `hub.js` functions
  `renderTransportFields`, `testHubConnection`; mock entry in `core.js`.
- `tests/test_new_services.py`: manifest `2.8.0`, `aioesphomeapi` in
  requirements.
- Validate both example YAMLs in a test with a SafeLoader that registers a `!secret` constructor
  (`tests/test_esphome_examples.py`) — assert `uart`, `api.services[0].service
  == "modbus_send"`, `stream_server.port == "8899"`.

Run: `.venv-test/bin/ruff check custom_components tests && .venv-test/bin/ruff
format --check custom_components tests && for f in
custom_components/modbus_usb/www/panel/*.js; do node --check "$f"; done &&
.venv-test/bin/python -m pytest -q` (expect ≥ 400 tests).

## Docs

- `wiki/USER_GUIDE.md`: new section **"Using an ESPHome device as the hub"**
  (hardware, wiring table for MAX485 vs auto-direction modules, both YAML
  files, HA hub form screenshots-in-words, latency expectations, when to pick
  which transport, troubleshooting: "Client connected" sensor false → nothing
  bound; `No response received` → baud mismatch / A-B swapped / termination).
- `README.md`: feature bullet + link.
- `CHANGELOG.md` `## 2.8.0`: `### Hub transports`, `### ESPHome examples`,
  `### Config flow`, `### Panel`, `### Development`.

## Acceptance criteria

- Existing serial entries load unchanged after migration; all 357 current tests
  still pass.
- `esphome_tcp` hub: polls entities, writes, scanner (slave-only), board
  tools, inspector shows real RX frames (`capture_hook == "trace_packet"`),
  capture coverage ≥ 90 % on a healthy bus.
- `esphome_api` hub: polls/writes work; inspector shows real RX frames via the
  shim's `trace_packet`; scanner works slave-only (baud switching only when
  `set_serial` service is present).
- Secrets (encryption key/password) never appear in `get_data`,
  diagnostics dumps, logs, or the activity-log export.
- CI green on the full matrix.

## Next updates after v2.8.0 (backlog, in priority order)

1. **v2.8.1 — Discovery & UX**: zeroconf discovery of ESPHome bridges
   (`_esphomelib._tcp.local.`), one-click "Adopt as hub"; hub tab shows the
   ESPHome device's WiFi RSSI / uptime pulled from its API; reconnect
   backoff telemetry in diagnostics.
2. **v2.8.2 — Generic Modbus-TCP/RTU-over-TCP hubs**: since the TCP path is
   pymodbus, expose `transport = "tcp"` (Modbus TCP framing, gateways like
   Waveshare/USR/Elfin) and `"rtu_over_tcp"` (any serial-to-Ethernet server),
   reusing the ESPHome TCP code path; per-transport default timeouts.
3. **v2.9.0 — Multi-hub bus sharing & health**: bridge liveness sensor
   entities on the hub device (connected, latency p95, reconnects), an
   `unavailable` state on all child entities while the bridge is down,
   repair issues (`homeassistant.helpers.issue_registry`) for "bridge
   unreachable" / "baud mismatch suspected" (derived from CRC-fail ratio in
   the inspector stats).
4. **v2.9.x — ESPHome-side improvements**: publish a maintained
   `esphome/packages/modbus_bridge.yaml` package (`packages:` include with
   substitutions), ESP8266 and ESP32-C3/S3 variants, optional RS-485
   termination/bias notes, OTA-safe UART pin table per board.
5. **v2.10.0 — Inspector for remote bridges**: per-frame timing already
   exists (v2.7.1); add a "transport latency" waterfall stage (socket RTT vs
   bus time) for TCP/API hubs, and export it in CSV/JSON.
6. **Tech debt**: rename `serial_config`/`reconfigure_serial`/`_serial_lock`
   to connection-neutral names once the alias period is over (v3.0);
   consolidate client construction in `transport.py` for the scanner probes;
   type the response-like object shared by pymodbus and the API shim in
   `models.py`.
