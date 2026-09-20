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
      if (!textarea) return;
      if (!_designerInitialized) {
        _designerInitialized = true;
        if (!textarea.value.trim()) textarea.value = DESIGNER_SAMPLE_YAML;
        // v2.7.0: syntax-aware editing — line-number gutter, Tab/Shift+Tab
        // indentation, and Ctrl+Enter (or Cmd+Enter) to validate.
        textarea.addEventListener('input', () => updateDesignerLineNumbers());
        textarea.addEventListener('scroll', () => syncDesignerGutterScroll(textarea));
        textarea.addEventListener('keydown', handleDesignerEditorKeydown);
      }
      renderDesignerApplyTargets();
      renderDesignerImportOptions();
      updateDesignerLineNumbers();
    }

    // ─── v2.7.0: line numbers, indentation & template import ────

    function updateDesignerLineNumbers() {
      const textarea = document.getElementById('designer-yaml');
      const gutter = document.getElementById('designer-line-numbers');
      if (!textarea || !gutter) return;
      const lineCount = textarea.value.split('\n').length;
      let numbers = '';
      for (let line = 1; line <= lineCount; line += 1) numbers += `${line}\n`;
      gutter.textContent = numbers;
      syncDesignerGutterScroll(textarea);
    }

    function syncDesignerGutterScroll(textarea) {
      const gutter = document.getElementById('designer-line-numbers');
      if (gutter) gutter.scrollTop = textarea.scrollTop;
    }

    function insertDesignerIndent(textarea) {
      // Two-space indent; a multi-line selection indents every line.
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const value = textarea.value;
      if (start === end) {
        textarea.setRangeText('  ', start, end, 'end');
      } else {
        const lineStart = value.lastIndexOf('\n', start - 1) + 1;
        const indented = value.slice(lineStart, end).split('\n')
          .map((line) => (line ? '  ' + line : line))
          .join('\n');
        textarea.setRangeText(indented, lineStart, end, 'select');
      }
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function outdentDesignerLines(textarea) {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const value = textarea.value;
      const lineStart = value.lastIndexOf('\n', start - 1) + 1;
      const block = value.slice(lineStart, end);
      const outdented = block.split('\n')
        .map((line) => line.replace(/^ {1,2}/, ''))
        .join('\n');
      if (outdented !== block) {
        textarea.setRangeText(outdented, lineStart, end, 'select');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }

    function handleDesignerEditorKeydown(event) {
      const textarea = event.target;
      // Ctrl+Enter (or Cmd+Enter) validates the draft from the editor.
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        runDesignerValidation();
        return;
      }
      if (event.key !== 'Tab') return;
      // A plain textarea would move focus out of the editor; keep Tab as an
      // indent key and Shift+Tab as an outdent key instead.
      event.preventDefault();
      if (event.shiftKey) outdentDesignerLines(textarea);
      else insertDesignerIndent(textarea);
    }

    function renderDesignerImportOptions() {
      const select = document.getElementById('designer-import-template');
      if (!select) return;
      const previous = select.value;
      const seen = new Set();
      const templates = (typeof _templates !== 'undefined' ? _templates : []).filter((tpl) => {
        if (!tpl || tpl.error || !tpl.raw_yaml) return false;
        const key = tpl.filename || tpl.id;
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      select.innerHTML = '<option value="">Select a bundled or saved template…</option>' + templates.map((tpl) =>
        `<option value="${escapeHtml(tpl.filename || tpl.id)}">${escapeHtml(tpl.name || tpl.filename)}${tpl.source ? ` (${escapeHtml(tpl.source)})` : ''}</option>`).join('');
      if (previous && [...select.options].some((option) => option.value === previous)) {
        select.value = previous;
      }
    }

    function importDesignerTemplate() {
      const textarea = document.getElementById('designer-yaml');
      const select = document.getElementById('designer-import-template');
      const filenameInput = document.getElementById('designer-filename');
      if (!textarea || !select) return;
      const template = (typeof _templates !== 'undefined' ? _templates : [])
        .find((tpl) => (tpl.filename || tpl.id) === select.value && !tpl.error && tpl.raw_yaml);
      if (!template) {
        toast('Pick a template to import first', 'err');
        return;
      }
      textarea.value = template.raw_yaml;
      if (filenameInput && !filenameInput.value.trim()) {
        filenameInput.value = template.filename || '';
      }
      _designerResult = null;
      renderDesignerResults();
      updateDesignerLineNumbers();
      textarea.focus();
      toast(`Imported ${template.name || template.filename} into the editor`, 'ok');
    }

    function renderDesignerApplyTargets() {
      const select = document.getElementById('designer-apply-device');
      if (!select) return;
      const entry = getCurrentEntry();
      const devices = entry?.devices || [];
      const previous = select.value;
      select.innerHTML = '<option value="">\u2795 Create new device\u2026</option>' + devices.map((device) =>
        `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name || device.id)} (slave ${escapeHtml(device.slave_id ?? '?')})</option>`).join('');
      if (previous && [...select.options].some((option) => option.value === previous)) {
        select.value = previous;
      }
    }

    function loadDesignerSample() {
      const textarea = document.getElementById('designer-yaml');
      if (!textarea) return;
      textarea.value = DESIGNER_SAMPLE_YAML;
      _designerResult = null;
      renderDesignerResults();
      updateDesignerLineNumbers();
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
        + (result.fingerprint_total ? ` · fingerprint ${result.fingerprint_matched}/${result.fingerprint_total}` : '')
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
        ${designerFingerprintSection(result)}
        ${(result.entities || []).map(designerEntityCard).join('') || '<div class="text-sm" style="color:var(--text-dim);">This template defines no entities.</div>'}
      `;
    }

    function designerFingerprintSection(result) {
      if (!result.test_reads) return '';
      const probes = result.fingerprint || [];
      if (!probes.length) {
        return '<div class="text-sm" style="color:var(--text-dim); margin-bottom:0.75rem;">This template declares no fingerprint probes — add a <code class="mono">fingerprint:</code> block so the RS-485 scanner can recognize the board.</div>';
      }
      const verdict = result.fingerprint_all_matched
        ? '<span class="badge badge-green">Fingerprint matched — the connected board looks like this template</span>'
        : '<span class="badge badge-amber">Fingerprint did not fully match — check probe addresses and ranges</span>';
      const rows = probes.map((probe) => {
        const badge = probe.status === 'match'
          ? '<span class="badge badge-green">match</span>'
          : probe.status === 'mismatch'
            ? '<span class="badge badge-amber">no match</span>'
            : '<span class="badge badge-red">error</span>';
        const value = typeof probe.value === 'number' || typeof probe.value === 'boolean'
          ? String(probe.value)
          : '\u2014';
        return `<div class="designer-entity-card">
          <div class="designer-entity-head">
            <div>
              <span class="designer-entity-name mono">${escapeHtml(probe.register_type || '?')} @ ${escapeHtml(probe.address ?? '?')}</span>
              <span class="badge badge-slate mono">${escapeHtml(probe.data_type || 'uint16')} \u00B7 expected ${escapeHtml(probe.min_value ?? '?')} \u2026 ${escapeHtml(probe.max_value ?? '?')}</span>
            </div>
            ${badge}
          </div>
          <div class="designer-entity-value">Read value: <span class="mono">${escapeHtml(value)}</span></div>
          ${probe.error ? `<div class="frame-errors"><div>\u26A0\uFE0F ${escapeHtml(probe.error)}</div></div>` : ''}
        </div>`;
      }).join('');
      return `<div class="designer-fingerprint-section">
        <div class="card-title" style="margin-bottom:0.5rem;">\uD83D\uDD0F Fingerprint probes</div>
        <div class="designer-summary">${verdict}</div>
        ${rows}
      </div>`;
    }

    async function saveAndApplyDesignerTemplate() {
      const textarea = document.getElementById('designer-yaml');
      const filenameInput = document.getElementById('designer-filename');
      const deviceSelect = document.getElementById('designer-apply-device');
      const nameInput = document.getElementById('designer-apply-name');
      const button = document.getElementById('btn-designer-apply');
      const entry = getCurrentEntry();
      if (!textarea || !button) return;
      if (!entry) {
        toast('Configure a hub before applying templates', 'err');
        return;
      }
      const content = textarea.value;
      if (!content.trim()) {
        toast('Paste a template YAML first', 'err');
        return;
      }
      const validated = _designerResult?.valid && (_designerResult.failed === 0 || !_designerResult.test_reads);
      if (!validated && !window.confirm('This draft has not passed live validation. Save and apply it anyway?')) {
        return;
      }
      const payload = { entry_id: entry.entry_id, content };
      const filename = filenameInput?.value.trim();
      if (filename) payload.filename = filename;
      const deviceId = deviceSelect?.value;
      if (deviceId) {
        payload.device_id = deviceId;
      } else {
        const deviceName = nameInput?.value.trim();
        if (deviceName) payload.device_name = deviceName;
        const slaveInput = document.getElementById('designer-slave-id');
        const slaveId = slaveInput && slaveInput.value ? parseInt(slaveInput.value, 10) : null;
        if (slaveId) payload.slave_id = slaveId;
      }
      button.disabled = true;
      button.textContent = 'Applying\u2026';
      try {
        const result = await apiCall('save_and_apply_template', payload);
        const savedAs = result.filename || filename || 'draft';
        toast(`Template saved as ${savedAs} \u2014 ${result.added_count} entit${result.added_count === 1 ? 'y' : 'ies'} applied`, 'ok');
        await refreshData();
      } catch (error) {
        toast(`Save & apply failed: ${error.message}`, 'err');
      } finally {
        button.disabled = false;
        button.textContent = '\uD83D\uDE80 Save & apply to device';
      }
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
