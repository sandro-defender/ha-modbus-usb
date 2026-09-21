/* hub.js — Hub settings tab and USB port selection.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 5: RENDER HUB & CONNECTION ─────────────────────────
    // v2.8.0: the hub can be a local USB adapter or an ESPHome device
    // (RTU over TCP stream server, or the native API). The tab and the
    // edit modal switch their field groups on `hub.transport`.
    const HUB_TRANSPORT_LABELS = {
      serial: 'Serial (USB adapter)',
      esphome_tcp: 'ESPHome · RTU over TCP',
      esphome_api: 'ESPHome · native API',
    };

    function hubTransport(hub) {
      const transport = hub && hub.transport;
      return HUB_TRANSPORT_LABELS[transport] ? transport : 'serial';
    }

    function hubIsEsphome(hub) {
      const transport = hubTransport(hub);
      return transport === 'esphome_tcp' || transport === 'esphome_api';
    }

    function hubEndpoint(hub) {
      if (!hub) return '—';
      if (hub.endpoint) return hub.endpoint;
      const transport = hubTransport(hub);
      if (transport === 'serial') return hub.port || '—';
      if (!hub.host) return '—';
      return transport === 'esphome_tcp' ? `${hub.host}:${hub.tcp_port || 8899}` : `${hub.host}:${hub.api_port || 6053}`;
    }

    function renderHubTab() {
      const entry = getCurrentEntry();
      const grid = document.getElementById('hub-info-grid');
      if (!entry || !entry.hub) {
        grid.innerHTML = `<div style="grid-column:1/-1; color:var(--text-dim); text-align:center; padding:1.5rem;">No hub configured.</div>`;
        return;
      }
      const hub = entry.hub;
      const transport = hubTransport(hub);
      const esphome = hubIsEsphome(hub);
      const fields = [
        ['Transport', `<span class="transport-badge transport-${transport}">${escapeHtml(hub.transport_label || HUB_TRANSPORT_LABELS[transport])}</span>`],
        [esphome ? 'ESPHome endpoint' : 'Serial Port', escapeHtml(hubEndpoint(hub))],
        ['Baud Rate', hub.baudrate ? `${hub.baudrate} baud${hub.baudrate_fixed ? ' <span class="text-sm" style="color:var(--text-dim);">(fixed in ESPHome uart:)</span>' : ''}` : '—'],
        ['Data Format', `${hub.bytesize || 8} Data bits, ${hub.parity || 'N'} Parity, ${hub.stopbits || 1} Stop`],
        ['Default Slave ID', hub.slave_id || '1'],
        ['Polling Scan Interval', `${hub.scan_interval || 10} seconds`],
      ];
      if (esphome) {
        fields.push(['Response timeout', `${hub.response_timeout ?? (transport === 'esphome_api' ? 1.5 : 3)} s`]);
      }
      if (transport === 'esphome_api') {
        fields.push(['API service / event', `${escapeHtml(hub.esphome_service || 'modbus_send')} · ${escapeHtml(hub.esphome_event || 'esphome.modbus_rx')}`]);
        fields.push(['API security', hub.encrypted ? '🔒 encryption key set' : (hub.password_set ? '🔑 password set' : '⚠ no encryption key')]);
      }
      fields.push(['Config Entry ID', escapeHtml(entry.entry_id || '—')]);

      grid.innerHTML = fields.map(([label, val]) => `
        <div style="background:rgba(255,255,255,0.025); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:0.85rem;">
          <div style="font-size:0.7rem; color:var(--text-dim); text-transform:uppercase; font-weight:600; letter-spacing:0.04em;">${label}</div>
          <div class="mono" style="font-size:0.95rem; font-weight:700; color:#f1f5f9; margin-top:3px;">${val}</div>
        </div>
      `).join('') + `
        <div class="hub-connection-test" id="hub-connection-test" style="grid-column:1/-1; display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap;">
          <button class="btn btn-secondary btn-sm" id="btn-test-hub-connection" onclick="testHubConnection()">📶 Test connection</button>
          <span class="text-sm" id="hub-connection-test-result" style="color:var(--text-dim);">${esphome ? 'Checks that the ESPHome bridge is reachable. Nothing is sent on the RS-485 bus.' : 'Checks that the serial adapter can be opened. Nothing is sent on the RS-485 bus.'}</span>
        </div>
        ${esphome ? renderEsphomeHelp(transport) : ''}`;
      if (!esphome) renderHubOwnershipRow(hub);
    }

    // ─── v2.9.0: PORT OWNERSHIP BANNER + STABLE BY-ID PATH ──────────
    // One-sentence explanation of why the serial port cannot be opened
    // (missing / busy / permission / unknown). When the configured port
    // is a dynamic ttyUSB*/ttyACM* with a matching /dev/serial/by-id
    // link, a one-click "Use stable path" button offers the persistent
    // path; confirming it saves the port via the regular save_hub
    // command (no new mutating endpoint).
    let _hubStablePath = null;
    let _hubStableEntryId = null;

    const HUB_OWNERSHIP_COLORS = {
      ok: '#34d399',
      missing: '#f87171',
      busy: '#fbbf24',
      permission: '#fbbf24',
      unknown: '#f87171',
    };

    async function renderHubOwnershipRow(hub) {
      _hubStablePath = null;
      _hubStableEntryId = null;
      const grid = document.getElementById('hub-info-grid');
      const entry = getCurrentEntry();
      if (!grid || !entry || !hub) return;
      const row = document.createElement('div');
      row.id = 'hub-ownership-row';
      row.style.cssText = 'grid-column:1/-1; display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap;';
      row.innerHTML = '<span class="text-sm" id="hub-ownership-banner" role="status" style="color:var(--text-dim);">Checking port status…</span>';
      grid.appendChild(row);
      try {
        const result = await apiCall('get_serial_status', { entry_id: entry.entry_id });
        const banner = document.getElementById('hub-ownership-banner');
        if (!banner) return;
        const ownership = result.ownership || { reason: 'unknown', hint: 'Port status is unavailable.' };
        banner.style.color = HUB_OWNERSHIP_COLORS[ownership.reason] || 'var(--text-dim)';
        banner.textContent = ownership.hint || ownership.reason;
        if (result.stable_path && result.stable_path !== hub.port) {
          _hubStablePath = result.stable_path;
          _hubStableEntryId = entry.entry_id;
          const note = document.createElement('span');
          note.className = 'text-sm';
          note.id = 'hub-stable-path-hint';
          note.style.color = 'var(--text-dim)';
          note.textContent = `Persistent path available: ${result.stable_path}`;
          const button = document.createElement('button');
          button.className = 'btn btn-secondary btn-sm';
          button.id = 'btn-use-stable-path';
          button.textContent = '🔒 Use stable path';
          button.title = `Save ${result.stable_path} as the hub port — it survives reboots and re-plugging.`;
          button.onclick = applyStableHubPath;
          row.appendChild(note);
          row.appendChild(button);
        }
      } catch (err) {
        const banner = document.getElementById('hub-ownership-banner');
        if (banner) banner.textContent = 'Could not check port status: ' + err.message;
      }
    }

    async function applyStableHubPath() {
      if (!_hubStablePath || !_hubStableEntryId) return;
      const ok = window.confirm(
        `Save the persistent path?\n\n${_hubStablePath}\n\nThe hub will reconnect on this path.`
      );
      if (!ok) return;
      try {
        await apiCall('save_hub', { entry_id: _hubStableEntryId, hub: { port: _hubStablePath } });
        toast('Stable path saved — hub reconnecting…', 'ok');
        await refreshData();
      } catch (err) {
        toast('Failed to save the stable path: ' + err.message, 'err');
      }
    }

    function renderEsphomeHelp(transport) {
      const file = transport === 'esphome_api' ? 'modbus_api_bridge_esp32.yaml' : 'modbus_bridge_esp32.yaml';
      return `<div class="esphome-help text-sm" style="grid-column:1/-1; color:var(--text-dim);">
        ESPHome firmware for this hub: <code>custom_components/modbus_usb/esphome/${file}</code> —
        baud rate, parity and stop bits are defined in that file's <code>uart:</code> block and must match the values above.
        ${transport === 'esphome_tcp'
          ? 'The stream server accepts a single TCP client; keep other tools (e.g. a desktop Modbus poller) disconnected while Home Assistant owns the bridge.'
          : 'Replies arrive as <code>esphome.modbus_rx</code> events over the native API; expect ~20–60 ms of extra latency per request compared to a USB adapter.'}
      </div>`;
    }

    async function testHubConnection(overrides) {
      const entry = getCurrentEntry();
      if (!entry) return null;
      const inModal = overrides !== undefined;
      const button = document.getElementById(inModal ? 'btn-hub-form-test' : 'btn-test-hub-connection');
      const output = document.getElementById(inModal ? 'hub-form-test-result' : 'hub-connection-test-result');
      if (button) { button.disabled = true; button.textContent = '⏳ Testing…'; }
      if (output) { output.style.color = 'var(--text-dim)'; output.textContent = 'Probing the hub endpoint…'; }
      try {
        const result = await apiCall('test_hub_connection', {
          entry_id: entry.entry_id,
          ...(inModal ? { hub: overrides } : {}),
        });
        if (output) {
          if (result.reachable) {
            const device = result.esphome && result.esphome.name
              ? ` — ESPHome "${result.esphome.name}"${result.esphome.esphome_version ? ` v${result.esphome.esphome_version}` : ''}`
              : '';
            const latency = typeof result.latency_ms === 'number' ? ` in ${result.latency_ms} ms` : (result.live ? ' (live connection)' : '');
            output.style.color = '#34d399';
            output.textContent = `✅ ${result.summary?.endpoint || 'Hub'} reachable${latency}${device}.`;
          } else {
            output.style.color = '#f87171';
            output.textContent = `❌ ${result.error || 'Not reachable'}`;
          }
        }
        return result;
      } catch (error) {
        if (output) { output.style.color = '#f87171'; output.textContent = `❌ Test failed: ${error.message}`; }
        return null;
      } finally {
        if (button) { button.disabled = false; button.textContent = '📶 Test connection'; }
      }
    }

    // ─── EDIT HUB MODAL ─────────────────────────────────────────
    function openEditHubModal() {
      const entry = getCurrentEntry();
      const hub = entry?.hub || {};
      const transport = hubTransport(hub);

      document.getElementById('hub-form-transport').value = transport;
      document.getElementById('hub-form-port').value = hub.port || '/dev/ttyUSB0';
      document.getElementById('hub-form-host').value = hub.host || '';
      document.getElementById('hub-form-tcp-port').value = hub.tcp_port || 8899;
      document.getElementById('hub-form-api-port').value = hub.api_port || 6053;
      document.getElementById('hub-form-api-key').value = '';
      document.getElementById('hub-form-api-key').placeholder = hub.encrypted ? '•••••• (stored — leave blank to keep)' : 'base64 key from the ESPHome api: block';
      document.getElementById('hub-form-service').value = hub.esphome_service || 'modbus_send';
      document.getElementById('hub-form-event').value = hub.esphome_event || 'esphome.modbus_rx';
      document.getElementById('hub-form-timeout').value = hub.response_timeout ?? (transport === 'esphome_api' ? 1.5 : 3);
      document.getElementById('hub-form-baud').value = hub.baudrate || 9600;
      document.getElementById('hub-form-bytesize').value = hub.bytesize || 8;
      document.getElementById('hub-form-parity').value = hub.parity || 'N';
      document.getElementById('hub-form-stopbits').value = hub.stopbits || 1;
      document.getElementById('hub-form-slave').value = hub.slave_id || 1;
      document.getElementById('hub-form-interval').value = hub.scan_interval || 10;
      document.getElementById('hub-usb-port-list').style.display = 'none';
      document.getElementById('hub-usb-port-message').textContent = 'Select Find USB to list adapters visible to Home Assistant.';
      const testResult = document.getElementById('hub-form-test-result');
      if (testResult) { testResult.textContent = ''; }
      renderTransportFields(transport);

      openModal('modal-edit-hub');
    }

    function renderTransportFields(transport) {
      const selected = HUB_TRANSPORT_LABELS[transport] ? transport : 'serial';
      const serialFields = document.getElementById('hub-serial-fields');
      const esphomeFields = document.getElementById('hub-esphome-fields');
      const apiFields = document.getElementById('hub-esphome-api-fields');
      const tcpFields = document.getElementById('hub-esphome-tcp-fields');
      const lineHint = document.getElementById('hub-line-settings-hint');
      if (serialFields) serialFields.hidden = selected !== 'serial';
      if (esphomeFields) esphomeFields.hidden = selected === 'serial';
      if (tcpFields) tcpFields.hidden = selected !== 'esphome_tcp';
      if (apiFields) apiFields.hidden = selected !== 'esphome_api';
      if (lineHint) {
        lineHint.textContent = selected === 'serial'
          ? ''
          : 'These must match the uart: block of the ESPHome YAML — the bridge forwards raw bytes and cannot change them.';
      }
      const timeout = document.getElementById('hub-form-timeout');
      if (timeout && !timeout.dataset.touched) {
        timeout.value = selected === 'esphome_api' ? 1.5 : 3;
      }
    }

    function collectHubForm() {
      const transport = hubTransport({ transport: document.getElementById('hub-form-transport').value });
      const hubData = {
        transport,
        baudrate: parseInt(document.getElementById('hub-form-baud').value, 10),
        bytesize: parseInt(document.getElementById('hub-form-bytesize').value, 10),
        parity: document.getElementById('hub-form-parity').value,
        stopbits: parseInt(document.getElementById('hub-form-stopbits').value, 10),
        slave_id: parseInt(document.getElementById('hub-form-slave').value, 10),
        scan_interval: parseInt(document.getElementById('hub-form-interval').value, 10),
      };
      if (transport === 'serial') {
        hubData.port = document.getElementById('hub-form-port').value.trim();
        return hubData;
      }
      hubData.host = document.getElementById('hub-form-host').value.trim();
      hubData.response_timeout = parseFloat(document.getElementById('hub-form-timeout').value) || (transport === 'esphome_api' ? 1.5 : 3);
      if (transport === 'esphome_tcp') {
        hubData.tcp_port = parseInt(document.getElementById('hub-form-tcp-port').value, 10) || 8899;
      } else {
        hubData.api_port = parseInt(document.getElementById('hub-form-api-port').value, 10) || 6053;
        hubData.esphome_service = document.getElementById('hub-form-service').value.trim() || 'modbus_send';
        hubData.esphome_event = document.getElementById('hub-form-event').value.trim() || 'esphome.modbus_rx';
        // Blank key = keep the stored one; the panel never sees the current value.
        hubData.api_encryption_key = document.getElementById('hub-form-api-key').value.trim();
      }
      return hubData;
    }

    function validateHubForm(hubData) {
      if (hubData.transport === 'serial') {
        if (!hubData.port) return 'Enter the serial port path';
      } else if (!hubData.host) {
        return 'Enter the ESPHome host name or IP address';
      }
      if (!Number.isFinite(hubData.slave_id) || hubData.slave_id < 1 || hubData.slave_id > 247) return 'Slave ID must be 1–247';
      if (!Number.isFinite(hubData.scan_interval) || hubData.scan_interval < 1) return 'Poll interval must be at least 1 second';
      return null;
    }

    async function testHubFormConnection() {
      const hubData = collectHubForm();
      const problem = validateHubForm(hubData);
      if (problem) { toast(problem, 'err'); return; }
      await testHubConnection(hubData);
    }

    async function scanUsbPorts() {
      const button = document.getElementById('btn-scan-usb-ports');
      const select = document.getElementById('hub-usb-port-list');
      const message = document.getElementById('hub-usb-port-message');
      if (button) { button.disabled = true; button.textContent = '⏳ Finding…'; }
      message.textContent = 'Checking serial and USB adapters visible to Home Assistant…';
      try {
        const result = await apiCall('scan_usb_ports');
        const ports = result.ports || [];
        if (!ports.length) {
          select.style.display = 'none';
          message.textContent = 'No serial adapters were found. Check USB passthrough or enter the port manually.';
          return;
        }
        const currentPort = document.getElementById('hub-form-port').value.trim();
        select.innerHTML = `<option value="">Choose a detected adapter (${ports.length})…</option>` + ports.map(item => {
          const label = `${item.port} — ${item.description}${item.details ? ` (${item.details})` : ''}`;
          return `<option value="${escapeHtml(item.port)}" ${item.port === currentPort ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        }).join('');
        select.style.display = 'block';
        message.textContent = `${ports.length} adapter${ports.length === 1 ? '' : 's'} found. Choose one to use it for this hub.`;
      } catch (err) {
        select.style.display = 'none';
        message.textContent = 'Could not scan USB adapters: ' + err.message;
      } finally {
        if (button) { button.disabled = false; button.textContent = '🔎 Find USB'; }
      }
    }

    function selectUsbPort(port) {
      if (port) document.getElementById('hub-form-port').value = port;
    }

    async function submitSaveHub() {
      const entry = getCurrentEntry();
      const hubData = collectHubForm();
      const problem = validateHubForm(hubData);
      if (problem) { toast(problem, 'err'); return; }

      try {
        await apiCall('save_hub', {
          entry_id: entry.entry_id,
          hub: hubData
        });
        toast(hubData.transport === 'serial' ? 'Hub settings saved! Reconnecting to serial port…' : 'Hub settings saved! Reconnecting to the ESPHome bridge…', 'ok');
        closeModal('modal-edit-hub');
        await refreshData();
      } catch(e) {
        toast('Failed to save hub settings: ' + e.message, 'err');
      }
    }
