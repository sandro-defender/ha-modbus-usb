/* state.js — Shared mutable panel state. Must load before every other panel script.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    const DOMAIN = 'modbus_usb';
    let _hass = null;
    let _isLiveHA = false;

    // State data
    let _entries = [];
    let _currentEntryId = null;
    let _templates = [];
    let _statesCache = {};
    let _unsubscribeStateChanges = null;
    let _liveStateRenderTimer = null;

    // ─── TRAFFIC INSPECTOR LIVE STREAM ──────────────────────────
    let _inspectorUnsubscribe = null;      // active WS subscription disposer
    let _inspectorStreamEntryId = null;    // entry the stream is bound to
    let _inspectorPaused = false;          // UI pause switch
    let _inspectorBuffered = 0;            // pushes skipped while paused
    let _inspectorLive = false;            // stream connected & rendering

    // ─── TRAFFIC INSPECTOR FILTERS (v2.7.0) ─────────────────────
    // Client-side filters over the live transaction list. Kept in shared
    // state so they survive tab switches and hub switches: the filter
    // controls are static DOM nodes (never re-rendered) and the values
    // below persist in module scope. v2.7.1 adds the function-code filter
    // (`fc`: 'all', a two-digit hex code such as '03' / '0F' / '10', or
    // 'exception').
    const INSPECTOR_DEFAULT_FILTERS = Object.freeze({ slave: 'all', status: 'all', fc: 'all', hex: '' });
    let _inspectorFilters = { ...INSPECTOR_DEFAULT_FILTERS };

    // ─── TRAFFIC INSPECTOR FILTER SAVED VIEWS (v2.7.1) ──────────
    // Named filter presets persisted in localStorage. Each preset is
    // { name, filters: { slave, status, fc, hex } }. The active preset name
    // is persisted too and re-applied whenever the inspector tab opens, so a
    // saved view survives page reloads; editing any filter by hand
    // deselects the preset (the filters themselves stay as edited).
    const INSPECTOR_PRESETS_STORAGE_KEY = 'modbus_usb_inspector_filter_presets';
    const INSPECTOR_ACTIVE_PRESET_STORAGE_KEY = 'modbus_usb_inspector_active_preset';
    const INSPECTOR_MAX_PRESETS = 24;
    let _inspectorFilterPresets = [];
    let _inspectorActivePreset = null;
    try {
      const stored = JSON.parse(localStorage.getItem(INSPECTOR_PRESETS_STORAGE_KEY) || '[]');
      if (Array.isArray(stored)) {
        stored.forEach((preset) => {
          if (!preset || typeof preset.name !== 'string' || !preset.name.trim()) return;
          if (_inspectorFilterPresets.length >= INSPECTOR_MAX_PRESETS) return;
          const filters = preset.filters && typeof preset.filters === 'object' ? preset.filters : {};
          _inspectorFilterPresets.push({
            name: preset.name.trim().slice(0, 48),
            filters: {
              slave: filters.slave == null || filters.slave === '' ? 'all' : String(filters.slave),
              status: typeof filters.status === 'string' && filters.status ? filters.status : 'all',
              fc: typeof filters.fc === 'string' && filters.fc ? filters.fc : 'all',
              hex: typeof filters.hex === 'string' ? filters.hex : '',
            },
          });
        });
      }
      const active = localStorage.getItem(INSPECTOR_ACTIVE_PRESET_STORAGE_KEY);
      if (active && _inspectorFilterPresets.some((preset) => preset.name === active)) {
        _inspectorActivePreset = active;
      }
    } catch (_) { /* Missing or malformed storage must not block the inspector. */ }

    function persistInspectorFilterPresets() {
      try {
        localStorage.setItem(INSPECTOR_PRESETS_STORAGE_KEY, JSON.stringify(_inspectorFilterPresets));
        if (_inspectorActivePreset) {
          localStorage.setItem(INSPECTOR_ACTIVE_PRESET_STORAGE_KEY, _inspectorActivePreset);
        } else {
          localStorage.removeItem(INSPECTOR_ACTIVE_PRESET_STORAGE_KEY);
        }
      } catch (_) { /* Presets still work for this session without storage. */ }
    }
    let _dashboardFilter = 'all';
    const _deviceTestReports = {};
    const _r413e16StateReports = {};
    let _serialStatusEntryId = null;
    const _expandedDiagnosticCards = new Set();
    try {
      JSON.parse(localStorage.getItem('modbus_usb_expanded_diagnostics') || '[]').forEach(key => _expandedDiagnosticCards.add(key));
    } catch (_) { /* A missing or malformed preference must not block Diagnostics. */ }
    const _expandedDeviceCards = new Set();
    try {
      JSON.parse(localStorage.getItem('modbus_usb_expanded_devices') || '[]').forEach(id => _expandedDeviceCards.add(id));
    } catch (_) { /* Ignore unavailable or malformed browser storage. */ }
    let _inputWatchActive = false;
    let _inputWatchTimer = null;
    const _inputWatchPrevious = new Map();
    let _automaticDiscoveryActive = false;
    let _discoveredTarget = null;

    // Standalone fallback mock data for testing/preview outside Home Assistant
    const MOCK_DATA = {
      entries: [
        {
          entry_id: 'mock_hub_1',
          title: 'Modbus USB Hub (ttyUSB0)',
          hub: {
            transport: 'serial',
            transport_label: 'Serial (USB adapter)',
            endpoint: '/dev/ttyUSB0',
            baudrate_fixed: false,
            port: '/dev/ttyUSB0',
            baudrate: 9600,
            bytesize: 8,
            parity: 'N',
            stopbits: 1,
            slave_id: 1,
            scan_interval: 5,
          },
          devices: [
            {
              id: 'dev_sdm120_main',
              name: 'Main Board SDM120 Energy Meter',
              slave_id: 1,
              model: 'SDM120',
              manufacturer: 'Eastron',
              description: 'Measures whole home AC power and cumulative energy',
            },
            {
              id: 'dev_climate_server',
              name: 'Server Room XY-MD02',
              slave_id: 2,
              model: 'XY-MD02',
              manufacturer: 'SHT20',
              description: 'Monitors temperature and humidity in rack',
            },
            {
              id: 'dev_relays_pool',
              name: '8-Channel Pool Relay Controller',
              slave_id: 3,
              model: 'Modbus RTU Relay 8-CH',
              manufacturer: 'Waveshare',
              description: 'Controls filter pump, heater, lighting relays',
            }
          ],
          entities: [
            { id: 'e1', device_id: 'dev_sdm120_main', name: 'Voltage', entity_type: 'sensor', register_type: 'input', address: 0, data_type: 'float32', scale: 1, unit_of_measurement: 'V', device_class: 'voltage' },
            { id: 'e2', device_id: 'dev_sdm120_main', name: 'Current', entity_type: 'sensor', register_type: 'input', address: 6, data_type: 'float32', scale: 1, unit_of_measurement: 'A', device_class: 'current' },
            { id: 'e3', device_id: 'dev_sdm120_main', name: 'Active Power', entity_type: 'sensor', register_type: 'input', address: 12, data_type: 'float32', scale: 1, unit_of_measurement: 'W', device_class: 'power' },
            { id: 'e4', device_id: 'dev_sdm120_main', name: 'Total Energy', entity_type: 'sensor', register_type: 'input', address: 342, data_type: 'float32', scale: 1, unit_of_measurement: 'kWh', device_class: 'energy' },
            { id: 'e5', device_id: 'dev_climate_server', name: 'Temperature', entity_type: 'sensor', register_type: 'input', address: 1, data_type: 'int16', scale: 0.1, unit_of_measurement: '°C', device_class: 'temperature' },
            { id: 'e6', device_id: 'dev_climate_server', name: 'Humidity', entity_type: 'sensor', register_type: 'input', address: 2, data_type: 'uint16', scale: 0.1, unit_of_measurement: '%', device_class: 'humidity' },
            { id: 'e7', device_id: 'dev_relays_pool', name: 'Filter Pump Relay', entity_type: 'switch', register_type: 'coil', address: 0 },
            { id: 'e8', device_id: 'dev_relays_pool', name: 'Underwater Light Relay', entity_type: 'switch', register_type: 'coil', address: 1 },
            { id: 'e9', device_id: 'dev_relays_pool', name: 'Flow Switch Status', entity_type: 'binary_sensor', register_type: 'discrete', address: 0 },
            { id: 'e10', device_id: 'dev_relays_pool', name: 'Pump Speed Setpoint', entity_type: 'number', register_type: 'holding', address: 10, data_type: 'uint16', scale: 1, min_value: 0, max_value: 100, step: 5, unit_of_measurement: '%' }
          ]
        },
        {
          // v2.8.0: an ESPHome ESP32 + RS-485 module acting as the hub
          // (RTU over TCP through the stream_server component).
          entry_id: 'mock_hub_esphome',
          title: 'Garage Modbus via ESPHome (modbus-bridge)',
          hub: {
            transport: 'esphome_tcp',
            transport_label: 'ESPHome · RTU over TCP',
            endpoint: 'modbus-bridge.local:8899',
            baudrate_fixed: true,
            host: 'modbus-bridge.local',
            tcp_port: 8899,
            response_timeout: 3,
            port: null,
            baudrate: 9600,
            bytesize: 8,
            parity: 'N',
            stopbits: 1,
            slave_id: 1,
            scan_interval: 10,
          },
          devices: [
            { id: 'dev_garage_relays', name: 'Garage Relay Board', slave_id: 1, model: 'R4D3B16', manufacturer: 'Generic', description: 'Door and light relays behind the ESPHome bridge' },
          ],
          entities: [
            { id: 'g1', device_id: 'dev_garage_relays', name: 'Garage Door Relay', entity_type: 'switch', register_type: 'coil', address: 0 },
            { id: 'g2', device_id: 'dev_garage_relays', name: 'Garage Light', entity_type: 'switch', register_type: 'coil', address: 1 },
          ]
        }
      ],
      templates: [
        {
          id: 'sdm120',
          filename: 'sdm120.yaml',
          name: 'Eastron SDM120 Energy Meter',
          manufacturer: 'Eastron',
          model: 'SDM120',
          default_slave_id: 1,
          description: 'Single-phase DIN rail energy meter with Modbus RTU communication',
          entities: [
            { name: 'Voltage', entity_type: 'sensor', register_type: 'input', address: 0, data_type: 'float32', unit_of_measurement: 'V', device_class: 'voltage' },
            { name: 'Current', entity_type: 'sensor', register_type: 'input', address: 6, data_type: 'float32', unit_of_measurement: 'A', device_class: 'current' },
            { name: 'Active Power', entity_type: 'sensor', register_type: 'input', address: 12, data_type: 'float32', unit_of_measurement: 'W', device_class: 'power' },
            { name: 'Frequency', entity_type: 'sensor', register_type: 'input', address: 70, data_type: 'float32', unit_of_measurement: 'Hz', device_class: 'frequency' },
            { name: 'Total Active Energy', entity_type: 'sensor', register_type: 'input', address: 342, data_type: 'float32', unit_of_measurement: 'kWh', device_class: 'energy' }
          ],
          raw_yaml: `id: sdm120
name: Eastron SDM120 Energy Meter
manufacturer: Eastron
model: SDM120
default_slave_id: 1
description: Single-phase DIN rail energy meter with Modbus RTU communication
entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
    unit_of_measurement: "V"
    device_class: voltage
  - name: Current
    entity_type: sensor
    register_type: input
    address: 6
    data_type: float32
    unit_of_measurement: "A"
    device_class: current
  - name: Active Power
    entity_type: sensor
    register_type: input
    address: 12
    data_type: float32
    unit_of_measurement: "W"
    device_class: power
  - name: Frequency
    entity_type: sensor
    register_type: input
    address: 70
    data_type: float32
    unit_of_measurement: "Hz"
    device_class: frequency
  - name: Total Active Energy
    entity_type: sensor
    register_type: input
    address: 342
    data_type: float32
    unit_of_measurement: "kWh"
    device_class: energy`
        },
        {
          id: 'xy_md02',
          filename: 'xy_md02.yaml',
          name: 'XY-MD02 Temperature & Humidity',
          manufacturer: 'SHT20',
          model: 'XY-MD02',
          default_slave_id: 1,
          description: 'RS485 Modbus RTU temperature and humidity environmental sensor',
          entities: [
            { name: 'Temperature', entity_type: 'sensor', register_type: 'input', address: 1, data_type: 'int16', scale: 0.1, unit_of_measurement: '°C', device_class: 'temperature' },
            { name: 'Humidity', entity_type: 'sensor', register_type: 'input', address: 2, data_type: 'uint16', scale: 0.1, unit_of_measurement: '%', device_class: 'humidity' }
          ],
          raw_yaml: `id: xy_md02
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
  - name: Humidity
    entity_type: sensor
    register_type: input
    address: 2
    data_type: uint16
    scale: 0.1
    unit_of_measurement: "%"
    device_class: humidity`
        },
        {
          id: 'relay_8ch',
          filename: 'relay_8ch.yaml',
          name: '8-Channel Modbus Relay Board',
          manufacturer: 'Waveshare',
          model: 'Modbus RTU Relay 8-CH',
          default_slave_id: 1,
          description: '8-channel relay output module with digital inputs via Modbus RTU',
          entities: [
            { name: 'Relay 1', entity_type: 'switch', register_type: 'coil', address: 0 },
            { name: 'Relay 2', entity_type: 'switch', register_type: 'coil', address: 1 },
            { name: 'Relay 3', entity_type: 'switch', register_type: 'coil', address: 2 },
            { name: 'Relay 4', entity_type: 'switch', register_type: 'coil', address: 3 },
            { name: 'Relay 5', entity_type: 'switch', register_type: 'coil', address: 4 },
            { name: 'Relay 6', entity_type: 'switch', register_type: 'coil', address: 5 },
            { name: 'Relay 7', entity_type: 'switch', register_type: 'coil', address: 6 },
            { name: 'Relay 8', entity_type: 'switch', register_type: 'coil', address: 7 },
            { name: 'Digital Input 1', entity_type: 'binary_sensor', register_type: 'discrete', address: 0 },
            { name: 'Digital Input 2', entity_type: 'binary_sensor', register_type: 'discrete', address: 1 }
          ],
          raw_yaml: `id: relay_8ch
name: 8-Channel Modbus Relay Board
manufacturer: Waveshare
model: Modbus RTU Relay 8-CH
default_slave_id: 1
description: 8-channel relay output module with digital inputs via Modbus RTU
entities:
  - name: Relay 1
    entity_type: switch
    register_type: coil
    address: 0
  - name: Relay 2
    entity_type: switch
    register_type: coil
    address: 1
  - name: Relay 3
    entity_type: switch
    register_type: coil
    address: 2
  - name: Relay 4
    entity_type: switch
    register_type: coil
    address: 3
  - name: Relay 5
    entity_type: switch
    register_type: coil
    address: 4
  - name: Relay 6
    entity_type: switch
    register_type: coil
    address: 5
  - name: Relay 7
    entity_type: switch
    register_type: coil
    address: 6
  - name: Relay 8
    entity_type: switch
    register_type: coil
    address: 7
  - name: Digital Input 1
    entity_type: binary_sensor
    register_type: discrete
    address: 0
  - name: Digital Input 2
    entity_type: binary_sensor
    register_type: discrete
    address: 1`
        }
      ]
    };

    let _availableUpdate = null;
    let _restartPending = false;
    const HACS_REPOSITORY = 'sandro-defender/ha-modbus-usb';

    // ─── ADD DEVICE MODAL FLOW ──────────────────────────────────
    let _addDeviceMode = 'template';
    let _selectedTemplateEntities = new Set();

    // ─── APPLY TEMPLATE TO EXISTING DEVICE MODAL ────────────────
    let _selectedApplyEntities = new Set();
