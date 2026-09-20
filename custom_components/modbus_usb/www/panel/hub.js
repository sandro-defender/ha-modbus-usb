/* hub.js — Hub settings tab and USB port selection.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 5: RENDER HUB & SERIAL ─────────────────────────────
    function renderHubTab() {
      const entry = getCurrentEntry();
      const grid = document.getElementById('hub-info-grid');
      if (!entry || !entry.hub) {
        grid.innerHTML = `<div style="grid-column:1/-1; color:var(--text-dim); text-align:center; padding:1.5rem;">No hub configured.</div>`;
        return;
      }
      const hub = entry.hub;
      const fields = [
        ['Serial Port', hub.port || '—'],
        ['Baud Rate', hub.baudrate ? `${hub.baudrate} baud` : '—'],
        ['Data Format', `${hub.bytesize || 8} Data bits, ${hub.parity || 'N'} Parity, ${hub.stopbits || 1} Stop`],
        ['Default Slave ID', hub.slave_id || '1'],
        ['Polling Scan Interval', `${hub.scan_interval || 10} seconds`],
        ['Config Entry ID', entry.entry_id || '—'],
      ];

      grid.innerHTML = fields.map(([label, val]) => `
        <div style="background:rgba(255,255,255,0.025); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:0.85rem;">
          <div style="font-size:0.7rem; color:var(--text-dim); text-transform:uppercase; font-weight:600; letter-spacing:0.04em;">${label}</div>
          <div class="mono" style="font-size:0.95rem; font-weight:700; color:#f1f5f9; margin-top:3px;">${val}</div>
        </div>
      `).join('');
    }

    // ─── EDIT HUB MODAL ─────────────────────────────────────────
    function openEditHubModal() {
      const entry = getCurrentEntry();
      const hub = entry?.hub || {};

      document.getElementById('hub-form-port').value = hub.port || '/dev/ttyUSB0';
      document.getElementById('hub-form-baud').value = hub.baudrate || 9600;
      document.getElementById('hub-form-bytesize').value = hub.bytesize || 8;
      document.getElementById('hub-form-parity').value = hub.parity || 'N';
      document.getElementById('hub-form-stopbits').value = hub.stopbits || 1;
      document.getElementById('hub-form-slave').value = hub.slave_id || 1;
      document.getElementById('hub-form-interval').value = hub.scan_interval || 10;
      document.getElementById('hub-usb-port-list').style.display = 'none';
      document.getElementById('hub-usb-port-message').textContent = 'Select Find USB to list adapters visible to Home Assistant.';

      openModal('modal-edit-hub');
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
      const hubData = {
        port: document.getElementById('hub-form-port').value.trim(),
        baudrate: parseInt(document.getElementById('hub-form-baud').value, 10),
        bytesize: parseInt(document.getElementById('hub-form-bytesize').value, 10),
        parity: document.getElementById('hub-form-parity').value,
        stopbits: parseInt(document.getElementById('hub-form-stopbits').value, 10),
        slave_id: parseInt(document.getElementById('hub-form-slave').value, 10),
        scan_interval: parseInt(document.getElementById('hub-form-interval').value, 10),
      };

      try {
        await apiCall('save_hub', {
          entry_id: entry.entry_id,
          hub: hubData
        });
        toast('Hub settings saved! Reconnecting to serial port…', 'ok');
        closeModal('modal-edit-hub');
        await refreshData();
      } catch(e) {
        toast('Failed to save hub settings: ' + e.message, 'err');
      }
    }
