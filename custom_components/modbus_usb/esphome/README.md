# ESPHome RS-485 bridge firmware

Ready-to-flash ESPHome configurations that turn an **ESP32 + RS-485 module**
into the Modbus hub for the *Modbus USB Controller* integration (v2.8.0+).

| File | Transport in the hub form | Extra ESPHome component | Notes |
|---|---|---|---|
| `modbus_bridge_esp32.yaml` | **ESPHome · RTU over TCP** | `oxan/esphome-stream-server` | Recommended. Lowest latency; every integration feature works unchanged. |
| `modbus_api_bridge_esp32.yaml` | **ESPHome · native API** | none | Fallback when you cannot use external components. +20–60 ms per request. |

Both files expect these `secrets.yaml` entries: `wifi_ssid`, `wifi_password`,
`api_encryption_key`, `ota_password`.

## Wiring (generic ESP32 dev board, UART2)

| ESP32 pin | RS-485 module (MAX485 / auto-direction) | Substitution |
|---|---|---|
| GPIO17 | DI (data in → TX) | `uart_tx` |
| GPIO16 | RO (data out → RX) | `uart_rx` |
| 3V3 / 5V, GND | VCC, GND (check your module's logic level) | — |
| A / B (module) | A / B of the RS-485 bus, plus common GND on long runs | — |

Auto-direction ("flow control free") modules need nothing else. Classic
MAX485 boards with DE/RE pins: tie DE and RE together to one GPIO (for
example GPIO4) and uncomment the `flow_control_pin` line in the YAML.

Only UART2 is used so UART0 stays free for USB flashing and logs.

**Baud rate, parity and stop bits live in the ESPHome `uart:` block** — the
values entered in Home Assistant must match them; the bridge cannot change
them on its own (the API variant exposes an optional `set_serial` service).

See the user guide chapter *"Using an ESPHome device as the hub"* for wiring,
troubleshooting and the limits of each transport.
