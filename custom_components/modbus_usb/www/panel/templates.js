/* templates.js — Templates tab: browsing, applying, editing, and deleting templates.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 2: RENDER TEMPLATES ────────────────────────────────
    function renderTemplatesTab() {
      const container = document.getElementById('template-cards-container');
      document.getElementById('count-templates').textContent = _templates.length;
      const openBrands = new Set(
        [...container.querySelectorAll('.template-brand-group[open]')]
          .map(group => group.dataset.brand)
          .filter(Boolean),
      );

      if (_templates.length === 0) {
        container.innerHTML = `
          <div class="empty-box card" style="grid-column:1/-1">
            <div class="empty-icon">📑</div>
            <h3>No Templates Found</h3>
            <p>Create your first device template YAML file to quickly configure new sensors and switches.</p>
            <button class="btn btn-primary" onclick="openTemplateEditorModal(null)">➕ Create Template</button>
          </div>
        `;
        return;
      }

      const templates = [..._templates].sort((left, right) => {
        const leftSaved = left.source === 'user' ? 0 : 1;
        const rightSaved = right.source === 'user' ? 0 : 1;
        if (leftSaved !== rightSaved) return leftSaved - rightSaved;
        return String(left.name || left.filename || '').localeCompare(
          String(right.name || right.filename || ''), undefined, { sensitivity: 'base' },
        );
      });

      const templateCard = (tpl) => {
        const entCount = (tpl.entities || []).length;
        const previewItems = (tpl.entities || []).slice(0, 5).map(e => `
          <div class="preview-pill">
            <span style="font-weight:600; color:#cbd5e1;">${e.name}</span>
            <span class="mono" style="color:var(--text-dim);">${e.entity_type} • ${e.register_type}@${e.address}</span>
          </div>
        `).join('');

        const extraCount = entCount > 5 ? `<div style="text-align:center; font-size:0.68rem; color:var(--text-dim); padding-top:2px;">+ ${entCount - 5} more entities</div>` : '';
        const isSavedInHa = tpl.source === 'user';
        const validationBadge = tpl.status === 'testing'
          ? '<span class="badge badge-amber" title="Currently being tested with this integration">◌ Testing</span>'
          : tpl.tested
            ? '<span class="badge badge-green" title="Confirmed working with this integration">✓ Tested & confirmed</span>'
            : '<span class="badge badge-amber" title="Not yet confirmed with this integration">○ Not yet tested</span>';

        return `
          <div class="template-card">
            <div>
              ${tpl.image ? (tpl.info_url ? `<a href="${tpl.info_url}" target="_blank" rel="noopener noreferrer" title="Open device information"><img class="template-photo" src="${tpl.image}" alt="${tpl.name || tpl.id}" width="640" height="280" loading="lazy" decoding="async"></a>` : `<img class="template-photo" src="${tpl.image}" alt="${tpl.name || tpl.id}" width="640" height="280" loading="lazy" decoding="async">`) : ''}
              <div class="template-header">
                <div class="template-title">
                  <h3>${tpl.name || tpl.id}</h3>
                  <div class="template-filename">${tpl.filename}</div>
                </div>
                <div class="flex gap-1" style="flex-direction:column; align-items:flex-end;">
                  ${isSavedInHa ? '<span class="badge badge-blue">Saved in HA</span>' : ''}
                  ${validationBadge}
                  <span class="badge badge-purple mono">Default Slave: ${tpl.default_slave_id || 1}</span>
                  <span class="badge badge-blue">${entCount} entities</span>
                </div>
              </div>

              <div class="template-desc">${tpl.description || 'Pre-configured Modbus registers and entity mappings'}</div>
              ${tpl.info_url ? `<a href="${tpl.info_url}" target="_blank" rel="noopener noreferrer" class="text-sm" style="color:#60a5fa;">↗ Device information</a>` : ''}

              <div class="template-entities-preview">
                ${previewItems || '<div style="color:var(--text-dim); font-size:0.7rem; text-align:center;">No entities defined in template</div>'}
                ${extraCount}
              </div>
            </div>

            <div class="template-actions">
              <button class="btn btn-primary btn-sm" onclick="useTemplateToCreateDevice('${tpl.filename}')" title="Create a new device using this template">
                🚀 Use Template
              </button>
              ${isSavedInHa
                ? `<button class="btn btn-secondary btn-sm" onclick="openTemplateEditorModal('${tpl.filename}')" title="Edit the copy saved in Home Assistant">✏ Edit YAML</button>`
                : `<button class="btn btn-secondary btn-sm" onclick="saveTemplateToHomeAssistant('${tpl.filename}')" title="Save this template to Home Assistant">⇩ Save to HA</button>`}
              <button class="btn btn-ghost btn-sm" onclick="duplicateTemplate('${tpl.filename}')" title="Duplicate template">
                📋 Duplicate
              </button>
              ${isSavedInHa ? `<button class="btn btn-danger btn-sm" onclick="confirmDeleteTemplate('${tpl.filename}')" title="Delete the saved YAML file">🗑 Delete</button>` : ''}
            </div>
          </div>
        `;
      };

      const brands = new Map();
      templates.forEach((tpl) => {
        const brand = String(tpl.manufacturer || 'Other').trim() || 'Other';
        if (!brands.has(brand)) brands.set(brand, []);
        brands.get(brand).push(tpl);
      });

      const orderedBrands = [...brands.entries()].sort(([leftBrand, leftTemplates], [rightBrand, rightTemplates]) => {
        const leftSaved = leftTemplates.some(tpl => tpl.source === 'user') ? 0 : 1;
        const rightSaved = rightTemplates.some(tpl => tpl.source === 'user') ? 0 : 1;
        if (leftSaved !== rightSaved) return leftSaved - rightSaved;
        return leftBrand.localeCompare(rightBrand, undefined, { sensitivity: 'base' });
      });

      container.innerHTML = orderedBrands.map(([brand, brandTemplates]) => {
        const savedCount = brandTemplates.filter(tpl => tpl.source === 'user').length;
        const countText = `${brandTemplates.length} template${brandTemplates.length === 1 ? '' : 's'}${savedCount ? ` · ${savedCount} saved in HA` : ''}`;
        const isOpen = openBrands.has(brand) ? ' open' : '';
        const safeBrand = escapeHtml(brand);
        return `
          <details class="template-brand-group" data-brand="${safeBrand}"${isOpen}>
            <summary class="template-brand-summary" aria-label="Show ${safeBrand} templates">
              <span class="template-brand-summary-copy">
                <span class="template-brand-name">${safeBrand}</span>
                <span class="template-brand-meta">${countText}</span>
              </span>
              <svg class="template-brand-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4"/></svg>
            </summary>
            <div class="template-brand-content">
              <div class="template-grid">${brandTemplates.map(templateCard).join('')}</div>
            </div>
          </details>
        `;
      }).join('');
    }

    async function saveTemplateToHomeAssistant(filename) {
      const template = _templates.find(item => item.filename === filename);
      if (!template?.raw_yaml) {
        toast('Template content is not available to save', 'err');
        return;
      }
      try {
        await apiCall('save_template', {
          filename: template.filename,
          content: template.raw_yaml
        });
        toast(`Saved ${template.filename} in Home Assistant`, 'ok');
        await refreshData();
      } catch (e) {
        toast('Failed to save template: ' + e.message, 'err');
      }
    }

    function openApplyTemplateModal(devId) {
      const entry = getCurrentEntry();
      const dev = (entry?.devices || []).find(d => d.id === devId);
      if (!dev) return;

      document.getElementById('apply-tpl-target-device-id').value = dev.id;
      document.getElementById('apply-tpl-device-label').textContent = `${dev.name} (Slave ID: ${dev.slave_id || 1})`;

      const sel = document.getElementById('apply-tpl-select');
      sel.innerHTML = _templates.map((t, idx) => `
        <option value="${t.filename}" ${idx === 0 ? 'selected' : ''}>
          ${t.name} (${t.filename})
        </option>
      `).join('');

      if (_templates.length > 0) {
        onApplyTemplateChange(_templates[0].filename);
      }
      openModal('modal-apply-template');
    }

    function onApplyTemplateChange(filename) {
      const tpl = _templates.find(t => t.filename === filename);
      if (!tpl) return;
      _selectedApplyEntities.clear();
      const list = document.getElementById('apply-tpl-entities-list');
      list.innerHTML = (tpl.entities || []).map(e => {
        _selectedApplyEntities.add(e.name);
        return `
          <label class="flex items-center gap-1" style="cursor:pointer; font-size:0.75rem; padding:0.25rem 0.4rem; border-radius:6px; background:rgba(255,255,255,0.02);">
            <input type="checkbox" checked onchange="toggleApplyEntity('${e.name}', this.checked)" />
            <span style="font-weight:600; color:#e2e8f0;">${e.name}</span>
            <span class="mono" style="color:var(--text-dim); margin-left:auto;">${e.entity_type} • ${e.register_type}@${e.address}</span>
          </label>
        `;
      }).join('');
    }

    function toggleApplyEntity(name, checked) {
      if (checked) _selectedApplyEntities.add(name);
      else _selectedApplyEntities.delete(name);
    }

    function toggleAllApplyTemplateEntities(selectAll) {
      const inputs = document.querySelectorAll('#apply-tpl-entities-list input[type="checkbox"]');
      inputs.forEach(i => {
        i.checked = selectAll;
        const name = i.parentElement.querySelector('span').textContent;
        if (selectAll) _selectedApplyEntities.add(name);
        else _selectedApplyEntities.delete(name);
      });
    }

    async function submitApplyTemplate() {
      const entry = getCurrentEntry();
      const devId = document.getElementById('apply-tpl-target-device-id').value;
      const sel = document.getElementById('apply-tpl-select').value;
      const offset = parseInt(document.getElementById('apply-tpl-offset').value || 0, 10);

      try {
        const res = await apiCall('apply_template', {
          entry_id: entry.entry_id,
          device_id: devId,
          template_filename: sel,
          address_offset: offset,
          selected_entities: Array.from(_selectedApplyEntities)
        });
        toast(`Added ${res.added_count || _selectedApplyEntities.size} entities to device!`, 'ok');
        closeModal('modal-apply-template');
        await refreshData();
      } catch(e) {
        toast('Failed to apply template: ' + e.message, 'err');
      }
    }

    // ─── YAML TEMPLATE EDITOR MODAL ─────────────────────────────
    function openTemplateEditorModal(filename = null) {
      const tpl = filename ? _templates.find(t => t.filename === filename) : null;
      const title = document.getElementById('tpl-editor-title');
      const fnInput = document.getElementById('tpl-editor-filename');
      const codeArea = document.getElementById('tpl-editor-code');

      if (tpl) {
        title.textContent = `📑 Edit Template: ${tpl.filename}`;
        fnInput.value = tpl.filename;
        fnInput.readOnly = true;
        codeArea.value = tpl.raw_yaml || '';
      } else {
        title.textContent = `➕ New Device Template (.yaml)`;
        fnInput.value = 'my_device.yaml';
        fnInput.readOnly = false;
        codeArea.value = `id: custom_device
name: My Custom Modbus Device
manufacturer: Generic
model: Modbus Device
default_slave_id: 1
description: Template for custom Modbus sensors and controls
entities:
  - name: Status Register
    entity_type: sensor
    register_type: holding
    address: 0
    data_type: uint16
    unit_of_measurement: ""
  - name: Power Switch
    entity_type: switch
    register_type: coil
    address: 0
  - name: Target Value
    entity_type: number
    register_type: holding
    address: 1
    data_type: uint16
    min_value: 0
    max_value: 100
    step: 1
`;
      }

      openModal('modal-template-editor');
    }

    async function submitSaveTemplate() {
      const filename = document.getElementById('tpl-editor-filename').value.trim();
      const content = document.getElementById('tpl-editor-code').value;

      if (!filename) { toast('Please specify a filename (e.g. my_device.yaml)', 'err'); return; }

      try {
        await apiCall('save_template', {
          filename: filename,
          content: content
        });
        toast(`Template "${filename}" saved successfully!`, 'ok');
        closeModal('modal-template-editor');
        await refreshData();
      } catch(e) {
        toast('Failed to save template: ' + e.message, 'err');
      }
    }

    async function duplicateTemplate(filename) {
      const tpl = _templates.find(t => t.filename === filename);
      if (!tpl) return;
      const newFn = prompt('Enter filename for the duplicate template:', 'copy_' + filename);
      if (!newFn) return;
      try {
        await apiCall('save_template', {
          filename: newFn,
          content: tpl.raw_yaml
        });
        toast(`Template duplicated as "${newFn}"`, 'ok');
        await refreshData();
      } catch(e) {
        toast('Failed to duplicate template: ' + e.message, 'err');
      }
    }

    async function confirmDeleteTemplate(filename) {
      if (!confirm(`Delete template file "${filename}"? This action cannot be undone.`)) return;
      try {
        await apiCall('delete_template', { filename: filename });
        toast(`Template "${filename}" deleted.`, 'ok');
        await refreshData();
      } catch(e) {
        toast('Failed to delete template: ' + e.message, 'err');
      }
    }
