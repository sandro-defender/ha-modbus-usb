/* devices.js — Devices tab: device cards, entity testing, and device CRUD modals.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 1: RENDER DEVICES ──────────────────────────────────
    function renderDevicesTab() {
      const entry = getCurrentEntry();
      const container = document.getElementById('device-cards-container');
      const devices = entry ? (entry.devices || []) : [];
      const entities = entry ? (entry.entities || []) : [];

      document.getElementById('count-devices').textContent = devices.length;

      if (devices.length === 0) {
        container.innerHTML = `
          <div class="empty-box card">
            <div class="empty-icon">🎛</div>
            <h3>No Devices Configured Yet</h3>
            <p>Add your first Modbus device using a pre-configured template (like an SDM120 Energy Meter, Temperature Sensor, or Relay Board) or create a custom blank device.</p>
            <div class="flex gap-1" style="justify-content:center;">
              <button class="btn btn-primary" onclick="openAddDeviceModal('template')">⚡ Add Device from Template</button>
              <button class="btn btn-ghost" onclick="openAddDeviceModal('blank')">➕ Blank Device</button>
            </div>
          </div>
        `;
        return;
      }

      container.innerHTML = devices.map(dev => {
        const devEntities = entities.filter(e => e.device_id === dev.id);
        const switchCount = devEntities.filter(e => e.entity_type === 'switch').length;
        const slaveId = dev.slave_id != null ? dev.slave_id : (entry.hub?.slave_id || 1);
        const isEnabled = dev.enabled !== false;
        const isR413e16 = dev.device_controls?.protocol === 'eletechsup_r413e16' || String(dev.model || '').toLowerCase().includes('r413e16');
        const isR4d6f20 = dev.device_controls?.protocol === 'eletechsup_r4d6f20' || String(dev.model || '').toLowerCase().includes('r4d6f20');
        const legacyAllChannelsSwitch = devEntities.find(e => e.entity_type === 'switch' && e.address === 0 && e.on_value === 1792 && e.off_value === 2048);
        const health = (entry.diagnostics?.devices || []).find(item => item.device_id === dev.id);
        const healthBadge = !isEnabled
          ? '<span class="badge badge-amber">Disabled — polling off</span>'
          : !health || health.status === 'unknown'
          ? '<span class="badge badge-slate">No bus activity</span>'
          : health.status === 'error'
            ? '<span class="badge badge-amber" title="Latest request failed">⚠ Communication error</span>'
            : '<span class="badge badge-green" title="Latest request succeeded">● Responding</span>';

        const entitiesHtml = devEntities.length === 0 ? `
          <div style="padding:1.5rem; text-align:center; color:var(--text-dim); font-size:0.8rem; width:100%;">
            No sensors or switches added to this device yet.
            <div class="flex gap-1" style="justify-content:center; margin-top:0.6rem;">
              <button class="btn btn-secondary btn-sm" onclick="openApplyTemplateModal('${dev.id}')">⚡ Add from Template</button>
              <button class="btn btn-ghost btn-sm" onclick="openAddEntityModal('${dev.id}')">➕ Add Entity</button>
            </div>
          </div>
        ` : devEntities.map(ent => {
          const type = ent.entity_type || 'sensor';
          const isR413Channel = isR413e16 && type === 'switch' && Number.isInteger(ent.address) && ent.address >= 1 && ent.address <= 16;
          const typeIcon = { sensor: '📈', switch: '💡', binary_sensor: '🔵', number: '🔢' }[type] || '📊';
          const combinedAddresses = Array.isArray(ent.addresses)
            ? ent.addresses
            : String(ent.addresses || '').split(',').map(value => value.trim()).filter(Boolean);
          const isCombinedSwitch = combinedAddresses.length > 1;
          const combinedChannelText = combinedAddresses.join(', ');
          const state = getLiveState(ent, dev);
          const val = state ? state.state : '—';
          const unit = ent.unit_of_measurement || state?.attributes?.unit_of_measurement || '';

          let controlHtml = '';
          if (type === 'switch') {
            const isOn = val === 'on';
            controlHtml = `
              <label class="switch-toggle" title="Toggle switch">
                <input type="checkbox" ${isOn ? 'checked' : ''} ${isEnabled ? '' : 'disabled'} onchange="toggleEntitySwitch('${ent.id}', this.checked)">
                <span class="switch-slider"></span>
              </label>
            `;
          } else if (type === 'number') {
            controlHtml = `
              <div class="flex items-center gap-1">
                <span class="mono text-sm" style="font-weight:700; color:#34d399;">${val}</span>
                <button class="btn btn-ghost btn-sm" style="padding:0.2rem 0.4rem; font-size:0.68rem;" ${isEnabled ? '' : 'disabled'} onclick="promptWriteNumber('${ent.id}', '${ent.name}', ${val})">✏</button>
              </div>
            `;
          } else {
            const displayVal = val === null || val === 'unknown' || val === 'unavailable' ? '—' : parseFloat(val);
            const formatted = isNaN(displayVal) ? val : displayVal.toLocaleString(undefined, { maximumFractionDigits: 3 });
            controlHtml = `
              <div class="ent-val">
                ${formatted}<span class="ent-val-unit">${unit}</span>
              </div>
            `;
          }

          return `
            <div class="device-entity-item">
              <div class="ent-left">
                <div class="ent-icon ${type}">${typeIcon}</div>
                <div class="ent-info">
                  <div class="ent-name" title="${ent.name}">${ent.name}</div>
                  <div class="ent-sub mono">
                    ${isCombinedSwitch
                      ? `<span class="combined-channel-list" tabindex="0" title="Channels: ${escapeHtml(combinedChannelText)}" aria-label="Combined channels: ${escapeHtml(combinedChannelText)}">Channels: ${escapeHtml(combinedChannelText)}</span>`
                      : `<span>${ent.register_type || 'holding'} @ ${ent.address ?? 0}</span>`}
                    <span aria-hidden="true">•</span>
                    <span class="ent-data-type">${ent.data_type || (ent.register_type === 'holding' ? 'uint16' : (type === 'switch' || type === 'binary_sensor' ? 'bit' : 'uint16'))}</span>
                  </div>
                </div>
              </div>
              <div class="ent-right">
                ${controlHtml}
                ${isR413Channel ? `<button class="btn btn-secondary btn-sm" title="Open R413E16 channel actions" onclick="openR413e16ChannelAction('${dev.id}', ${ent.address}, '${escapeHtml(ent.name)}')">Actions</button>` : ''}
                <button class="btn btn-ghost btn-sm" style="padding:0.2rem 0.45rem; font-size:0.68rem;" title="Edit entity" onclick="openEditEntityModal('${ent.id}')">✏</button>
                <button class="btn btn-danger btn-sm" style="padding:0.2rem 0.45rem; font-size:0.68rem;" title="Delete entity" onclick="confirmDeleteEntity('${ent.id}', '${ent.name}')">🗑</button>
              </div>
            </div>
          `;
        }).join('');

        const avatarImage = dev.image
          ? `<img class="device-avatar" src="${dev.image}" alt="${dev.model || dev.name || 'Device'}">`
          : `<div class="device-avatar">🎛</div>`;
        const avatar = dev.info_url
          ? `<a href="${dev.info_url}" target="_blank" rel="noopener noreferrer" title="Open device information">${avatarImage}</a>`
          : avatarImage;

        return `
          <div class="device-card ${isEnabled ? '' : 'is-disabled'} ${_expandedDeviceCards.has(dev.id) ? 'is-expanded' : ''}" id="dev-card-${dev.id}">
            <div class="device-card-header">
              <div class="device-header-left" role="button" tabindex="0" aria-expanded="${_expandedDeviceCards.has(dev.id)}" aria-controls="dev-content-${dev.id}" onclick="toggleDeviceCard(event, '${dev.id}')" onkeydown="toggleDeviceCardKey(event, '${dev.id}')">
                <span class="device-collapse-chevron" aria-hidden="true">›</span>
                ${avatar}
                <div class="device-meta">
                  <h3>
                    <span>${dev.name || 'Unnamed Device'}</span>
                    <span class="badge badge-purple mono">Slave ID: ${slaveId}</span>
                    ${healthBadge}
                    ${dev.model ? `<span class="badge badge-slate">${dev.model}</span>` : ''}
                    ${dev.manufacturer ? `<span class="badge badge-slate">${dev.manufacturer}</span>` : ''}
                    <span class="badge badge-blue">${devEntities.length} entities</span>
                  </h3>
                  <p>${dev.description || 'Modbus RTU device connected via USB'}</p>
                  ${dev.info_url ? `<a href="${dev.info_url}" target="_blank" rel="noopener noreferrer" class="text-sm" style="color:#60a5fa;">↗ Device information</a>` : ''}
                </div>
              </div>

              <div class="device-header-actions">
                <button class="btn btn-secondary btn-sm" onclick="openApplyTemplateModal('${dev.id}')" title="Add sensors/switches from any YAML template">
                  ⚡ Apply Template
                </button>
                <button class="btn btn-primary btn-sm" onclick="openAddEntityModal('${dev.id}')" title="Add a custom entity to this device">
                  ➕ Add Entity
                </button>
                ${devEntities.length ? `<button class="btn btn-secondary btn-sm" id="device-test-${dev.id}" ${isEnabled ? '' : 'disabled'} onclick="runDeviceEntityTest('${dev.id}')" title="Read all configured entities and cycle switches one at a time">🧪 Test device</button>` : ''}
                ${isR413e16 ? (legacyAllChannelsSwitch
                  ? `<button class="btn btn-secondary btn-sm" onclick="convertR413e16AllChannelsSwitch('${dev.id}', '${legacyAllChannelsSwitch.id}')" title="Replace the legacy all-channel command with a selectable combined switch">Convert to Combined Switch</button>`
                  : `<button class="btn btn-secondary btn-sm" onclick="openAddR413e16CombinedSwitch('${dev.id}')" title="Create another switch for selected R413E16 channels">Add Combined Switch</button>`) : ''}
                ${isR413e16 ? `<button class="btn btn-secondary btn-sm" onclick="openR413e16Documentation()" title="Open verified R413E16 wiring and command reference">Documentation</button>` : ''}
                ${isR413e16 ? `<button class="btn btn-secondary btn-sm" id="r413e16-read-${dev.id}" onclick="readR413e16States('${dev.id}')" title="Read every reported R413E16 channel state now">Read channel states</button>` : ''}
                ${isR413e16 ? `<button class="btn btn-secondary btn-sm" onclick="openR413e16Controls('${dev.id}')" title="Control all channels or change this R413E16 board's communication settings">R413E16 controls</button>` : ''}
                ${isR4d6f20 ? `<button class="btn btn-secondary btn-sm" ${isEnabled ? '' : 'disabled'} onclick="openR4d6f20Controls('${dev.id}')" title="Open documented R4D6F20 relay and board controls">R4D6F20 controls</button>` : ''}
                <button class="btn btn-ghost btn-sm" id="device-enabled-${dev.id}" aria-pressed="${isEnabled}" onclick="toggleDeviceEnabled('${dev.id}', ${isEnabled ? 'false' : 'true'})" title="${isEnabled ? 'Stop polling and make this device unavailable' : 'Resume polling and restore this device'}">
                  ${isEnabled ? 'Disable device' : 'Enable device'}
                </button>
                <button class="btn btn-ghost btn-sm" onclick="openEditDeviceModal('${dev.id}')" title="Rename or edit slave ID">
                  ✏ Edit
                </button>
                <button class="btn btn-danger btn-sm" onclick="confirmDeleteDevice('${dev.id}', '${dev.name}')" title="Delete device and its entities">
                  🗑 Delete
                </button>
              </div>
            </div>

            <div class="device-card-content" id="dev-content-${dev.id}" ${_expandedDeviceCards.has(dev.id) ? '' : 'hidden'}>
            <div class="device-entities-list">
              ${entitiesHtml}
            </div>
            ${isEnabled ? '' : '<div class="device-disabled-notice">Disabled: its entities are unavailable in Home Assistant and no RS-485 requests are sent for this device.</div>'}
            ${isR413e16 ? `<div class="device-command-panel"><p>Board communication settings: baud rate and slave ID.</p><button class="btn btn-secondary btn-sm" onclick="openR413e16Controls('${dev.id}')">Open R413E16 settings</button></div>` : ''}
            ${isR4d6f20 ? `<div class="device-command-panel"><p>All relays, per-channel actions, board ID, serial settings, and reset.</p><button class="btn btn-secondary btn-sm" ${isEnabled ? '' : 'disabled'} onclick="openR4d6f20Controls('${dev.id}')">Open R4D6F20 controls</button></div>` : ''}
            ${_r413e16StateReports[dev.id] ? renderR413e16StateReport(_r413e16StateReports[dev.id]) : ''}
            ${_deviceTestReports[dev.id] ? renderDeviceTestReport(_deviceTestReports[dev.id]) : ''}
            </div>
          </div>
        `;
      }).join('');
    }

    function toggleDeviceCard(event, deviceId) {
      if (event?.target?.closest('a, button, input, select, textarea')) return;
      if (_expandedDeviceCards.has(deviceId)) _expandedDeviceCards.delete(deviceId);
      else _expandedDeviceCards.add(deviceId);
      try { localStorage.setItem('modbus_usb_expanded_devices', JSON.stringify([..._expandedDeviceCards])); } catch (_) { /* Expansion still works for this session. */ }
      renderDevicesTab();
    }

    function toggleDeviceCardKey(event, deviceId) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      toggleDeviceCard(event, deviceId);
    }

    function renderDeviceTestReport(report) {
      const summaryClass = report.failed_steps ? 'device-test-fail' : 'device-test-ok';
      const rows = (report.results || []).map(item => {
        if (item.read) {
          const read = item.read.success ? `Read value: ${item.read.value}` : `Read failed: ${item.read.error || 'unknown error'}`;
          return `<li class="${item.read.success ? 'device-test-ok' : 'device-test-fail'}"><strong>${escapeHtml(item.name)}</strong> — ${escapeHtml(read)}</li>`;
        }
        const on = item.on?.success ? 'ON acknowledged' : `ON failed: ${item.on?.error || 'unknown error'}`;
        const off = item.off?.success ? 'OFF acknowledged' : `OFF failed: ${item.off?.error || 'unknown error'}`;
        const rowClass = item.on?.success && item.off?.success ? 'device-test-ok' : 'device-test-fail';
        return `<li class="${rowClass}"><strong>${escapeHtml(item.name)}</strong> — ${escapeHtml(on)}; ${escapeHtml(off)}</li>`;
      }).join('');
      return `<details class="device-test-report" open>
        <summary class="${summaryClass}">Device test report — ${report.successful_steps}/${report.successful_steps + report.failed_steps} requests successful${report.failed_steps ? `, ${report.failed_steps} failed` : ''} (${Math.round(report.duration_ms)} ms)</summary>
        <p style="margin-top:0.55rem; color:var(--text-muted);">Readings test configured sensors, binary sensors, and numbers. Switches turn on briefly, then off. An acknowledgement confirms Modbus communication, not the physical relay position.</p>
        <ul>${rows}</ul>
      </details>`;
    }

    async function runDeviceEntityTest(deviceId) {
      const entry = getCurrentEntry();
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      const deviceEntities = (entry?.entities || []).filter(item => item.device_id === deviceId);
      const switches = deviceEntities.filter(item => item.entity_type === 'switch');
      if (!entry || !device || deviceEntities.length === 0) {
        toast('This device has no configured entities to test', 'err');
        return;
      }
      const switchWarning = switches.length ? `\n\n${switches.length} switch${switches.length === 1 ? '' : 'es'} will turn ON briefly, then OFF. This can activate connected equipment.` : '';
      const warning = `Test all ${deviceEntities.length} configured entities on ${device.name}?\n\nSensors, binary sensors, and numbers will be read once.${switchWarning}`;
      if (!confirm(warning)) return;

      const button = document.getElementById(`device-test-${deviceId}`);
      if (button) { button.disabled = true; button.textContent = '⏳ Testing…'; }
      try {
        const report = await apiCall('test_device_entities', {
          entry_id: entry.entry_id,
          device_id: deviceId,
        });
        _deviceTestReports[deviceId] = report;
        renderDevicesTab();
        toast(report.failed_steps ? 'Device test finished with errors' : 'Device test completed', report.failed_steps ? 'err' : 'ok');
      } catch (err) {
        toast('Device test failed: ' + err.message, 'err');
        if (button) { button.disabled = false; button.textContent = '🧪 Test device'; }
      }
    }

    function openAddDeviceModal(mode = 'template') {
      setAddDeviceMode(mode);
      populateTemplateSelectInAddDevice();
      openModal('modal-add-device');
    }

    function setAddDeviceMode(mode) {
      _addDeviceMode = mode;
      const tplBtn = document.getElementById('btn-mode-template');
      const blkBtn = document.getElementById('btn-mode-blank');
      const tplForm = document.getElementById('form-device-template');
      const blkForm = document.getElementById('form-device-blank');

      if (mode === 'template') {
        tplBtn.className = 'btn btn-primary btn-sm';
        blkBtn.className = 'btn btn-ghost btn-sm';
        tplForm.style.display = 'block';
        blkForm.style.display = 'none';
        document.getElementById('add-device-modal-title').textContent = '⚡ Add Device from YAML Template';
      } else {
        tplBtn.className = 'btn btn-ghost btn-sm';
        blkBtn.className = 'btn btn-primary btn-sm';
        tplForm.style.display = 'none';
        blkForm.style.display = 'block';
        document.getElementById('add-device-modal-title').textContent = '📝 Create Blank Device';
      }
    }

    function populateTemplateSelectInAddDevice(preselectedFilename = null) {
      const sel = document.getElementById('add-dev-tpl-select');
      if (_templates.length === 0) {
        sel.innerHTML = '<option value="">No templates available</option>';
        return;
      }
      sel.innerHTML = _templates.map(t => `
        <option value="${t.filename}" ${(preselectedFilename && t.filename === preselectedFilename) || (!preselectedFilename && t === _templates[0]) ? 'selected' : ''}>
          ${t.name} (${t.filename})
        </option>
      `).join('');

      const activeFilename = preselectedFilename || _templates[0].filename;
      onTemplateSelectionChange(activeFilename);
    }

    function onTemplateSelectionChange(filename) {
      const tpl = _templates.find(t => t.filename === filename) || _templates[0];
      if (!tpl) return;

      document.getElementById('add-dev-tpl-desc').textContent = tpl.description || '';
      document.getElementById('add-dev-name').value = tpl.name || 'New Device';
      document.getElementById('add-dev-slave').value = tpl.default_slave_id || 1;
      const m0Group = document.getElementById('add-dev-m0-group');
      const m0Checkbox = document.getElementById('add-dev-m0-short');
      const m0Label = document.getElementById('add-dev-m0-label');
      const m0Help = document.getElementById('add-dev-m0-help');
      const controls = tpl.device_controls || {};
      const m0 = controls.m0;
      m0Group.style.display = m0 ? 'block' : 'none';
      m0Checkbox.checked = Boolean(tpl.m0_short);
      m0Label.textContent = m0?.label || 'M0 jumper is shorted';
      m0Help.textContent = m0?.description || '';

      // Populate entities checklist
      _selectedTemplateEntities.clear();
      const list = document.getElementById('add-dev-entities-list');
      const entities = tpl.entities || [];

      list.innerHTML = entities.map((e, idx) => {
        _selectedTemplateEntities.add(e.name);
        return `
          <label class="flex items-center gap-1" style="cursor:pointer; font-size:0.75rem; padding:0.25rem 0.4rem; border-radius:6px; background:rgba(255,255,255,0.02);">
            <input type="checkbox" checked onchange="toggleTemplateEntity('${e.name}', this.checked)" />
            <span style="font-weight:600; color:#e2e8f0;">${e.name}</span>
            <span class="mono" style="color:var(--text-dim); margin-left:auto;">${e.entity_type} • ${e.register_type}@${e.address}</span>
          </label>
        `;
      }).join('');

      document.getElementById('tpl-selected-count').textContent = _selectedTemplateEntities.size;
    }

    function toggleTemplateEntity(name, checked) {
      if (checked) _selectedTemplateEntities.add(name);
      else _selectedTemplateEntities.delete(name);
      document.getElementById('tpl-selected-count').textContent = _selectedTemplateEntities.size;
    }

    function toggleAllTemplateEntities(selectAll) {
      const inputs = document.querySelectorAll('#add-dev-entities-list input[type="checkbox"]');
      inputs.forEach(i => {
        i.checked = selectAll;
        const name = i.parentElement.querySelector('span').textContent;
        if (selectAll) _selectedTemplateEntities.add(name);
        else _selectedTemplateEntities.delete(name);
      });
      document.getElementById('tpl-selected-count').textContent = _selectedTemplateEntities.size;
    }

    async function submitAddDevice() {
      const entry = getCurrentEntry();
      if (!entry) return;

      if (_addDeviceMode === 'template') {
        const sel = document.getElementById('add-dev-tpl-select').value;
        const devName = document.getElementById('add-dev-name').value.trim();
        const slaveId = parseInt(document.getElementById('add-dev-slave').value, 10);
        const offset = parseInt(document.getElementById('add-dev-offset').value || 0, 10);
        const m0Short = document.getElementById('add-dev-m0-group').style.display !== 'none' && document.getElementById('add-dev-m0-short').checked;

        if (!devName) { toast('Please enter a device name', 'err'); return; }

        try {
          const template = _templates.find(item => item.filename === sel);
          const m0Config = template?.device_controls?.m0 || {};
          const m0SaveCommand = m0Config.save_command;
          const installM0Profile = Boolean(m0Config.install_profile_on_create);
          const response = await apiCall('apply_template', {
            entry_id: entry.entry_id,
            template_filename: sel,
            device_name: devName,
            slave_id: slaveId,
            address_offset: offset,
            // A profile-changing M0 template starts in its default entity map,
            // then runs the template-declared mode command below.
            m0_short: installM0Profile ? false : m0Short,
            selected_entities: Array.from(_selectedTemplateEntities)
          });
          if (m0Short && installM0Profile && m0SaveCommand) {
            await apiCall(m0SaveCommand, { entry_id: entry.entry_id, device_id: response.device_id, m0_short: true });
          }
          toast(`Device "${devName}" created from template!`, 'ok');
          closeModal('modal-add-device');
          await refreshData();
        } catch(e) {
          toast('Failed to create device: ' + e.message, 'err');
        }
      } else {
        // Blank device
        const name = document.getElementById('blank-dev-name').value.trim();
        const slave = parseInt(document.getElementById('blank-dev-slave').value, 10);
        const mfr = document.getElementById('blank-dev-mfr').value.trim();
        const model = document.getElementById('blank-dev-model').value.trim();
        const desc = document.getElementById('blank-dev-desc').value.trim();

        if (!name) { toast('Please enter a device name', 'err'); return; }

        try {
          await apiCall('save_device', {
            entry_id: entry.entry_id,
            device: {
              name: name,
              slave_id: slave,
              manufacturer: mfr || 'Modbus USB',
              model: model || 'Modbus Device',
              description: desc
            }
          });
          toast(`Device "${name}" created!`, 'ok');
          closeModal('modal-add-device');
          await refreshData();
        } catch(e) {
          toast('Failed to create device: ' + e.message, 'err');
        }
      }
    }

    function useTemplateToCreateDevice(filename) {
      openAddDeviceModal('template');
      populateTemplateSelectInAddDevice(filename);
    }

    async function toggleDeviceEnabled(deviceId, enabled) {
      const entry = getCurrentEntry();
      const device = (entry?.devices || []).find(item => item.id === deviceId);
      if (!entry || !device) return;
      if (!enabled && !confirm(`Disable "${device.name}"?\n\nIts entities will become unavailable in Home Assistant and no requests will be sent to this device.`)) return;
      const button = document.getElementById(`device-enabled-${deviceId}`);
      if (button) {
        button.disabled = true;
        button.textContent = enabled ? 'Enabling…' : 'Disabling…';
      }
      try {
        await apiCall('set_device_enabled', { entry_id: entry.entry_id, device_id: deviceId, enabled });
        toast(enabled ? 'Device enabled. Polling resumed.' : 'Device disabled. Polling stopped.', 'ok');
        await refreshData();
      } catch (error) {
        toast(`Could not ${enabled ? 'enable' : 'disable'} device: ${error.message}`, 'err');
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = enabled ? 'Enable device' : 'Disable device';
        }
      }
    }

    // ─── EDIT DEVICE MODAL ──────────────────────────────────────
    function openEditDeviceModal(devId) {
      const entry = getCurrentEntry();
      const dev = (entry?.devices || []).find(d => d.id === devId);
      if (!dev) return;

      document.getElementById('edit-dev-id').value = dev.id;
      document.getElementById('edit-dev-name').value = dev.name || '';
      document.getElementById('edit-dev-slave').value = dev.slave_id != null ? dev.slave_id : 1;
      document.getElementById('edit-dev-model').value = dev.model || '';
      document.getElementById('edit-dev-mfr').value = dev.manufacturer || '';
      document.getElementById('edit-dev-desc').value = dev.description || '';

      openModal('modal-edit-device');
    }

    async function submitEditDevice() {
      const entry = getCurrentEntry();
      const devId = document.getElementById('edit-dev-id').value;
      const name = document.getElementById('edit-dev-name').value.trim();
      const slave = parseInt(document.getElementById('edit-dev-slave').value, 10);
      const model = document.getElementById('edit-dev-model').value.trim();
      const mfr = document.getElementById('edit-dev-mfr').value.trim();
      const desc = document.getElementById('edit-dev-desc').value.trim();

      if (!name) { toast('Please enter a device name', 'err'); return; }

      try {
        await apiCall('save_device', {
          entry_id: entry.entry_id,
          device: {
            id: devId,
            name: name,
            slave_id: slave,
            model: model,
            manufacturer: mfr,
            description: desc
          }
        });
        toast(`Device "${name}" updated!`, 'ok');
        closeModal('modal-edit-device');
        await refreshData();
      } catch(e) {
        toast('Failed to save device: ' + e.message, 'err');
      }
    }

    async function confirmDeleteDevice(devId, devName) {
      if (!confirm(`Delete device "${devName}" and all its sensors/switches?`)) return;
      const entry = getCurrentEntry();
      try {
        await apiCall('delete_device', {
          entry_id: entry.entry_id,
          device_id: devId,
          delete_entities: true
        });
        toast(`Device "${devName}" deleted.`, 'ok');
        await refreshData();
      } catch(e) {
        toast('Failed to delete device: ' + e.message, 'err');
      }
    }

    // ─── LIVE SWITCH & NUMBER INTERACTIONS ───────────────────────
    async function toggleEntitySwitch(entityId, isOn) {
      const entry = getCurrentEntry();
      const entity = entry?.entities?.find(item => item.id === entityId);
      const name = entity?.name || 'Switch';
      if (!_isLiveHA) {
        toast(`${name} switched ${isOn ? 'ON' : 'OFF'} (simulated)`, 'ok');
        return;
      }
      try {
        if (entity?.ha_entity_id) {
          await _hass.callService('switch', isOn ? 'turn_on' : 'turn_off', {
            entity_id: entity.ha_entity_id,
          });
        } else {
          await apiCall('write_entity', {
            entry_id: entry.entry_id,
            entity_id: entityId,
            state: isOn,
          });
        }
        toast(`${name} turned ${isOn ? 'on' : 'off'}`, 'ok');
        setTimeout(refreshData, 250);
      } catch(e) {
        toast(`Toggle failed: ${e.message}`, 'err');
      }
    }

    async function promptWriteNumber(entId, entName, currentVal) {
      const newVal = prompt(`Enter new value for "${entName}":`, currentVal);
      if (newVal === null) return;
      const num = parseFloat(newVal);
      if (isNaN(num)) { toast('Invalid number', 'err'); return; }

      if (!_isLiveHA) {
        toast(`${entName} set to ${num} (simulated)`, 'ok');
        return;
      }
      const slug = entName.toLowerCase().replace(/[^a-z0-9]+/g, '_');
      try {
        await _hass.callService('number', 'set_value', { entity_id: `number.${slug}`, value: num });
        toast(`${entName} set to ${num}`, 'ok');
        setTimeout(refreshData, 1000);
      } catch(e) {
        toast(`Failed to set value: ${e.message}`, 'err');
      }
    }

    // ─── DIAGNOSTICS LIVE READ ──────────────────────────────────
    async function doLiveRead() {
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('diag-slave').value, 10);
      const address = parseInt(document.getElementById('diag-address').value, 10);
      const regtype = document.getElementById('diag-regtype').value;
      const dtype = document.getElementById('diag-dtype').value;

      const resBox = document.getElementById('diag-result-box');
      resBox.style.color = '#93c5fd';
      resBox.textContent = `Reading ${regtype} @ address ${address} (slave ${slave})…`;

      if (!_isLiveHA) {
        setTimeout(() => {
          resBox.style.color = '#34d399';
          resBox.textContent = `✅ [Simulated] Read register ${address} (${regtype}, ${dtype}, slave=${slave}) = 230.4`;
        }, 500);
        return;
      }

      try {
        const result = await apiCall('diagnostic_read', {
          entry_id: entry.entry_id,
          address: address,
          register_type: regtype,
          data_type: dtype,
          slave_id: slave
        });
        resBox.style.color = '#34d399';
        resBox.textContent = `✅ Response received: ${result.value} (${regtype} ${address}, slave ${slave})`;
        setTimeout(refreshData, 200);
      } catch(e) {
        resBox.style.color = '#f87171';
        resBox.textContent = `❌ Read failed: ${e.message}`;
      }
    }

    async function doLiveWrite() {
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('diag-slave').value, 10);
      const address = parseInt(document.getElementById('diag-address').value, 10);
      const regtype = document.getElementById('diag-regtype').value;
      const value = parseInt(document.getElementById('diag-write-value').value, 10);
      const resBox = document.getElementById('diag-result-box');
      if (!['holding', 'coil'].includes(regtype)) {
        resBox.style.color = '#f87171';
        resBox.textContent = '❌ Writes are available only for Holding Register or Coil.';
        return;
      }
      if (![slave, address, value].every(Number.isFinite)) {
        resBox.style.color = '#f87171';
        resBox.textContent = '❌ Enter a valid slave ID, address, and write value.';
        return;
      }
      if (!window.confirm(`Send documented custom command to slave ${slave}: ${regtype} address ${address}, value ${value}? This can change board settings or connected equipment.`)) return;
      resBox.style.color = '#93c5fd';
      resBox.textContent = `Writing ${value} to ${regtype} ${address} (slave ${slave})…`;
      if (!_isLiveHA) {
        resBox.style.color = '#34d399';
        resBox.textContent = `✅ [Simulated] Wrote ${value} to ${regtype} ${address} (slave ${slave})`;
        return;
      }
      try {
        await apiCall('diagnostic_write', {
          entry_id: entry.entry_id, address, register_type: regtype, value, slave_id: slave,
        });
        resBox.style.color = '#34d399';
        resBox.textContent = `✅ Write sent: ${value} → ${regtype} ${address} (slave ${slave})`;
        setTimeout(refreshData, 200);
      } catch (error) {
        resBox.style.color = '#f87171';
        resBox.textContent = `❌ Write failed: ${error.message}`;
      }
    }
