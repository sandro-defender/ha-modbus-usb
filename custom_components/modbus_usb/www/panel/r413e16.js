/* r413e16.js — R413E16 board controls, channel actions, and combined switches.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    function renderR413e16StateReport(report) {
      const rows = (report.states || []).map(item => item.ok
        ? `CH-${String(item.channel).padStart(2, '0')}: <strong>${escapeHtml(item.value)}</strong>`
        : `CH-${String(item.channel).padStart(2, '0')}: <strong>Error</strong>`).join(' · ');
      return `<details class="device-test-report" open><summary>Device read-back — all 16 channels synchronized</summary><p style="margin-top:0.55rem; color:var(--text-muted);">Raw function-03 values: ${rows}</p><p style="margin-top:0.4rem; color:#86efac;">Confirmed R413E16 feedback: <strong>1 = ON</strong>, <strong>0 = OFF</strong>. The channel switches now match this board response.</p></details>`;
    }

    function openR413e16Controls(deviceId) {
      const entry = getCurrentEntry();
      const dev = (entry?.devices || []).find(item => item.id === deviceId);
      if (!entry || !dev || !(dev.device_controls?.protocol === 'eletechsup_r413e16' || String(dev.model || '').toLowerCase().includes('r413e16'))) { toast('R413E16 controls are unavailable for this device', 'err'); return; }
      const baud = entry.hub?.baudrate || 9600;
      const slave = dev.slave_id != null ? dev.slave_id : (entry.hub?.slave_id || 1);
      document.getElementById('r413e16-device-id').value = deviceId;
      document.getElementById('r413e16-original-baud').value = baud;
      document.getElementById('r413e16-original-slave').value = slave;
      document.getElementById('r413e16-original-m0').value = String(Boolean(dev.m0_short));
      document.getElementById('r413e16-baud').value = baud;
      document.getElementById('r413e16-slave').value = slave;
      document.getElementById('r413e16-m0-short').checked = Boolean(dev.m0_short);
      const m0 = dev.device_controls?.m0 || findStandardControlTemplate(dev)?.device_controls?.m0 || {};
      document.getElementById('r413e16-m0-label').textContent = m0.label || 'M0 hardware setting';
      document.getElementById('r413e16-m0-help').textContent = m0.description || '';
      openModal('modal-r413e16-controls');
    }

    async function readR413e16States(deviceId) {
      const entry = getCurrentEntry();
      const button = document.getElementById(`r413e16-read-${deviceId}`);
      if (!entry) return;
      if (button) { button.disabled = true; button.textContent = 'Reading…'; }
      try {
        const report = await apiCall('r413e16_command', { entry_id: entry.entry_id, device_id: deviceId, command: 'read_states' });
        _r413e16StateReports[deviceId] = report;
        // The command updates HA switch entities. Refresh the state snapshot
        // before rendering so the sixteen toggles use the fresh real feedback.
        await refreshData();
        const replied = report.states.filter(item => item.ok).length;
        toast(`Requested state from ${replied}/16 channels`, replied === 16 ? 'ok' : 'err');
      } catch (error) {
        toast(`Could not read channel states: ${error.message}`, 'err');
        if (button) { button.disabled = false; button.textContent = 'Read channel states'; }
      }
    }

    function openR413e16Documentation() {
      openModal('modal-r413e16-documentation');
    }

    async function convertR413e16AllChannelsSwitch(deviceId, entityId) {
      const entry = getCurrentEntry();
      const legacy = (entry?.entities || []).find(item => item.id === entityId);
      if (!entry || !legacy || !confirm('Convert this legacy All Channels switch to Combined Switch? It will control channels 1–16 individually using the confirmed R413E16 commands.')) return;
      try {
        await apiCall('save_entity', {
          entry_id: entry.entry_id,
          entity: {
            ...legacy, device_id: deviceId, name: 'Combined Switch', entity_type: 'switch',
            register_type: 'holding', address: 1, addresses: Array.from({ length: 16 }, (_, index) => index + 1), data_type: 'uint16',
            on_value: 256, off_value: 512, assumed_state: true,
          },
        });
        toast('Converted to Combined Switch', 'ok');
        await refreshData();
      } catch (err) { toast('Could not convert the switch: ' + err.message, 'err'); }
    }

    function openR413e16ChannelAction(deviceId, channel, name) {
      document.getElementById('r413e16-action-device-id').value = deviceId;
      document.getElementById('r413e16-action-channel').value = channel;
      document.getElementById('r413e16-action-title').textContent = `${name} — R413E16 action`;
      document.getElementById('r413e16-action-kind').value = 'toggle';
      document.getElementById('r413e16-delay-seconds').value = 1;
      updateR413e16ActionForm();
      openModal('modal-r413e16-channel-action');
    }

    function updateR413e16ActionForm() {
      const action = document.getElementById('r413e16-action-kind').value;
      const delay = document.getElementById('r413e16-delay-group');
      const hint = document.getElementById('r413e16-action-hint');
      delay.style.display = action === 'delay' ? 'block' : 'none';
      hint.textContent = {
        toggle: 'Sends the board’s toggle command to this channel.',
        momentary: 'Sends the board’s documented one-second pulse command.',
        delay: 'Sends the board’s documented timed action command.',
        interlock: 'Warning: this turns this channel on and turns every other channel off.',
      }[action];
    }

    async function runR413e16ChannelAction() {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r413e16-action-device-id').value;
      const channel = parseInt(document.getElementById('r413e16-action-channel').value, 10);
      const action = document.getElementById('r413e16-action-kind').value;
      const delaySeconds = parseInt(document.getElementById('r413e16-delay-seconds').value, 10);
      if (!entry || !deviceId || !Number.isInteger(channel)) return;
      if (action === 'delay' && (!Number.isInteger(delaySeconds) || delaySeconds < 0 || delaySeconds > 255)) { toast('Enter a delay from 0 to 255 seconds', 'err'); return; }
      const extra = action === 'interlock' ? '\n\nThis will turn all other R413E16 channels OFF.' : action === 'delay' ? `\n\nTimed action: ${delaySeconds} seconds.` : '';
      if (!confirm(`Run ${action} on channel ${channel}?${extra}`)) return;
      const button = document.getElementById('r413e16-action-run'); button.disabled = true; button.textContent = 'Sending…';
      try {
        const payload = { entry_id: entry.entry_id, device_id: deviceId, command: 'channel_action', channel, action };
        if (action === 'delay') payload.delay_seconds = delaySeconds;
        await apiCall('r413e16_command', payload);
        closeModal('modal-r413e16-channel-action'); toast(`Channel ${channel}: ${action} command sent`, 'ok'); setTimeout(refreshData, 500);
      } catch (err) { toast('Channel action failed: ' + err.message, 'err'); }
      finally { button.disabled = false; button.textContent = 'Run action'; }
    }

    async function runR413e16GroupCommand(command) {
      const entry = getCurrentEntry(); const deviceId = document.getElementById('r413e16-device-id').value;
      const actionLabel = command === 'all_on' ? 'turn every R413E16 output ON' : 'turn every R413E16 output OFF';
      if (!entry || !deviceId || !confirm(`Confirm: ${actionLabel}?`)) return;
      const button = document.getElementById(command === 'all_on' ? 'r413e16-all-on' : 'r413e16-all-off');
      if (button) { button.disabled = true; button.textContent = 'Sending…'; }
      try { await apiCall('r413e16_command', { entry_id: entry.entry_id, device_id: deviceId, command }); toast(command === 'all_on' ? 'All R413E16 outputs turned ON' : 'All R413E16 outputs turned OFF', 'ok'); }
      catch (err) { toast('R413E16 command failed: ' + err.message, 'err'); }
      finally { if (button) { button.disabled = false; button.textContent = command === 'all_on' ? 'Turn all ON' : 'Turn all OFF'; } }
    }

    async function saveR413e16Settings() {
      const entry = getCurrentEntry(); const deviceId = document.getElementById('r413e16-device-id').value;
      const baudrate = parseInt(document.getElementById('r413e16-baud').value, 10); const slaveId = parseInt(document.getElementById('r413e16-slave').value, 10);
      const originalBaud = parseInt(document.getElementById('r413e16-original-baud').value, 10); const originalSlave = parseInt(document.getElementById('r413e16-original-slave').value, 10);
      const m0Short = document.getElementById('r413e16-m0-short').checked; const originalM0 = document.getElementById('r413e16-original-m0').value === 'true';
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      if (!entry || !deviceId || !Number.isFinite(baudrate) || !Number.isInteger(slaveId) || slaveId < 1 || slaveId > 247) { toast('Enter a valid baud rate and slave ID (1–247)', 'err'); return; }
      if (baudrate === originalBaud && slaveId === originalSlave && m0Short === originalM0) { toast('No board settings were changed', 'inf'); return; }
      if ((baudrate !== originalBaud || slaveId !== originalSlave) && !confirm(`Change this R413E16 board to ${baudrate} baud and slave ID ${slaveId}?\n\nThe board changes immediately. The hub will reconnect with the new baud rate.`)) return;
      const button = document.getElementById('r413e16-save'); button.disabled = true; button.textContent = 'Saving…';
      try {
        const payload = { entry_id: entry.entry_id, device_id: deviceId, command: 'configure' };
        if (baudrate !== originalBaud) payload.baudrate = baudrate;
        if (slaveId !== originalSlave) payload.slave_id = slaveId;
        if (baudrate !== originalBaud || slaveId !== originalSlave) await apiCall('r413e16_command', payload);
        if (m0Short !== originalM0 && device) await apiCall('save_device', { entry_id: entry.entry_id, device: { ...device, m0_short: m0Short } });
        closeModal('modal-r413e16-controls'); toast(baudrate !== originalBaud ? 'Settings saved. Power-cycle the board to apply the new baud rate.' : m0Short !== originalM0 ? 'M0 output polarity recorded. No command was sent to the board.' : 'Board slave ID saved.', 'ok'); setTimeout(refreshData, 1500);
      } catch (err) { toast('Could not save board settings: ' + err.message, 'err'); }
      finally { button.disabled = false; button.textContent = 'Save board settings'; }
    }

    async function resetR413e16Board() {
      const entry = getCurrentEntry(); const deviceId = document.getElementById('r413e16-device-id').value;
      if (!entry || !deviceId) return;
      if (!confirm('Restore this R413E16 to factory connection settings?\n\nThis sends the documented reset command. After it is accepted, power-cycle the board. The integration will use 9600 baud and slave ID 1.')) return;
      const button = document.getElementById('r413e16-reset'); button.disabled = true; button.textContent = 'Resetting…';
      try {
        await apiCall('r413e16_command', { entry_id: entry.entry_id, device_id: deviceId, command: 'factory_reset' });
        closeModal('modal-r413e16-controls'); toast('Reset command sent. Power-cycle the board, then use 9600 baud and slave ID 1.', 'ok'); setTimeout(refreshData, 1500);
      } catch (err) { toast('Factory reset failed: ' + err.message, 'err'); }
      finally { button.disabled = false; button.textContent = 'Restore factory settings'; }
    }

    function renderSwitchChannelPicker() {
      const wrap = document.getElementById('ent-form-channel-picker-wrap');
      const picker = document.getElementById('ent-form-channel-picker');
      const type = document.getElementById('ent-form-type')?.value;
      const deviceId = document.getElementById('ent-form-device')?.value;
      const entry = getCurrentEntry();
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      const isR413e16 = String(device?.model || '').toLowerCase().includes('r413e16');
      if (type !== 'switch' || !isR413e16) {
        wrap.style.display = 'none';
        picker.innerHTML = '';
        return;
      }
      const field = document.getElementById('ent-form-addresses');
      const selected = new Set(field.value.split(',').map(value => Number(value.trim())).filter(Number.isInteger));
      wrap.style.display = 'block';
      picker.innerHTML = Array.from({ length: 16 }, (_, index) => {
        const channel = index + 1;
        return `<label><input type="checkbox" value="${channel}" ${selected.has(channel) ? 'checked' : ''} onchange="syncR413e16ChannelPicker()" /> CH-${String(channel).padStart(2, '0')}</label>`;
      }).join('');
    }

    function syncR413e16ChannelPicker() {
      const values = [...document.querySelectorAll('#ent-form-channel-picker input:checked')]
        .map(input => Number(input.value)).sort((a, b) => a - b);
      document.getElementById('ent-form-addresses').value = values.join(', ');
    }

    function openAddR413e16CombinedSwitch(deviceId) {
      openAddEntityModal(deviceId);
      document.getElementById('entity-modal-title').textContent = 'Add Combined Switch';
      document.getElementById('ent-form-name').value = 'Combined Switch';
      document.getElementById('ent-form-type').value = 'switch';
      document.getElementById('ent-form-regtype').value = 'holding';
      document.getElementById('ent-form-onval').value = '256';
      document.getElementById('ent-form-offval').value = '512';
      onEntityTypeChange('switch');
      renderSwitchChannelPicker();
    }
