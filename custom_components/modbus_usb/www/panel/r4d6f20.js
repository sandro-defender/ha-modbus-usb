/* r4d6f20.js — R4D6F20 board controls, mode switching, and serial setup.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    async function openR4d6f20Controls(deviceId) {
      const entry = getCurrentEntry();
      const dev = (entry?.devices || []).find(item => item.id === deviceId);
      if (!entry || !dev) return;
      document.getElementById('r4d6f20-device-id').value = deviceId;
      document.getElementById('r4d6f20-baud').value = String(entry.hub?.baudrate || 9600);
      document.getElementById('r4d6f20-parity').value = entry.hub?.parity || 'N';
      document.getElementById('r4d6f20-m0-short').checked = Boolean(dev.m0_short);
      updateR4d6f20ModeUi();
      document.getElementById('r4d6f20-result').textContent = `Target: ${dev.name || dev.model || 'R4D6F20'} · slave ${dev.slave_id ?? entry.hub?.slave_id ?? 1}`;
      openModal('modal-r4d6f20-controls');
    }

    function updateR4d6f20ModeUi() {
      const m0Short = document.getElementById('r4d6f20-m0-short').checked;
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r4d6f20-device-id').value;
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      const m0 = device?.device_controls?.m0 || findStandardControlTemplate(device)?.device_controls?.m0 || {};
      const command1Actions = ['r4d6f20-command1-actions', 'r4d6f20-command1-delay'];
      command1Actions.forEach(id => { document.getElementById(id).style.display = m0Short ? 'none' : ''; });
      document.getElementById('r4d6f20-run-action').style.display = m0Short ? 'none' : '';
      document.getElementById('r4d6f20-mode-help').textContent = m0Short
        ? (m0.shorted_description || m0.description || '')
        : (m0.open_description || m0.description || '');
    }

    async function runR4d6f20Command(command) {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r4d6f20-device-id').value;
      const result = document.getElementById('r4d6f20-result');
      if (!entry || !deviceId) return;
      const destructive = ['all_on', 'all_off', 'factory_reset'].includes(command);
      const label = command.replaceAll('_', ' ');
      if (destructive && !confirm(`Confirm ${label} for the selected R4D6F20 board?`)) return;
      result.textContent = `Sending ${label}…`;
      try {
        const response = await apiCall('r4d6f20_command', { entry_id: entry.entry_id, device_id: deviceId, command });
        result.textContent = command === 'read_slave_id' ? `Board reported slave ID ${response.slave_id}.` : `${label} accepted.`;
        toast('R4D6F20 command sent', 'ok');
        await refreshData();
      } catch (err) { result.textContent = `Command failed: ${err.message}`; toast('R4D6F20 command failed: ' + err.message, 'err'); }
    }

    async function saveR4d6f20Mode() {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r4d6f20-device-id').value;
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      const m0Short = document.getElementById('r4d6f20-m0-short').checked;
      const m0Config = device?.device_controls?.m0 || findStandardControlTemplate(device)?.device_controls?.m0 || {};
      if (!entry || !device) return;
      if (m0Short && !confirm(m0Config.shorted_description || m0Config.description || 'Save the selected M0 mode?')) return;
      try {
        await apiCall(m0Config.save_command || 'r4d6f20_set_mode', { entry_id: entry.entry_id, device_id: deviceId, m0_short: m0Short });
        document.getElementById('r4d6f20-result').textContent = m0Short ? (m0Config.shorted_description || 'M0 mode saved.') : (m0Config.open_description || 'M0 mode saved.');
        updateR4d6f20ModeUi();
        await refreshData();
      } catch (err) { document.getElementById('r4d6f20-result').textContent = `Could not save M0 mode: ${err.message}`; }
    }

    async function saveR4d6f20Serial() {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r4d6f20-device-id').value;
      const baudrate = parseInt(document.getElementById('r4d6f20-baud').value, 10);
      const parity = document.getElementById('r4d6f20-parity').value;
      if (!entry || !deviceId || !Number.isFinite(baudrate) || !confirm(`Change the selected board and hub to ${baudrate} baud, ${parity} parity? Power-cycle the board after saving.`)) return;
      try {
        await apiCall('r4d6f20_command', { entry_id: entry.entry_id, device_id: deviceId, command: 'configure_serial', baudrate, parity });
        document.getElementById('r4d6f20-result').textContent = 'Serial settings saved. Power-cycle the board to apply them.';
        toast('Serial settings saved', 'ok');
        await refreshData();
      } catch (err) { document.getElementById('r4d6f20-result').textContent = `Serial settings failed: ${err.message}`; }
    }

    async function runR4d6f20ChannelAction() {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('r4d6f20-device-id').value;
      const channel = parseInt(document.getElementById('r4d6f20-channel').value, 10) - 1;
      const action = document.getElementById('r4d6f20-action').value;
      const delaySeconds = parseInt(document.getElementById('r4d6f20-delay').value, 10);
      if (!entry || !deviceId || channel < 0 || channel > 19 || (action === 'delay' && (!Number.isInteger(delaySeconds) || delaySeconds < 0 || delaySeconds > 255)) || !confirm(`Run ${action} on relay ${channel + 1}?`)) return;
      try {
        await apiCall('r4d6f20_command', { entry_id: entry.entry_id, device_id: deviceId, command: 'channel_action', channel, action, ...(action === 'delay' ? { delay_seconds: delaySeconds } : {}) });
        document.getElementById('r4d6f20-result').textContent = `Relay ${channel + 1}: ${action} accepted.`;
        await refreshData();
      } catch (err) { document.getElementById('r4d6f20-result').textContent = `Relay command failed: ${err.message}`; }
    }
