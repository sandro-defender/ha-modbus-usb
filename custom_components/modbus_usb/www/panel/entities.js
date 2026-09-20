/* entities.js — Entity add/edit modals and entity CRUD.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 4: RENDER ENTITIES TABLE ───────────────────────────
    function renderEntitiesTab() {
      const entry = getCurrentEntry();
      const tbody = document.getElementById('entities-table-tbody');
      const devices = entry ? (entry.devices || []) : [];
      const entities = entry ? (entry.entities || []) : [];

      document.getElementById('count-entities').textContent = entities.length;

      if (entities.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:2rem; color:var(--text-dim);">No entities configured yet.</td></tr>`;
        return;
      }

      tbody.innerHTML = entities.map(ent => {
        const dev = devices.find(d => d.id === ent.device_id);
        const type = ent.entity_type || 'sensor';
        const pillClass = { sensor: 'badge-blue', switch: 'badge-purple', binary_sensor: 'badge-amber', number: 'badge-green' }[type] || 'badge-blue';
        const state = getLiveState(ent, dev);
        const val = state ? state.state : '—';
        const unit = ent.unit_of_measurement || '';

        return `
          <tr>
            <td><span class="badge badge-slate">${dev ? dev.name : '—'}</span></td>
            <td style="font-weight:600; color:#fff;">${ent.name}</td>
            <td><span class="badge ${pillClass}">${type}</span></td>
            <td class="mono">${ent.register_type || 'holding'}</td>
            <td class="mono">${ent.address ?? '0'}</td>
            <td class="mono">${ent.data_type || (ent.register_type === 'holding' ? 'uint16' : (type === 'switch' || type === 'binary_sensor' ? 'bit' : 'uint16'))}</td>
            <td class="mono">${ent.scale != null && ent.scale !== 1 ? ent.scale + ' × ' : ''}${unit || '—'}</td>
            <td class="mono" style="font-weight:700; color:#93c5fd;">${val} ${unit}</td>
            <td>
              <div class="flex gap-1">
                <button class="btn btn-ghost btn-sm" onclick="openEditEntityModal('${ent.id}')" title="Edit">✏</button>
                <button class="btn btn-danger btn-sm" onclick="confirmDeleteEntity('${ent.id}', '${ent.name}')" title="Delete">🗑</button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    // ─── ADD / EDIT ENTITY MODAL ────────────────────────────────
    function openAddEntityModal(preselectedDeviceId = null) {
      const entry = getCurrentEntry();
      const devices = entry?.devices || [];

      document.getElementById('entity-modal-title').textContent = '➕ Add Entity';
      document.getElementById('ent-form-id').value = '';
      document.getElementById('ent-form-name').value = '';
      document.getElementById('ent-form-type').value = 'sensor';
      document.getElementById('ent-form-regtype').value = 'holding';
      document.getElementById('ent-form-address').value = '0';
      document.getElementById('ent-form-addresses').value = '';
      document.getElementById('ent-form-dtype').value = 'uint16';
      document.getElementById('ent-form-scale').value = '1';
      document.getElementById('ent-form-unit').value = '';
      document.getElementById('ent-form-devclass').value = '';
      document.getElementById('ent-form-stateclass').value = '';

      const devSel = document.getElementById('ent-form-device');
      devSel.innerHTML = devices.map(d => `
        <option value="${d.id}" ${(preselectedDeviceId && d.id === preselectedDeviceId) ? 'selected' : ''}>
          ${d.name} (Slave ID: ${d.slave_id || 1})
        </option>
      `).join('');

      onEntityTypeChange('sensor');
      openModal('modal-entity-form');
    }

    function openEditEntityModal(entId) {
      const entry = getCurrentEntry();
      const ent = (entry?.entities || []).find(e => e.id === entId);
      if (!ent) return;

      document.getElementById('entity-modal-title').textContent = '✏ Edit Entity';
      document.getElementById('ent-form-id').value = ent.id;
      document.getElementById('ent-form-name').value = ent.name || '';
      document.getElementById('ent-form-type').value = ent.entity_type || 'sensor';
      document.getElementById('ent-form-regtype').value = ent.register_type || 'holding';
      document.getElementById('ent-form-address').value = ent.address ?? 0;
      document.getElementById('ent-form-addresses').value = Array.isArray(ent.addresses) ? ent.addresses.join(', ') : '';
      document.getElementById('ent-form-dtype').value = ent.data_type || 'uint16';
      document.getElementById('ent-form-scale').value = ent.scale != null ? ent.scale : 1;
      document.getElementById('ent-form-unit').value = ent.unit_of_measurement || '';
      document.getElementById('ent-form-devclass').value = ent.device_class || '';
      document.getElementById('ent-form-stateclass').value = ent.state_class || '';

      const devSel = document.getElementById('ent-form-device');
      const devices = entry?.devices || [];
      devSel.innerHTML = devices.map(d => `
        <option value="${d.id}" ${d.id === ent.device_id ? 'selected' : ''}>
          ${d.name} (Slave ID: ${d.slave_id || 1})
        </option>
      `).join('');

      onEntityTypeChange(ent.entity_type || 'sensor');
      openModal('modal-entity-form');
    }

    function onEntityTypeChange(type) {
      const numFields = document.getElementById('ent-numeric-fields');
      const swFields = document.getElementById('ent-switch-fields');
      const nFields = document.getElementById('ent-number-fields');
      const regTypeSel = document.getElementById('ent-form-regtype');

      if (type === 'sensor') {
        numFields.style.display = 'block';
        swFields.style.display = 'none';
        nFields.style.display = 'none';
        renderSwitchChannelPicker();
      } else if (type === 'switch') {
        numFields.style.display = 'none';
        swFields.style.display = 'block';
        nFields.style.display = 'none';
        if (regTypeSel.value !== 'coil' && regTypeSel.value !== 'holding') {
          regTypeSel.value = 'coil';
        }
        renderSwitchChannelPicker();
      } else if (type === 'binary_sensor') {
        numFields.style.display = 'none';
        swFields.style.display = 'none';
        nFields.style.display = 'none';
        if (regTypeSel.value !== 'discrete' && regTypeSel.value !== 'coil') {
          regTypeSel.value = 'discrete';
        }
        renderSwitchChannelPicker();
      } else if (type === 'number') {
        numFields.style.display = 'block';
        swFields.style.display = 'none';
        nFields.style.display = 'block';
        regTypeSel.value = 'holding';
        renderSwitchChannelPicker();
      }
    }

    async function submitSaveEntity() {
      const entry = getCurrentEntry();
      const entId = document.getElementById('ent-form-id').value;
      const name = document.getElementById('ent-form-name').value.trim();
      const devId = document.getElementById('ent-form-device').value;
      const type = document.getElementById('ent-form-type').value;
      const regtype = document.getElementById('ent-form-regtype').value;
      const address = parseInt(document.getElementById('ent-form-address').value, 10);

      if (!name) { toast('Please enter an entity name', 'err'); return; }

      const entityData = {
        id: entId || undefined,
        device_id: devId,
        name: name,
        entity_type: type,
        register_type: regtype,
        address: address,
      };

      if (type === 'sensor' || type === 'number') {
        entityData.data_type = document.getElementById('ent-form-dtype').value;
        entityData.scale = parseFloat(document.getElementById('ent-form-scale').value || 1);
        entityData.unit_of_measurement = document.getElementById('ent-form-unit').value.trim();
        entityData.device_class = document.getElementById('ent-form-devclass').value.trim() || undefined;
        entityData.state_class = document.getElementById('ent-form-stateclass').value || undefined;
      }
      if (type === 'switch') {
        if (regtype === 'holding') {
          // Holding-register switches carry a 16-bit command, unlike coils.
          entityData.data_type = 'uint16';
        }
        entityData.on_value = parseInt(document.getElementById('ent-form-onval').value || 1, 10);
        entityData.off_value = parseInt(document.getElementById('ent-form-offval').value || 0, 10);
        const groupText = document.getElementById('ent-form-addresses').value.trim();
        if (groupText) {
          const addresses = groupText.split(',').map(value => Number(value.trim()));
          if (!addresses.length || addresses.some(value => !Number.isInteger(value) || value < 0 || value > 65535)) {
            toast('Use comma-separated channel addresses from 0 to 65535', 'err');
            return;
          }
          entityData.addresses = [...new Set(addresses)];
          entityData.address = entityData.addresses[0];
          entityData.assumed_state = true;
        } else entityData.addresses = [];
      }
      if (type === 'number') {
        entityData.min_value = parseFloat(document.getElementById('ent-form-min').value || 0);
        entityData.max_value = parseFloat(document.getElementById('ent-form-max').value || 100);
        entityData.step = parseFloat(document.getElementById('ent-form-step').value || 1);
      }

      try {
        await apiCall('save_entity', {
          entry_id: entry.entry_id,
          entity: entityData
        });
        toast(`Entity "${name}" saved!`, 'ok');
        closeModal('modal-entity-form');
        await refreshData();
      } catch(e) {
        toast('Failed to save entity: ' + e.message, 'err');
      }
    }

    async function confirmDeleteEntity(entId, entName) {
      if (!confirm(`Delete entity "${entName}"?`)) return;
      const entry = getCurrentEntry();
      try {
        await apiCall('delete_entity', {
          entry_id: entry.entry_id,
          entity_id: entId
        });
        toast(`Entity "${entName}" deleted.`, 'ok');
        await refreshData();
      } catch(e) {
        toast('Failed to delete entity: ' + e.message, 'err');
      }
    }
