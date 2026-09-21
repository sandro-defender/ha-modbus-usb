/* core.js — Bootstrap, Home Assistant connection, API wrapper, and shared UI helpers.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── Bootstrap & HA Connection ──────────────────────────────
    function getHass() {
      try {
        if (window.parent && window.parent.document) {
          const el = window.parent.document.querySelector('home-assistant');
          if (el && el.hass) return el.hass;
        }
      } catch(e) {}
      return null;
    }

    async function init() {
      initializeDiagnosticCollapsibles();
      _hass = getHass();
      if (_hass) {
        _isLiveHA = true;
        const statusBadge = document.getElementById('status-badge');
        statusBadge.className = 'status-badge connected';
        document.getElementById('status-text').textContent = 'Connected (HA)';
      } else {
        _isLiveHA = false;
        const statusBadge = document.getElementById('status-badge');
        statusBadge.className = 'status-badge local';
        document.getElementById('status-text').textContent = 'Interactive Preview';
      }

      await refreshData();
      await subscribeToLiveStateChanges();
      setInterval(refreshData, 15000);
      setInterval(() => {
        if (document.getElementById('pane-diag')?.classList.contains('active')) refreshData();
      }, 5000);
    }

    function isConfiguredHaEntity(entityId) {
      return _entries.some((entry) => (entry.entities || []).some(
        (entity) => entity.ha_entity_id === entityId,
      ));
    }

    function scheduleLiveStateRender() {
      if (_liveStateRenderTimer) return;
      _liveStateRenderTimer = setTimeout(() => {
        _liveStateRenderTimer = null;
        renderDevicesTab();
        renderDashboardTab();
        renderEntitiesTab();
      }, 60);
    }

    async function subscribeToLiveStateChanges() {
      if (!_isLiveHA || !_hass?.connection?.subscribeEvents || _unsubscribeStateChanges) return;

      try {
        _unsubscribeStateChanges = await _hass.connection.subscribeEvents((event) => {
          const entityId = event?.data?.entity_id;
          if (!entityId || !isConfiguredHaEntity(entityId)) return;

          const newState = event.data.new_state;
          if (newState) {
            _statesCache = { ..._statesCache, [entityId]: newState };
          } else {
            const { [entityId]: _removedState, ...remainingStates } = _statesCache;
            _statesCache = remainingStates;
          }
          scheduleLiveStateRender();
        }, 'state_changed');
      } catch (error) {
        // The regular refresh remains a fallback if the embedded HA client
        // does not expose its event stream.
        console.debug('Could not subscribe to Home Assistant state changes:', error);
      }
    }

    // ─── Backend API Client ─────────────────────────────────────
    async function apiCall(type, payload = {}) {
      let backendError = null;
      if (_isLiveHA && _hass && _hass.callWS) {
        try {
          return await _hass.callWS({ type: `modbus_usb/${type}`, ...payload });
        } catch(e) {
          backendError = e;
          console.warn(`[Modbus API] WS ${type} failed, trying REST fallback:`, e);
        }
      }

      if (_isLiveHA && _hass && _hass.callApi) {
        try {
          if (type === 'get_data') return await _hass.callApi('GET', 'modbus_usb/config');
          if (type === 'get_templates') return await _hass.callApi('GET', 'modbus_usb/templates');
          if (type === 'save_template') return await _hass.callApi('POST', 'modbus_usb/templates', payload);
          if (type === 'delete_template') return await _hass.callApi('DELETE', `modbus_usb/templates?filename=${payload.filename}`);
        } catch(e) {
          backendError = e;
          console.warn(`[Modbus API] REST fallback failed:`, e);
        }
      }

      // Mock values are only for the standalone preview. Never fabricate a
      // successful serial response when Home Assistant is running live.
      if (_isLiveHA) throw backendError || new Error(`Modbus API '${type}' is unavailable`);

      // Standalone Mock fallback
      return handleMockCall(type, payload);
    }

    function handleMockCall(type, payload) {
      const entry = MOCK_DATA.entries.find(e => e.entry_id === (payload.entry_id || _currentEntryId)) || MOCK_DATA.entries[0];

      if (type === 'get_data') {
        return { entries: MOCK_DATA.entries, templates: MOCK_DATA.templates };
      }
      if (type === 'diagnostic_read') {
        return { value: 230.4 };
      }
      if (type === 'traffic_inspector') {
        // Standalone preview: a canned, pre-parsed inspector view. Live mode
        // parses frames server-side in custom_components/modbus_usb/inspector.py
        // and captures real RX bytes via custom_components/modbus_usb/capture.py.
        return {
          entry_id: entry.entry_id,
          connected: true,
          default_slave_id: 1,
          capture: { hook: 'trace_packet', supported: true },
          stats: { total: 3, errors: 1, responses: 3, capture_coverage: 1.0, samples: 3, avg_ms: 32.4, min_ms: 12.8, max_ms: 65.2, p95_ms: 65.2 },
          per_slave: {
            1: { count: 2, errors: 1, last_seen: new Date().toISOString(), samples: 2, avg_ms: 19.1, min_ms: 12.8, max_ms: 25.4, p95_ms: 25.4 },
            3: { count: 1, errors: 0, last_seen: new Date().toISOString(), samples: 1, avg_ms: 65.2, min_ms: 65.2, max_ms: 65.2, p95_ms: 65.2 },
          },
          transactions: [
            {
              // Batch-style transaction: the full RX stream (two frames) is
              // carried in response_frames; response_frame/response_hex hold
              // the last frame, exactly like the live server view.
              transaction: { timestamp: new Date().toISOString(), operation: 'read_input', slave: 1, address: 0, count: 2, status: 'ok', function_code: '0x04', request_hex: '01 04 00 00 00 02 71 CB', request_captured: true, response_hex: '01 03 04 00 01 00 02 2A 32', response_frames: ['01 04 04 43 66 66 66 A5 95', '01 03 04 00 01 00 02 2A 32'], response_frame_times_ms: [9.6, 24.1], duration_ms: 25.4, latency: { lock_wait_ms: 0.3, connect_ms: 0.1, frame_delay_ms: 0, request_ms: 25 }, error: null },
              frame: { raw_hex: '01 04 00 00 00 02 71 CB', frame_length: 8, direction: 'request', slave_id: 1, function_code: 4, function_name: 'Read Input Registers', frame_kind: 'read_request', address: 0, count: 2, crc_present: true, crc_low: 113, crc_high: 203, crc_received: 52081, crc_expected: 52081, crc_valid: true, errors: [], valid: true, summary: 'Read Input Registers · slave 1 · addr 0000 · count 2' },
              response_frame: { raw_hex: '01 03 04 00 01 00 02 2A 32', frame_length: 9, direction: 'response', slave_id: 1, function_code: 3, function_name: 'Read Holding Registers', frame_kind: 'read_response', byte_count: 4, data_hex: '00 01 00 02', values: [1, 2], crc_present: true, crc_low: 42, crc_high: 50, crc_received: 12842, crc_expected: 12842, crc_valid: true, errors: [], valid: true, summary: 'Read Holding Registers · slave 1 · 4 data bytes' },
              response_frames: [
                { raw_hex: '01 04 04 43 66 66 66 A5 95', frame_length: 9, direction: 'response', slave_id: 1, function_code: 4, function_name: 'Read Input Registers', frame_kind: 'read_response', byte_count: 4, data_hex: '43 66 66 66', values: [17254, 26214], crc_present: true, crc_low: 149, crc_high: 165, crc_received: 42389, crc_expected: 42389, crc_valid: true, errors: [], valid: true, summary: 'Read Input Registers · slave 1 · 4 data bytes', arrival_ms: 9.6, gap_ms: 9.6 },
                { raw_hex: '01 03 04 00 01 00 02 2A 32', frame_length: 9, direction: 'response', slave_id: 1, function_code: 3, function_name: 'Read Holding Registers', frame_kind: 'read_response', byte_count: 4, data_hex: '00 01 00 02', values: [1, 2], crc_present: true, crc_low: 42, crc_high: 50, crc_received: 12842, crc_expected: 12842, crc_valid: true, errors: [], valid: true, summary: 'Read Holding Registers · slave 1 · 4 data bytes', arrival_ms: 24.1, gap_ms: 14.5 },
              ],
            },
            {
              transaction: { timestamp: new Date().toISOString(), operation: 'write_holding', slave: 3, address: 128, count: 1, status: 'ok', function_code: '0x06', request_hex: '03 06 00 80 00 01 48 00', request_captured: true, response_hex: '03 06 00 80 00 01 48 00', duration_ms: 65.2, latency: { lock_wait_ms: 4.8, connect_ms: 0, frame_delay_ms: 10, request_ms: 50.4 }, error: null },
              frame: { raw_hex: '03 06 00 80 00 01 48 00', frame_length: 8, direction: 'request', slave_id: 3, function_code: 6, function_name: 'Write Single Register', frame_kind: 'write_frame', address: 128, value: 1, crc_present: true, crc_low: 72, crc_high: 0, crc_received: 72, crc_expected: 72, crc_valid: true, errors: [], valid: true, summary: 'Write Single Register · slave 3 · addr 0080 · value 0001' },
              response_frame: { raw_hex: '03 06 00 80 00 01 48 00', frame_length: 8, direction: 'response', slave_id: 3, function_code: 6, function_name: 'Write Single Register', frame_kind: 'write_frame', address: 128, value: 1, crc_present: true, crc_low: 72, crc_high: 0, crc_received: 72, crc_expected: 72, crc_valid: true, errors: [], valid: true, summary: 'Write Single Register · slave 3 · addr 0080 · value 0001' },
            },
            {
              transaction: { timestamp: new Date().toISOString(), operation: 'read_holding', slave: 1, address: 10, count: 2, status: 'error', function_code: '0x03', request_hex: '01 03 00 0A 00 01 A4 08', request_captured: true, response_hex: '01 83 02 C0 F1', duration_ms: 12.8, latency: { lock_wait_ms: 0.2, connect_ms: 0, frame_delay_ms: 0, request_ms: 12.6 }, error: 'ExceptionResponse: Illegal Data Address' },
              frame: { raw_hex: '01 03 00 0A 00 01 A4 08', frame_length: 8, direction: 'request', slave_id: 1, function_code: 3, function_name: 'Read Holding Registers', frame_kind: 'read_request', address: 10, count: 1, crc_present: true, crc_low: 164, crc_high: 8, crc_received: 2212, crc_expected: 2212, crc_valid: true, errors: [], valid: true, summary: 'Read Holding Registers · slave 1 · addr 000A · count 1' },
              response_frame: { raw_hex: '01 83 02 C0 F1', frame_length: 5, direction: 'response', slave_id: 1, function_code: 131, base_function_code: 3, function_name: 'Read Holding Registers', frame_kind: 'exception_response', exception_code: 2, exception_name: 'Illegal Data Address', crc_present: true, crc_low: 192, crc_high: 241, crc_received: 61888, crc_expected: 61888, crc_valid: true, errors: [], valid: true, summary: 'Exception 02 (Illegal Data Address) from slave 1 for Read Holding Registers' },
            },
          ],
        };
      }
      if (type === 'designer_validate') {
        // Standalone preview of v2.7.1 line-error highlighting: an entity
        // with an unknown entity_type reports the offending line, like
        // designer.py's TemplateDraftError does in live mode.
        const draftLines = String(payload.content || '').split('\n');
        const badLine = draftLines.findIndex((line) => /^\s*entity_type:\s*(?!(sensor|switch|binary_sensor|number)\s*$)\S/.test(line));
        if (badLine !== -1) {
          const value = draftLines[badLine].replace(/^\s*entity_type:\s*/, '').trim();
          const entityIndex = draftLines.slice(0, badLine + 1).filter((line) => /^\s*-\s*name:/.test(line)).length - 1;
          return {
            valid: false,
            error: `Unsupported entity type: '${value}'`,
            entities: [],
            error_line: badLine + 1,
            error_column: draftLines[badLine].indexOf(value) + 1,
            error_path: `entities[${Math.max(entityIndex, 0)}].entity_type`,
          };
        }
        return {
          valid: true,
          template: { name: 'My Custom Meter', id: null, default_slave_id: 1 },
          slave_id: payload.slave_id || 1,
          test_reads: payload.test_reads !== false,
          entity_count: 3, tested: 3, passed: 3, failed: 0, skipped: 0, truncated: false, all_passed: true,
          fingerprint: [], fingerprint_total: 0, fingerprint_matched: 0, fingerprint_all_matched: false,
          duration_ms: 214.6,
          entities: [
            { name: 'Voltage', address: 0, register_type: 'input', data_type: 'float32', word_count: 2, success: true, status: 'pass', raw_words: [17222, 26214], value: 230.4, scaled: false, decodings: { uint32: 1128529920, int32: 1128529920, float32: 230.4 } },
            { name: 'Temperature', address: 1, register_type: 'holding', data_type: 'int16', word_count: 1, success: true, status: 'pass', raw_words: [242], value: 24.2, scaled: true, decodings: { uint16: 242, int16: 242 } },
            { name: 'Relay 1', address: 0, register_type: 'coil', data_type: 'uint16', word_count: 1, success: true, status: 'pass', raw_words: [1], value: true, scaled: false, decodings: { bool: true } },
          ],
        };
      }
      if (type === 'probe_registers') {
        const types = payload.register_types || ['holding'];
        const names = { coil: ['0x01', 'Read Coils'], discrete: ['0x02', 'Read Discrete Inputs'], holding: ['0x03', 'Read Holding Registers'], input: ['0x04', 'Read Input Registers'] };
        const results = [];
        types.forEach(type => {
          for (let address = payload.start_address; address <= payload.end_address; address += 1) {
            const [code, name] = names[type];
            results.push({ register_type: type, function_code: code, function_name: name, address,
              request: `${Number(payload.slave_id).toString(16).padStart(2, '0').toUpperCase()} ${code.slice(2)} ${Math.floor(address / 256).toString(16).padStart(2, '0').toUpperCase()} ${(address % 256).toString(16).padStart(2, '0').toUpperCase()} 00 01 [CRC]`,
              status: 'response', value: type === 'holding' ? 0 : false,
              meaning: 'Simulated response — a real board may return a different value.' });
          }
        });
        return { results };
      }
      if (type === 'stop_probe_registers') return { stopping: true };
      if (type === 'scan_bus') {
        return { found: [{ slave_id: 1, baudrate: 9600, response: 'register response' }], probed: 20 };
      }
      if (type === 'test_hub_connection') {
        const hub = entry.hub || {};
        return hub.transport === 'esphome_tcp' || hub.transport === 'esphome_api'
          ? { reachable: true, latency_ms: 18.4, error: null, error_key: null, live: false, esphome: { name: 'modbus-bridge', esphome_version: '2025.9.0', mac_address: 'A4:CF:12:34:56:78' }, summary: { transport: hub.transport, label: hub.transport_label, endpoint: hub.endpoint } }
          : { reachable: true, latency_ms: 2.1, error: null, error_key: null, live: true, esphome: null, summary: { transport: 'serial', label: 'Serial (USB adapter)', endpoint: hub.port } };
      }
      if (type === 'scan_usb_ports') {
        return { ports: [{ port: '/dev/ttyUSB0', description: 'USB-RS485 Adapter', details: 'CH340 USB-Serial' }] };
      }
      if (type === 'manual_hex_write') return { slave_id: 1, function_code: '0x06', address: 128, count: 1 };
      if (type === 'get_serial_status') {
        if ((entry.hub || {}).transport === 'esphome_tcp') {
          return { serial: { port: null, baudrate: 9600, bytesize: 8, parity: 'N', stopbits: 1, connection_owner: 'Home Assistant Modbus USB', operation_active: false, transport: 'esphome_tcp', label: 'ESPHome · RTU over TCP', endpoint: 'modbus-bridge.local:8899', baudrate_fixed: true, capture_support: 'trace_packet' }, adapter: null, esphome: true, ownership: null, by_id_candidates: [], stable_path: null };
        }
        return { serial: { port: '/dev/ttyUSB0', baudrate: 9600, bytesize: 8, parity: 'N', stopbits: 1, connection_owner: 'Home Assistant Modbus USB', operation_active: false, transport: 'serial', label: 'Serial (USB adapter)', endpoint: '/dev/ttyUSB0', baudrate_fixed: false, state: 'ok', resolved_from: 'configured', reconnects_total: 1 }, adapter: { port: '/dev/ttyUSB0', description: 'USB-RS485 Adapter', details: 'CH340 USB-Serial' }, ownership: { reason: 'ok', hint: 'Port is open — Home Assistant is currently using it.', holders: [], probed: false }, by_id_candidates: ['/dev/serial/by-id/usb-FTDI_D2XX_P12345-0000'], stable_path: '/dev/serial/by-id/usb-FTDI_D2XX_P12345-0000' };
      }
      if (type === 'test_device_entities') {
        const device = entry.devices.find(d => d.id === payload.device_id);
        const deviceEntities = entry.entities.filter(e => e.device_id === payload.device_id);
        const switches = deviceEntities.filter(e => e.entity_type === 'switch');
        return { device_name: device?.name || 'Device', entity_count: deviceEntities.length,
          switch_count: switches.length, read_count: deviceEntities.length - switches.length,
          successful_steps: deviceEntities.length + switches.length, failed_steps: 0, duration_ms: deviceEntities.length * 500,
          results: deviceEntities.map(e => e.entity_type === 'switch'
            ? { name: e.name, address: e.address, register_type: e.register_type, entity_type: 'switch', on: { success: true, message: 'acknowledged' }, off: { success: true, message: 'acknowledged' } }
            : { name: e.name, address: e.address, register_type: e.register_type, entity_type: e.entity_type, read: { success: true, value: 'sample value' } }) };
      }
      if (type === 'verify_device_reads') {
        const deviceEntities = entry.entities.filter(e => e.device_id === payload.device_id);
        const checked = deviceEntities.filter(e => e.entity_type !== 'switch');
        return { device_name: entry.devices.find(d => d.id === payload.device_id)?.name || 'Device', checked: checked.length, passed: checked.length, failed: 0, skipped: deviceEntities.length - checked.length, duration_ms: checked.length * 120, results: deviceEntities.map(e => e.entity_type === 'switch' ? { name: e.name, address: e.address, register_type: e.register_type, entity_type: 'switch', status: 'skipped', reason: 'Output control is excluded from read-only verification' } : { name: e.name, address: e.address, register_type: e.register_type, entity_type: e.entity_type, status: 'pass', value: 'sample value' }) };
      }
      if (type === 'save_device') {
        const dev = { ...payload.device };
        if (!dev.id) dev.id = 'dev_' + Math.random().toString(36).substring(2, 9);
        const idx = entry.devices.findIndex(d => d.id === dev.id);
        if (idx >= 0) entry.devices[idx] = dev; else entry.devices.push(dev);
        return { success: true, device: dev };
      }
      if (type === 'delete_device') {
        entry.devices = entry.devices.filter(d => d.id !== payload.device_id);
        if (payload.delete_entities !== false) {
          entry.entities = entry.entities.filter(e => e.device_id !== payload.device_id);
        }
        return { success: true };
      }
      if (type === 'save_entity') {
        const ent = { ...payload.entity };
        if (!ent.id) ent.id = 'e_' + Math.random().toString(36).substring(2, 8);
        const idx = entry.entities.findIndex(e => e.id === ent.id);
        if (idx >= 0) entry.entities[idx] = ent; else entry.entities.push(ent);
        return { success: true, entity: ent };
      }
      if (type === 'delete_entity') {
        entry.entities = entry.entities.filter(e => e.id !== payload.entity_id);
        return { success: true };
      }
      if (type === 'save_hub') {
        Object.assign(entry.hub, payload.hub);
        return { success: true };
      }
      if (type === 'get_templates') {
        return { templates: MOCK_DATA.templates };
      }
      if (type === 'save_template') {
        let tpl = MOCK_DATA.templates.find(t => t.filename === payload.filename);
        if (!tpl) {
          tpl = { id: payload.filename.replace('.yaml',''), filename: payload.filename, name: payload.filename, entities: [], raw_yaml: payload.content };
          MOCK_DATA.templates.push(tpl);
        }
        tpl.raw_yaml = payload.content;
        return { success: true, template: tpl };
      }
      if (type === 'delete_template') {
        MOCK_DATA.templates = MOCK_DATA.templates.filter(t => t.filename !== payload.filename);
        return { success: true };
      }
      if (type === 'save_and_apply_template') {
        // Designer one-step flow: save the draft, then reuse the apply logic.
        const nameMatch = payload.content.match(/^name:\s*(.+)$/m);
        const filename = payload.filename
          || `${((nameMatch ? nameMatch[1] : 'custom_template') + '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'custom_template'}.yaml`;
        handleMockCall('save_template', { filename, content: payload.content });
        const applied = handleMockCall('apply_template', { ...payload, template_filename: filename });
        return { ...applied, filename, template_id: filename.replace(/\.ya?ml$/, '') };
      }
      if (type === 'apply_template') {
        const tpl = MOCK_DATA.templates.find(t => t.filename === payload.template_filename || t.id === payload.template_id);
        if (!tpl) throw new Error('Template not found');
        let devId = payload.device_id;
        if (!devId) {
          devId = 'dev_' + Math.random().toString(36).substring(2, 9);
          entry.devices.push({
            id: devId,
            name: payload.device_name || tpl.name,
            slave_id: payload.slave_id || tpl.default_slave_id || 1,
            model: tpl.model,
            manufacturer: tpl.manufacturer,
            description: tpl.description,
            image: tpl.image || '',
            m0_short: Boolean(payload.m0_short)
          });
        }
        const offset = parseInt(payload.address_offset || 0, 10);
        let count = 0;
        (tpl.entities || []).forEach(e => {
          if (!payload.selected_entities || payload.selected_entities.includes(e.name)) {
            entry.entities.push({
              ...e,
              id: 'e_' + Math.random().toString(36).substring(2, 8),
              device_id: devId,
              address: (e.address || 0) + offset
            });
            count++;
          }
        });
        return { success: true, device_id: devId, added_count: count };
      }
      return { success: true };
    }

    // ─── Data Loading & Refresh ─────────────────────────────────
    async function refreshData() {
      const btn = document.getElementById('btn-refresh');
      btn.disabled = true;
      btn.classList.add('is-loading');
      btn.setAttribute('aria-busy', 'true');

      try {
        if (_isLiveHA) {
          _hass = getHass() || _hass;
          _statesCache = _hass ? (_hass.states || {}) : {};
        }

        const data = await apiCall('get_data');
        _entries = data.entries || [];
        _templates = data.templates || [];

        if (_entries.length > 0) {
          if (!_currentEntryId || !_entries.some(e => e.entry_id === _currentEntryId)) {
            _currentEntryId = _entries[0].entry_id;
          }
        }

        renderHubSelector();
        renderHeaderSubtitle();
        renderDevicesTab();
        renderTemplatesTab();
        renderDashboardTab();
        renderEntitiesTab();
        renderHubTab();
        renderDiagnosticsTab();
        renderDesignerApplyTargets();
        renderDesignerImportOptions();
        if (document.getElementById('pane-inspector')?.classList.contains('active')) {
          // Prefer the live WebSocket stream; poll only as a fallback and
          // never while the user paused the inspector.
          ensureTrafficSubscription();
          if (!_inspectorLive && !_inspectorPaused) loadTrafficInspector(true);
        }
      } catch(e) {
        console.error('[Modbus USB] Refresh failed:', e);
        toast('Failed to load data: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
        btn.classList.remove('is-loading');
        btn.removeAttribute('aria-busy');
      }
    }

    function getCurrentEntry() {
      return _entries.find(e => e.entry_id === _currentEntryId) || _entries[0] || null;
    }

    // ─── Find Live State ────────────────────────────────────────
    function getLiveState(ent, dev) {
      if (!_isLiveHA) {
        // Return simulated value in preview mode
        if (ent.entity_type === 'switch') return { state: 'on' };
        if (ent.entity_type === 'binary_sensor') return { state: 'on' };
        if (ent.name.includes('Voltage')) return { state: '230.4' };
        if (ent.name.includes('Current')) return { state: '4.82' };
        if (ent.name.includes('Power')) return { state: '1110.5' };
        if (ent.name.includes('Energy')) return { state: '428.6' };
        if (ent.name.includes('Temperature')) return { state: '24.2' };
        if (ent.name.includes('Humidity')) return { state: '55.0' };
        return { state: '100' };
      }

      const slug = (ent.name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      const devSlug = dev ? (dev.name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_' : '';
      const domain = ent.entity_type === 'switch' ? 'switch' : ent.entity_type === 'binary_sensor' ? 'binary_sensor' : ent.entity_type === 'number' ? 'number' : 'sensor';

      // Verified Modbus feedback wins while HA updates its state machine. This
      // prevents an old browser snapshot from rendering a known ON channel as
      // OFF immediately after "Read channel states".
      if (ent.entity_type === 'switch' && ['on', 'off'].includes(ent.reported_state)) {
        return { state: ent.reported_state };
      }

      // The backend resolves the exact Home Assistant entity ID through the
      // entity registry. Use it first; name-based guesses can select a stale
      // entity when channels have been re-created or similarly named.
      if (ent.ha_entity_id && _statesCache[ent.ha_entity_id]) {
        return _statesCache[ent.ha_entity_id];
      }

      const candidateIds = [
        `${domain}.${devSlug}${slug}`,
        `${domain}.${slug}`,
        `sensor.${devSlug}${slug}`,
        `sensor.${slug}`,
        `switch.${devSlug}${slug}`,
        `switch.${slug}`,
      ];

      for (const id of candidateIds) {
        if (_statesCache[id]) return _statesCache[id];
      }

      // Friendly name match
      for (const [sid, st] of Object.entries(_statesCache)) {
        if (st.attributes?.friendly_name?.toLowerCase() === (ent.name || '').toLowerCase()) {
          return st;
        }
      }
      return null;
    }

    // ─── TAB 6: SERIAL DIAGNOSTICS ──────────────────────────────
    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>'"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#039;', '"':'&quot;' })[c]);
    }

    function formatLogTime(timestamp) {
      if (!timestamp) return '—';
      const date = new Date(timestamp);
      return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleTimeString();
    }

    // ─── TAB SWITCHING ──────────────────────────────────────────
    function switchTab(tabName) {
      document.querySelectorAll('.nav-tab').forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
        t.tabIndex = -1;
      });
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.remove('active');
        p.hidden = true;
      });

      const btn = document.getElementById(`tab-btn-${tabName}`);
      const pane = document.getElementById(`pane-${tabName}`);
      if (btn) {
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        btn.tabIndex = 0;
      }
      if (pane) {
        pane.hidden = false;
        pane.classList.add('active');
      }
      if (tabName === 'inspector') {
        // v2.7.1: re-apply the persisted saved view (filter preset) before
        // the first render so the tab opens already filtered.
        applyStoredInspectorPreset();
        loadTrafficInspector();
        ensureTrafficSubscription();
      } else {
        teardownTrafficSubscription();
      }
      if (tabName === 'designer') initDesignerTab();
    }

    // ─── MODALS CONTROL ─────────────────────────────────────────
    function openModal(id) {
      const el = document.getElementById(id);
      if (el) el.classList.add('open');
    }
    function closeModal(id) {
      const el = document.getElementById(id);
      if (el) el.classList.remove('open');
    }

    // ─── TOAST NOTIFICATIONS ────────────────────────────────────
    function toast(msg, type = 'inf') {
      const container = document.getElementById('snackbar');
      const el = document.createElement('div');
      el.className = `toast ${type}`;
      const icon = type === 'ok' ? '✅' : type === 'err' ? '❌' : 'ℹ️';
      el.innerHTML = `<span>${icon}</span><span>${msg}</span>`;
      container.appendChild(el);
      setTimeout(() => el.remove(), 4200);
    }
