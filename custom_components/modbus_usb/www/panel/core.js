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
      if (type === 'scan_usb_ports') {
        return { ports: [{ port: '/dev/ttyUSB0', description: 'USB-RS485 Adapter', details: 'CH340 USB-Serial' }] };
      }
      if (type === 'manual_hex_write') return { slave_id: 1, function_code: '0x06', address: 128, count: 1 };
      if (type === 'get_serial_status') {
        return { serial: { port: '/dev/ttyUSB0', baudrate: 9600, bytesize: 8, parity: 'N', stopbits: 1, connection_owner: 'Home Assistant Modbus USB', operation_active: false }, adapter: { port: '/dev/ttyUSB0', description: 'USB-RS485 Adapter', details: 'CH340 USB-Serial' } };
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
