/* designer.js — Template Designer tab: live validation before saving YAML.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Structure validation and live test reads run on the backend
 * (custom_components/modbus_usb/designer.py); this module renders the report.
 */
    let _designerResult = null;
    let _designerInitialized = false;

    const DESIGNER_SAMPLE_YAML = `name: My Custom Meter
manufacturer: Custom
model: DIY Meter
default_slave_id: 1
description: Draft template created in the Template Designer
entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
    scale: 1
    unit_of_measurement: "V"
    device_class: voltage
  - name: Temperature
    entity_type: sensor
    register_type: holding
    address: 1
    data_type: int16
    scale: 0.1
    unit_of_measurement: "\u00B0C"
    device_class: temperature
  - name: Relay 1
    entity_type: switch
    register_type: coil
    address: 0
`;

    function initDesignerTab() {
      const textarea = document.getElementById('designer-yaml');
      if (!textarea || _designerInitialized) return;
      _designerInitialized = true;
      if (!textarea.value.trim()) textarea.value = DESIGNER_SAMPLE_YAML;
    }

    function loadDesignerSample() {
      const textarea = document.getElementById('designer-yaml');
      if (!textarea) return;
      textarea.value = DESIGNER_SAMPLE_YAML;
      _designerResult = null;
      renderDesignerResults();
      toast('Sample template loaded — run a validation to test it', 'ok');
    }

    async function runDesignerValidation() {
      const textarea = document.getElementById('designer-yaml');
      const button = document.getElementById('btn-designer-validate');
      const entry = getCurrentEntry();
      if (!textarea || !button) return;
      if (!entry) {
        toast('Configure a hub before validating templates', 'err');
        return;
      }
      const content = textarea.value;
      if (!content.trim()) {
        toast('Paste a template YAML first', 'err');
        return;
      }
      const slaveInput = document.getElementById('designer-slave-id');
      const slaveId = slaveInput && slaveInput.value ? parseInt(slaveInput.value, 10) : null;
      const testReads = document.getElementById('designer-test-reads')?.checked !== false;

      button.disabled = true;
      button.textContent = 'Validating…';
      try {
        _designerResult = await apiCall('designer_validate', {
          entry_id: entry.entry_id,
          content,
          ...(slaveId ? { slave_id: slaveId } : {}),
          test_reads: testReads,
        });
        renderDesignerResults();
        if (_designerResult?.valid && _designerResult.failed === 0) {
          toast(`Validation passed: ${_designerResult.passed} register read(s) verified`, 'ok');
        } else if (_designerResult?.valid) {
          toast(`Validation finished with ${_designerResult.failed} failed read(s)`, 'err');
        } else {
          toast('Template structure is invalid — see the report', 'err');
        }
      } catch (error) {
        _designerResult = { valid: false, error: error.message, entities: [] };
        renderDesignerResults();
        toast(`Validation failed: ${error.message}`, 'err');
      } finally {
        button.disabled = false;
        button.textContent = '▶ Validate & test reads';
      }
    }

    function designerEntityCard(entity) {
      const statusBadge = entity.status === 'pass'
        ? '<span class="badge badge-green">pass</span>'
        : entity.status === 'fail'
          ? '<span class="badge badge-red">fail</span>'
          : '<span class="badge badge-slate">skipped</span>';
      const decodings = Object.entries(entity.decodings || {})
        .map(([dataType, value]) => {
          const declared = dataType === entity.data_type ? ' declared' : '';
          return `<tr class="${declared ? 'designer-declared-row' : ''}"><td class="mono">${escapeHtml(dataType)}${declared ? ' ★' : ''}</td><td class="mono">${escapeHtml(typeof value === 'number' ? String(value) : value)}</td></tr>`;
        }).join('');
      const rawWords = Array.isArray(entity.raw_words)
        ? entity.raw_words.map((word) => `0x${word.toString(16).toUpperCase().padStart(4, '0')}`).join(' ')
        : '';
      return `<div class="designer-entity-card">
        <div class="designer-entity-head">
          <div>
            <span class="designer-entity-name">${escapeHtml(entity.name)}</span>
            <span class="badge badge-slate mono">${escapeHtml(entity.register_type)} @ ${entity.address} · ${escapeHtml(entity.data_type)} · ${entity.word_count} word(s)</span>
          </div>
          ${statusBadge}
        </div>
        ${entity.success ? `
          <div class="designer-entity-value">Decoded value: <span class="mono">${escapeHtml(String(entity.value))}</span>${entity.scaled ? ' <span class="badge badge-blue">scaled</span>' : ''}</div>
          ${rawWords ? `<div class="text-sm mono" style="color:var(--text-dim);">raw: ${escapeHtml(rawWords)}</div>` : ''}
          ${decodings ? `<table class="designer-decodings"><thead><tr><th>Data type</th><th>Decoded</th></tr></thead><tbody>${decodings}</tbody></table>` : ''}
        ` : ''}
        ${entity.error ? `<div class="frame-errors"><div>❌ ${escapeHtml(entity.error)}</div></div>` : ''}
      </div>`;
    }

    function renderDesignerResults() {
      const container = document.getElementById('designer-results');
      const subtitle = document.getElementById('designer-results-subtitle');
      if (!container) return;
      const result = _designerResult;
      if (!result) {
        subtitle.textContent = 'Run a validation to test every register against the board.';
        container.innerHTML = '<div class="empty-box"><div class="empty-icon">🧪</div><h3>Nothing validated yet</h3></div>';
        return;
      }
      if (!result.valid) {
        subtitle.textContent = 'The draft template could not be validated.';
        container.innerHTML = `<div class="frame-errors"><div>❌ ${escapeHtml(result.error || 'Unknown validation error')}</div></div>`;
        return;
      }

      subtitle.textContent = `Slave ${result.slave_id} · ${result.passed} passed · ${result.failed} failed`
        + (result.skipped ? ` · ${result.skipped} skipped` : '')
        + ` · ${formatMs(result.duration_ms)}`;
      const verdict = result.all_passed
        ? '<span class="badge badge-green">All test reads passed — safe to save</span>'
        : result.test_reads
          ? '<span class="badge badge-red">Some test reads failed — fix the mapping before saving</span>'
          : '<span class="badge badge-amber">Structure only — test reads were disabled</span>';
      const truncatedNote = result.truncated
        ? '<div class="text-sm" style="color:var(--text-dim);">Only the first 64 entities are test-read.</div>'
        : '';
      container.innerHTML = `
        <div class="designer-summary">${verdict}${truncatedNote}</div>
        ${(result.entities || []).map(designerEntityCard).join('') || '<div class="text-sm" style="color:var(--text-dim);">This template defines no entities.</div>'}
      `;
    }

    async function saveDesignerTemplate() {
      const textarea = document.getElementById('designer-yaml');
      const filenameInput = document.getElementById('designer-filename');
      if (!textarea || !filenameInput) return;
      const content = textarea.value;
      if (!content.trim()) {
        toast('Paste a template YAML first', 'err');
        return;
      }
      let filename = filenameInput.value.trim();
      if (!filename) {
        const nameMatch = content.match(/^name:\s*(.+)$/m);
        filename = `${(nameMatch ? nameMatch[1] : 'custom_template').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'custom_template'}.yaml`;
      }
      const validated = _designerResult?.valid && (_designerResult.failed === 0 || !_designerResult.test_reads);
      if (!validated && !window.confirm('This draft has not passed live validation. Save it anyway?')) {
        return;
      }
      try {
        await apiCall('save_template', { filename, content });
        toast(`Template saved as ${filename}`, 'ok');
        await refreshData();
      } catch (error) {
        toast(`Could not save template: ${error.message}`, 'err');
      }
    }
