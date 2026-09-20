/* diagnostics.js — Diagnostics tab: health, scans, probes, hex writes, and activity log.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    function initializeDiagnosticCollapsibles() {
      const cards = [...document.querySelectorAll('#pane-diag > .card, #pane-diag .diagnostic-workspace > .card')]
        .filter(card => card.tagName !== 'DETAILS');
      cards.forEach((card, index) => {
        if (card.dataset.diagnosticCollapsible) return;
        const header = card.querySelector(':scope > .card-header');
        const titleWrap = header?.firstElementChild;
        if (!header || !titleWrap) return;
        const key = `diagnostic-${index}`;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'diagnostic-card-toggle';
        toggle.setAttribute('aria-expanded', String(_expandedDiagnosticCards.has(key)));
        for (const child of [...titleWrap.children]) {
          const text = document.createElement('span');
          text.className = child.className;
          while (child.firstChild) text.appendChild(child.firstChild);
          toggle.appendChild(text);
        }
        const summary = document.createElement('span');
        summary.className = 'diagnostic-card-summary';
        summary.textContent = 'Ready';
        toggle.appendChild(summary);
        header.replaceChild(toggle, titleWrap);
        card.classList.add('diagnostic-card');
        card.dataset.diagnosticCollapsible = key;
        card.classList.toggle('is-collapsed', !_expandedDiagnosticCards.has(key));
        toggle.addEventListener('click', () => {
          const isExpanded = card.classList.toggle('is-collapsed') === false;
          toggle.setAttribute('aria-expanded', String(isExpanded));
          if (isExpanded) _expandedDiagnosticCards.add(key);
          else _expandedDiagnosticCards.delete(key);
          try { localStorage.setItem('modbus_usb_expanded_diagnostics', JSON.stringify([..._expandedDiagnosticCards])); } catch (_) { /* Session state still works. */ }
        });
      });
    }

    function updateDiagnosticCardSummaries({ connected, serial, latestTransaction, diag, entry, transactions }) {
      const scan = diag.scan || {};
      const health = diag.health || {};
      const scanTotal = Number(scan.total || 0);
      const scanDone = Number(scan.completed || 0);
      const scanPercent = scanTotal ? Math.min(100, Math.round(scanDone / scanTotal * 100)) : 0;
      const selectedBoard = document.getElementById('board-tools-device')?.selectedOptions?.[0]?.textContent?.trim();
      const verifyBoard = document.getElementById('template-verify-device')?.selectedOptions?.[0]?.textContent?.trim();
      const completedReads = Math.max(0, Number(health.total_reads || 0) - Number(health.failed_reads || 0));
      const successRate = Number(health.total_reads || 0)
        ? `${Math.max(0, (completedReads / Number(health.total_reads || 1) * 100)).toFixed(0)}% ok`
        : 'No requests';
      const latestDetails = latestTransaction
        ? `${latestTransaction.operation || 'request'} · S${latestTransaction.slave ?? '—'} @ ${latestTransaction.address ?? '—'}${latestTransaction.duration_ms == null ? '' : ` · ${latestTransaction.duration_ms}ms`}`
        : 'Ready';
      const serialSettings = `${serial.baudrate || entry?.hub?.baudrate || '—'} ${serial.bytesize || entry?.hub?.bytesize || '—'}${serial.parity || entry?.hub?.parity || '—'}${serial.stopbits || entry?.hub?.stopbits || '—'}`;
      const summaries = {
        'diagnostic-0': `${connected ? 'Connected' : 'Offline'} · ${successRate} · ${health.failed_reads || 0} failed`,
        'diagnostic-1': `${serial.port || entry?.hub?.port || 'No port'} · ${serialSettings} · ${serial.operation_active ? 'Busy' : 'Idle'}`,
        'diagnostic-2': scan.active
          ? `Scanning ${scanDone}/${scanTotal} · ${scanPercent}% · ${scan.found || 0} found`
          : (scanTotal ? `Done ${scanDone}/${scanTotal} · ${scan.found || 0} found` : 'Ready to scan'),
        'diagnostic-3': latestDetails,
        'diagnostic-4': verifyBoard ? `${verifyBoard} · Read only` : 'Choose board · Read only',
        'diagnostic-5': transactions.length
          ? `${transactions.length} records · ${health.failed_reads || 0} failed · ${latestTransaction?.timestamp ? formatLogTime(latestTransaction.timestamp) : '—'}`
          : 'No activity recorded',
        'diagnostic-6': selectedBoard || 'Select a board',
        'diagnostic-8': 'Read only · addresses 0–3',
        'diagnostic-9': 'Read only · watch changes',
      };
      for (const [key, value] of Object.entries(summaries)) {
        const summary = document.querySelector(`[data-diagnostic-collapsible="${key}"] .diagnostic-card-summary`);
        if (!summary) continue;
        summary.textContent = value;
        summary.title = value;
      }
    }

    function renderDiagnosticsTab() {
      const entry = getCurrentEntry();
      const diag = entry?.diagnostics || {};
      renderBoardTools();
      renderTemplateVerifyDevices();
      renderScanProgress(diag.scan || {});
      updateScanTransportNote();
      const health = diag.health || {};
      const connected = Boolean(diag.connected);
      const latestTransaction = Array.isArray(diag.transactions) ? diag.transactions[0] : null;
      const status = document.getElementById('diag-connection-status');
      status.className = `serial-status ${connected ? 'ok' : ''}`;
      status.innerHTML = `<span class="dot"></span><span>${connected ? 'Serial port connected' : 'Serial port unavailable'}</span>`;

      const total = Number(health.total_reads || 0);
      const failed = Number(health.failed_reads || 0);
      const successRate = total ? `${Math.max(0, ((total - failed) / total * 100)).toFixed(1)}%` : '—';
      const fields = [
        ['Connection', connected ? 'Connected' : 'Disconnected'],
        ['Latest activity', latestTransaction ? `${latestTransaction.operation} · slave ${latestTransaction.slave}` : 'No Modbus traffic yet'],
        ['Last activity', latestTransaction?.timestamp ? formatLogTime(latestTransaction.timestamp) : '—'],
        ['Successful requests', Math.max(0, total - failed)],
        ['Failed requests', failed],
        ['Success rate', successRate],
        ['Consecutive failures', health.consecutive_failures || 0],
        ['Last good response', health.last_success ? formatLogTime(health.last_success) : '—'],
      ];
      document.getElementById('diag-health-grid').innerHTML = fields.map(([label, value]) => `
        <div class="diag-metric"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>
      `).join('');

      const serial = diag.serial || entry?.hub || {};
      const serialFields = [
        ['Port', serial.port || entry?.hub?.port || '—'],
        ['Settings', `${serial.baudrate || entry?.hub?.baudrate || '—'} baud · ${serial.bytesize || entry?.hub?.bytesize || '—'}${serial.parity || entry?.hub?.parity || '—'}${serial.stopbits || entry?.hub?.stopbits || '—'}`],
        ['Port owner', serial.connection_owner || 'Home Assistant Modbus USB'],
        ['Bus activity', serial.operation_active ? 'Request in progress' : 'Idle'],
      ];
      document.getElementById('diag-serial-grid').innerHTML = serialFields.map(([label, value]) => `
        <div class="diag-metric"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>
      `).join('');
      if (entry && _serialStatusEntryId !== entry.entry_id) {
        _serialStatusEntryId = entry.entry_id;
        setTimeout(refreshSerialPortStatus, 0);
      }

      const errorBox = document.getElementById('diag-last-error');
      const circuitBreaker = diag.circuit_breaker || {};
      const breakerEntries = Object.values(circuitBreaker).filter(b => b.state !== 'healthy');
      let breakerWarning = '';
      if (breakerEntries.length) {
        breakerWarning = ' · ' + breakerEntries.map(b => `Slave ${b.slave_id}: ${b.state.toUpperCase()} (${b.backoff_seconds}s backoff)`).join(', ');
      }
      if (health.last_error || breakerWarning) {
        errorBox.style.display = 'block';
        errorBox.textContent = `Latest error: ${health.last_error || 'None'}${breakerWarning}`;
      } else {
        errorBox.style.display = 'none';
      }

      const filter = document.getElementById('diag-log-filter')?.value || 'all';
      const slaveFilter = document.getElementById('diag-log-slave-filter')?.value?.trim();
      const fcFilter = document.getElementById('diag-log-fc-filter')?.value?.trim()?.toUpperCase();
      const allTransactions = diag.transactions || [];
      const transactions = allTransactions.filter(item => {
        if (filter === 'error' && item.status !== 'error') return false;
        if (filter === 'read' && !String(item.operation || '').startsWith('read_')) return false;
        if (filter === 'write' && !String(item.operation || '').startsWith('write_')) return false;
        if (slaveFilter && String(item.slave) !== slaveFilter) return false;
        if (fcFilter && String(item.function_code || '').toUpperCase() !== fcFilter) return false;
        return true;
      });
      updateDiagnosticCardSummaries({ connected, serial, latestTransaction, diag, entry, transactions: allTransactions });
      const body = document.getElementById('diag-log-body');
      if (!transactions.length) {
        body.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center; padding:1.2rem;">No RS-485 requests have been recorded yet.</td></tr>';
        return;
      }
      body.innerHTML = transactions.map(item => {
        const response = getDiagnosticResponse(item);
        const frame = item.request_hex
          ? `${item.function_code || '—'} · ${item.request_hex}`
          : (item.function_code || '—');
        const retry = Number(item.retries_configured || 0);
        return `<tr class="${item.status === 'error' ? 'error' : ''}">
          <td>${escapeHtml(formatLogTime(item.timestamp))}</td>
          <td>${escapeHtml(item.operation)}</td>
          <td class="mono" title="Reconstructed request. Response bytes are unavailable from the current PyModbus transport.">${escapeHtml(frame)}</td>
          <td>slave ${escapeHtml(item.slave)} · ${escapeHtml(item.address)}${item.count > 1 ? ` (${escapeHtml(item.count)} words)` : ''}</td>
          <td>${escapeHtml(response)}</td>
          <td>${item.duration_ms == null ? '—' : `${escapeHtml(item.duration_ms)} ms`} · ${retry} ${retry === 1 ? 'retry' : 'retries'} configured</td>
        </tr>`;
      }).join('');
    }

    async function refreshSerialPortStatus() {
      const entry = getCurrentEntry();
      const button = document.getElementById('btn-serial-status');
      const hint = document.getElementById('diag-serial-adapter');
      if (!entry || !hint) return;
      if (button) { button.disabled = true; button.textContent = 'Checking…'; }
      hint.textContent = 'Listing adapters visible to Home Assistant without opening the configured port…';
      try {
        const result = await apiCall('get_serial_status', { entry_id: entry.entry_id });
        const adapter = result.adapter;
        const serial = result.serial || {};
        if (adapter) {
          const meta = [];
          if (adapter.chipset) meta.push(`Chipset: ${adapter.chipset}`);
          if (adapter.vid && adapter.pid) meta.push(`VID/PID: ${adapter.vid}:${adapter.pid}`);
          if (adapter.persistent_path && adapter.persistent_path !== adapter.port) meta.push(`Persistent path: ${adapter.persistent_path}`);
          const metaStr = meta.length ? ` [${meta.join(' · ')}]` : '';
          hint.textContent = `${adapter.port} — ${adapter.description || 'Serial adapter'}${adapter.details ? ` (${adapter.details})` : ''}${metaStr}. ${serial.operation_active ? 'A Modbus request is currently using the integration lock.' : 'The integration lock is idle.'}`;
        } else {
          hint.textContent = `${serial.port || 'Configured port'} is not currently listed by Home Assistant. Check USB passthrough, cable, and adapter power.`;
        }
      } catch (error) {
        hint.textContent = `Could not read adapter details: ${error.message}`;
      } finally {
        if (button) { button.disabled = false; button.textContent = '↻ Refresh adapter details'; }
      }
    }

    function renderTemplateVerifyDevices() {
      const select = document.getElementById('template-verify-device');
      const entry = getCurrentEntry();
      if (!select || !entry) return;
      const selected = select.value;
      const devices = (entry.devices || []).filter(device => (entry.entities || []).some(entity => entity.device_id === device.id));
      select.innerHTML = devices.length
        ? devices.map(device => `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name || device.model || 'Modbus board')} · slave ${escapeHtml(device.slave_id ?? entry.hub?.slave_id ?? 1)}</option>`).join('')
        : '<option value="">No configured board with entities</option>';
      if (devices.some(device => device.id === selected)) select.value = selected;
      document.getElementById('btn-template-verify').disabled = !devices.length;
    }

    async function verifyTemplateReads() {
      const entry = getCurrentEntry();
      const deviceId = document.getElementById('template-verify-device')?.value;
      const button = document.getElementById('btn-template-verify');
      const output = document.getElementById('template-verify-result');
      if (!entry || !deviceId || !output) { toast('Choose a configured board first', 'err'); return; }
      button.disabled = true;
      button.textContent = 'Verifying…';
      output.style.display = 'block';
      output.className = 'debug-result';
      output.textContent = 'Reading configured non-switch entities through the Home Assistant serial lock…';
      try {
        const report = await apiCall('verify_device_reads', { entry_id: entry.entry_id, device_id: deviceId });
        output.className = `debug-result ${report.failed ? 'error' : 'ok'}`;
        const rows = (report.results || []).map(item => {
          const detail = item.status === 'pass' ? `value: ${item.value}` : (item.reason || item.error || 'No result');
          return `<li><strong>${escapeHtml(item.status.toUpperCase())}</strong> · ${escapeHtml(item.name)} · ${escapeHtml(item.register_type)} ${escapeHtml(item.address)} · ${escapeHtml(detail)}</li>`;
        }).join('');
        output.innerHTML = `<div class="debug-result-title"><span>${escapeHtml(report.device_name)}</span><span>${report.passed}/${report.checked} reads passed</span></div><div class="debug-result-copy">${report.failed} failed, ${report.skipped} outputs skipped · ${report.duration_ms} ms</div><ul class="debug-result-copy" style="margin:0.55rem 0 0; padding-left:1.1rem;">${rows}</ul>`;
        await refreshData();
      } catch (error) {
        output.className = 'debug-result error';
        output.textContent = `Read verification failed: ${error.message}`;
      } finally {
        button.disabled = false;
        button.textContent = '▶ Verify configured reads';
      }
    }

    function getDiagnosticResponse(item) {
      // Do not use || here: a Modbus value of 0 is a real, useful response.
      return item.status === 'error'
        ? `ERROR: ${item.error || 'Unknown error'}`
        : (item.result ?? item.value ?? 'Accepted');
    }

    function renderScanProgress(scan) {
      const wrap = document.getElementById('scan-progress-wrap');
      if (!wrap) return;
      const total = Number(scan.total || 0);
      const completed = Number(scan.completed || 0);
      const percent = total ? Math.min(100, Math.round(completed / total * 100)) : 0;
      if (!scan.active && !total) {
        wrap.style.display = 'none';
        return;
      }
      wrap.style.display = 'block';
      document.getElementById('scan-progress-bar').style.width = `${percent}%`;
      document.getElementById('scan-progress-percent').textContent = `${percent}%`;
      document.getElementById('scan-progress-text').textContent = scan.active
        ? `Scanning ${completed} of ${total} probes — ${scan.found || 0} device${scan.found === 1 ? '' : 's'} found`
        : `Scan complete — ${completed} probes, ${scan.found || 0} device${scan.found === 1 ? '' : 's'} found`;
    }

    async function refreshScanProgress() {
      try {
        const data = await apiCall('get_data');
        _entries = data.entries || _entries;
        renderDiagnosticsTab();
      } catch (e) {
        // The completed scan call will surface the error in its result box.
      }
    }

        async function exportDiagnosticLog() {
      const entry = getCurrentEntry();
      if (!entry) return;
      const format = document.getElementById('diag-log-export-format')?.value || 'json';
      const redact = Boolean(document.getElementById('diag-log-export-redact')?.checked);
      const filter = document.getElementById('diag-log-filter')?.value || 'all';
      const slaveFilter = document.getElementById('diag-log-slave-filter')?.value?.trim();
      const fcFilter = document.getElementById('diag-log-fc-filter')?.value?.trim();

      const params = {
        entry_id: entry.entry_id,
        format,
        redact,
        filter,
      };
      if (slaveFilter) params.slave_id = parseInt(slaveFilter, 10);
      if (fcFilter) params.function_code = fcFilter;

      try {
        const result = await apiCall('export_activity_log', params);
        const blob = new Blob([result.data], { type: result.content_type || 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = result.filename || `modbus_log.${format}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        toast(`Exported ${result.count} records (${format.toUpperCase()})`, 'ok');
      } catch (err) {
        toast(`Export failed: ${err.message}`, 'err');
      }
    }

    async function copyDiagnosticLog() {
      const entry = getCurrentEntry();
      const filter = document.getElementById('diag-log-filter')?.value || 'all';
      const rows = (entry?.diagnostics?.transactions || []).filter(item =>
        filter === 'all' || (filter === 'error' && item.status === 'error') ||
        (filter === 'read' && String(item.operation || '').startsWith('read_')) ||
        (filter === 'write' && String(item.operation || '').startsWith('write_'))
      );
      const text = rows.map(item => [item.timestamp, item.status.toUpperCase(), item.operation,
        `fc=${item.function_code || 'n/a'}`, `request=${item.request_hex || 'not available'}`,
        `slave=${item.slave}`, `address=${item.address}`, getDiagnosticResponse(item),
        item.duration_ms == null ? '' : `${item.duration_ms}ms`,
        `retries_configured=${item.retries_configured ?? 0}`].join(' | ')).join('\n');
      try {
        const content = text || 'No RS-485 activity recorded.';
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(content);
        } else {
          const textarea = document.createElement('textarea');
          textarea.value = content;
          textarea.setAttribute('readonly', '');
          textarea.style.cssText = 'position:fixed; opacity:0; pointer-events:none;';
          document.body.appendChild(textarea);
          textarea.select();
          const copied = document.execCommand('copy');
          textarea.remove();
          if (!copied) throw new Error('Browser denied clipboard access');
        }
        toast('Diagnostic log copied', 'ok');
      } catch (e) {
        const textarea = document.createElement('textarea');
        textarea.value = text || 'No RS-485 activity recorded.';
        textarea.setAttribute('readonly', '');
        textarea.style.cssText = 'position:fixed; opacity:0; pointer-events:none;';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        toast(copied ? 'Diagnostic log copied' : 'Could not copy the diagnostic log', copied ? 'ok' : 'err');
      }
    }

    async function clearDiagnosticLog() {
      const entry = getCurrentEntry();
      if (!entry) return;
      try {
        await apiCall('clear_diagnostic_log', { entry_id: entry.entry_id });
        toast('RS-485 activity log cleared', 'ok');
        await refreshData();
      } catch (e) {
        toast(`Could not clear the diagnostic log: ${e.message}`, 'err');
      }
    }

    function normalizeManualHex(value) {
      return String(value || '').replace(/0x/gi, '').replace(/\s+/g, '');
    }

    function crc16Modbus(bytes) {
      let crc = 0xFFFF;
      for (const byte of bytes) {
        crc ^= byte;
        for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) ? ((crc >>> 1) ^ 0xA001) : (crc >>> 1);
      }
      return crc;
    }

    function previewManualHexCrc() {
      const input = document.getElementById('manual-hex-frame');
      const auto = document.getElementById('manual-hex-generate-crc')?.checked;
      const preview = document.getElementById('manual-hex-preview');
      if (!input || !preview) return;
      const compact = normalizeManualHex(input.value);
      if (!compact) { preview.textContent = 'Paste a frame to validate it.'; return; }
      if (compact.length % 2 || /[^0-9a-f]/i.test(compact)) { preview.textContent = 'Use complete hexadecimal byte pairs only.'; return; }
      const bytes = Uint8Array.from(compact.match(/../g).map(pair => parseInt(pair, 16)));
      if (auto) {
        const existingCrc = bytes.length >= 4 ? (bytes[bytes.length - 2] | (bytes[bytes.length - 1] << 8)) : -1;
        const expectedExistingCrc = bytes.length >= 4 ? crc16Modbus(bytes.slice(0, -2)) : -2;
        if (existingCrc === expectedExistingCrc) {
          preview.textContent = 'CRC is already valid; the pasted frame will be kept unchanged.';
        } else {
          const crc = crc16Modbus(bytes);
          preview.textContent = `CRC preview: ${[...bytes, crc & 0xFF, crc >>> 8].map(byte => byte.toString(16).padStart(2, '0').toUpperCase()).join(' ')}`;
        }
      } else if (bytes.length >= 4) {
        const expected = crc16Modbus(bytes.slice(0, -2));
        const received = bytes[bytes.length - 2] | (bytes[bytes.length - 1] << 8);
        preview.textContent = expected === received ? 'CRC is valid.' : `CRC mismatch: frame has ${received.toString(16).padStart(4, '0').toUpperCase()}, expected ${expected.toString(16).padStart(4, '0').toUpperCase()}.`;
      } else {
        preview.textContent = 'A complete RTU frame needs at least four bytes plus CRC.';
      }
    }

    function openManualHexWrite() {
      document.getElementById('manual-hex-confirm').checked = false;
      document.getElementById('manual-hex-result').textContent = '';
      previewManualHexCrc();
      openModal('modal-manual-hex-write');
      setTimeout(() => document.getElementById('manual-hex-frame').focus(), 0);
    }

    async function pasteManualHex() {
      const input = document.getElementById('manual-hex-frame');
      try {
        input.value = await navigator.clipboard.readText();
        previewManualHexCrc();
      } catch (_) {
        toast('Clipboard access was denied. Paste with Ctrl+V instead.', 'err');
      }
    }

    async function sendManualHexWrite() {
      const entry = getCurrentEntry();
      const frame = document.getElementById('manual-hex-frame').value;
      const generateCrc = document.getElementById('manual-hex-generate-crc').checked;
      const confirmed = document.getElementById('manual-hex-confirm').checked;
      const button = document.getElementById('btn-manual-hex-send');
      const result = document.getElementById('manual-hex-result');
      if (!entry || !normalizeManualHex(frame)) { result.textContent = 'Paste a documented RTU write frame first.'; return; }
      if (!confirmed) { result.textContent = 'Confirm the hardware-risk checkbox before sending.'; return; }
      if (!window.confirm('Send exactly one dangerous documented Modbus RTU write? This can change equipment or serial settings.')) return;
      button.disabled = true;
      button.textContent = 'Sending…';
      result.textContent = 'Validating CRC and sending through the Home Assistant serial lock…';
      try {
        const response = await apiCall('manual_hex_write', { entry_id: entry.entry_id, frame_hex: frame, generate_crc: generateCrc, confirmed: true });
        result.textContent = `Accepted: slave ${response.slave_id}, ${response.function_code}, address ${response.address}, ${response.count} item${response.count === 1 ? '' : 's'}.`;
        toast('Dangerous hex write accepted; verify the physical result.', 'ok');
        await refreshData();
      } catch (error) {
        result.textContent = `Hex write rejected or failed: ${error.message}`;
      } finally {
        button.disabled = false;
        button.textContent = 'Send one dangerous hex write';
      }
    }

    // ─── UNKNOWN BOARD DEBUG LAB ───────────────────────────────
    function renderAutomaticDiscoveryAdvice(results) {
      const output = document.getElementById('auto-discovery-advice');
      const responsive = results.filter(item => item.status === 'response');
      if (!responsive.length) {
        output.innerHTML = `<article class="debug-result error"><div class="debug-result-title"><span>No standard read reply found</span><span>Connection check</span></div><div class="debug-result-copy">Try the correct slave ID and baud rate first. Then check A/B wiring, common ground, parity, stop bits, and whether the board needs power separately from the USB adapter.</div></article>`;
        return;
      }
      const byType = Object.groupBy
        ? Object.groupBy(responsive, item => item.register_type)
        : responsive.reduce((groups, item) => { (groups[item.register_type] ||= []).push(item); return groups; }, {});
      const suggestions = [];
      if (byType.discrete) suggestions.push(`Discrete inputs replied at ${byType.discrete.map(item => item.address).join(', ')}. These are good candidates for Home Assistant binary sensors; use Input Watch and change a physical input to confirm.`);
      if (byType.input) suggestions.push(`Input registers replied at ${byType.input.map(item => item.address).join(', ')}. These may be measurements such as temperature, voltage, or counters. Their units and scaling still require the board manual.`);
      if (byType.holding) suggestions.push(`Holding registers replied at ${byType.holding.map(item => item.address).join(', ')}. They can be values or configuration. Read them safely, but do not write them until you have documentation.`);
      if (byType.coil) suggestions.push(`Coils replied at ${byType.coil.map(item => item.address).join(', ')}. They may show output state, but a coil response alone does not prove it is safe to control.`);
      output.innerHTML = suggestions.map(text => `<article class="debug-result ok"><div class="debug-result-copy">${escapeHtml(text)}</div></article>`).join('');
    }

    async function runAutomaticDiscovery() {
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('debug-slave').value, 10);
      const button = document.getElementById('btn-auto-discover');
      const stopButton = document.getElementById('btn-auto-stop');
      const status = document.getElementById('auto-discovery-status');
      if (!entry || !Number.isInteger(slave) || slave < 1 || slave > 247) {
        status.textContent = 'Enter a valid Target Slave ID below before starting discovery.';
        return;
      }
      if (_inputWatchActive) {
        status.textContent = 'Stop Input Watch before starting automatic discovery.';
        return;
      }
      _automaticDiscoveryActive = true;
      button.disabled = true;
      stopButton.disabled = false;
      document.getElementById('btn-debug-probe').disabled = true;
      document.getElementById('auto-discovery-advice').innerHTML = '';
      const allTypes = ['holding', 'input', 'coil', 'discrete'];
      try {
        status.textContent = 'Step 1 of 2: checking four standard read functions at address 0…';
        const first = await apiCall('probe_registers', { entry_id: entry.entry_id, slave_id: slave, start_address: 0, end_address: 0, register_types: allTypes });
        if (!_automaticDiscoveryActive || first.stopped) {
          status.textContent = 'Discovery stopped after the current read.';
          return;
        }
        const respondingTypes = [...new Set(first.results.filter(item => item.status === 'response').map(item => item.register_type))];
        let results = first.results;
        if (respondingTypes.length) {
          status.textContent = `Step 2 of 2: checking addresses 1–3 with ${respondingTypes.length} responding function${respondingTypes.length === 1 ? '' : 's'}…`;
          const second = await apiCall('probe_registers', { entry_id: entry.entry_id, slave_id: slave, start_address: 1, end_address: 3, register_types: respondingTypes });
          results = [...results, ...second.results];
          if (second.stopped || !_automaticDiscoveryActive) {
            status.textContent = 'Discovery stopped after the current read.';
            renderBoardProbeResults(results);
            renderAutomaticDiscoveryAdvice(results);
            return;
          }
        }
        renderBoardProbeResults(results);
        renderAutomaticDiscoveryAdvice(results);
        const count = results.filter(item => item.status === 'response').length;
        status.textContent = `Discovery complete: ${count} standard read response${count === 1 ? '' : 's'} found. Review the suggested next steps below.`;
        setTimeout(refreshData, 200);
      } catch (error) {
        status.textContent = `Discovery failed: ${error.message}. Verify serial settings and try again.`;
      } finally {
        _automaticDiscoveryActive = false;
        button.disabled = false;
        stopButton.disabled = true;
        document.getElementById('btn-debug-probe').disabled = _inputWatchActive;
      }
    }

    async function stopAutomaticDiscovery() {
      if (!_automaticDiscoveryActive) return;
      _automaticDiscoveryActive = false;
      document.getElementById('btn-auto-stop').disabled = true;
      document.getElementById('auto-discovery-status').textContent = 'Stop requested. The current serial read must finish before queued discovery checks are cancelled.';
      try {
        await apiCall('stop_probe_registers', { entry_id: getCurrentEntry().entry_id });
      } catch (error) {
        document.getElementById('auto-discovery-status').textContent = `Could not stop discovery: ${error.message}`;
      }
    }

    function renderBoardProbeResults(results) {
      const box = document.getElementById('debug-probe-results');
      if (!results?.length) {
        box.innerHTML = '<div class="debug-result">No requests were sent.</div>';
        return;
      }
      box.innerHTML = results.map(item => {
        const isOk = item.status === 'response';
        const title = isOk
          ? `Response · ${item.function_name} (${item.function_code}) · address ${item.address}`
          : `No response · ${item.function_name} (${item.function_code}) · address ${item.address}`;
        const reply = isOk
          ? `<strong>Returned:</strong> ${escapeHtml(item.value)}`
          : `<strong>Error:</strong> ${escapeHtml(item.error || 'No response received')}`;
        return `<article class="debug-result ${isOk ? 'ok' : 'error'}">
          <div class="debug-result-title"><span>${escapeHtml(title)}</span><span>${isOk ? 'Reply received' : 'Check connection'}</span></div>
          <div class="debug-result-code">Request: ${escapeHtml(item.request)}</div>
          <div class="debug-result-copy">${reply}</div>
          <div class="debug-result-copy"><strong>What this tells you:</strong> ${escapeHtml(item.meaning)}</div>
        </article>`;
      }).join('');
    }

    async function runSafeBoardProbe() {
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('debug-slave').value, 10);
      const startAddress = parseInt(document.getElementById('debug-start-address').value, 10);
      const endAddress = parseInt(document.getElementById('debug-end-address').value, 10);
      const types = [...document.querySelectorAll('input[name="debug-read-type"]:checked')].map(input => input.value);
      const button = document.getElementById('btn-debug-probe');
      const stopButton = document.getElementById('btn-debug-stop');
      const status = document.getElementById('debug-probe-status');
      const output = document.getElementById('debug-probe-results');
      if (!entry || !Number.isInteger(slave) || slave < 1 || slave > 247 || !Number.isInteger(startAddress) || !Number.isInteger(endAddress)) {
        status.textContent = 'Enter a valid slave ID and register-address range.';
        return;
      }
      if (endAddress < startAddress || endAddress - startAddress > 3) {
        status.textContent = 'Use an address range from 0 to 3 addresses wide (up to four addresses total).';
        return;
      }
      if (!types.length) {
        status.textContent = 'Choose at least one read function.';
        return;
      }
      const requestCount = (endAddress - startAddress + 1) * types.length;
      button.disabled = true;
      button.textContent = 'Probing…';
      stopButton.disabled = false;
      status.textContent = `Sending ${requestCount} read-only request${requestCount === 1 ? '' : 's'}… absent boards can take time to reply.`;
      output.innerHTML = '';
      try {
        const result = await apiCall('probe_registers', {
          entry_id: entry.entry_id, slave_id: slave, start_address: startAddress,
          end_address: endAddress, register_types: types,
        });
        const successCount = result.results.filter(item => item.status === 'response').length;
        status.textContent = result.stopped
          ? `Stopped: ${successCount} response${successCount === 1 ? '' : 's'} from ${result.results.length} completed request${result.results.length === 1 ? '' : 's'}.`
          : `Finished: ${successCount} response${successCount === 1 ? '' : 's'} from ${result.results.length} safe read requests.`;
        renderBoardProbeResults(result.results);
        setTimeout(refreshData, 200);
      } catch (error) {
        status.textContent = `Probe failed: ${error.message}`;
        output.innerHTML = '<div class="debug-result error">The probe did not complete. Confirm the serial settings and retry with one function and one address.</div>';
      } finally {
        button.disabled = false;
        button.textContent = 'Run safe read probe';
        stopButton.disabled = true;
      }
    }

    async function stopSafeBoardProbe() {
      const entry = getCurrentEntry();
      const button = document.getElementById('btn-debug-stop');
      const status = document.getElementById('debug-probe-status');
      if (!entry) return;
      button.disabled = true;
      button.textContent = 'Stopping…';
      status.textContent = 'Stop requested. The current serial read must finish before remaining requests are cancelled.';
      try {
        const result = await apiCall('stop_probe_registers', { entry_id: entry.entry_id });
        if (!result.stopping) status.textContent = 'There is no active probe to stop.';
      } catch (error) {
        status.textContent = `Could not stop the probe: ${error.message}`;
        button.disabled = false;
        button.textContent = 'Stop probe';
      }
    }

    function renderInputWatchResults(results, changedAddresses) {
      const output = document.getElementById('input-watch-results');
      output.innerHTML = results.map(item => {
        const key = `${item.register_type}:${item.address}`;
        const changed = changedAddresses.has(key);
        if (item.status !== 'response') {
          return `<article class="debug-result error"><div class="debug-result-title"><span>Address ${item.address}</span><span>No response</span></div><div class="debug-result-copy">${escapeHtml(item.error || 'No response received')}</div></article>`;
        }
        return `<article class="debug-result ${changed ? 'ok' : ''}"><div class="debug-result-title"><span>Address ${item.address}</span><span>${changed ? 'Signal changed' : 'Current value'}</span></div><div class="debug-result-copy"><strong>${escapeHtml(item.register_type)}:</strong> ${escapeHtml(item.value)}</div></article>`;
      }).join('');
    }

    async function runInputWatchCycle() {
      if (!_inputWatchActive) return;
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('debug-slave').value, 10);
      const startAddress = parseInt(document.getElementById('debug-start-address').value, 10);
      const endAddress = parseInt(document.getElementById('debug-end-address').value, 10);
      const registerType = document.getElementById('input-watch-type').value;
      const interval = parseInt(document.getElementById('input-watch-interval').value, 10);
      const status = document.getElementById('input-watch-status');
      try {
        const result = await apiCall('probe_registers', {
          entry_id: entry.entry_id, slave_id: slave, start_address: startAddress,
          end_address: endAddress, register_types: [registerType],
        });
        const changedAddresses = new Set();
        result.results.forEach(item => {
          if (item.status !== 'response') return;
          const key = `${item.register_type}:${item.address}`;
          if (_inputWatchPrevious.has(key) && _inputWatchPrevious.get(key) !== item.value) changedAddresses.add(key);
          _inputWatchPrevious.set(key, item.value);
        });
        renderInputWatchResults(result.results, changedAddresses);
        const replyCount = result.results.filter(item => item.status === 'response').length;
        status.textContent = result.stopped
          ? 'Watch stopped after the current read.'
          : `${replyCount} address${replyCount === 1 ? '' : 'es'} replied. ${changedAddresses.size ? `${changedAddresses.size} signal change${changedAddresses.size === 1 ? '' : 's'} detected.` : 'Change a board input to detect a signal.'}`;
      } catch (error) {
        status.textContent = `Watch read failed: ${error.message}. Check serial settings, then retry.`;
      }
      if (_inputWatchActive) _inputWatchTimer = setTimeout(runInputWatchCycle, Number.isFinite(interval) ? interval : 2000);
    }

    function startInputWatch() {
      const slave = parseInt(document.getElementById('debug-slave').value, 10);
      const startAddress = parseInt(document.getElementById('debug-start-address').value, 10);
      const endAddress = parseInt(document.getElementById('debug-end-address').value, 10);
      const status = document.getElementById('input-watch-status');
      if (!Number.isInteger(slave) || slave < 1 || slave > 247 || !Number.isInteger(startAddress) || !Number.isInteger(endAddress) || endAddress < startAddress || endAddress - startAddress > 3) {
        status.textContent = 'Use a valid slave ID and a range of up to four addresses before starting the watch.';
        return;
      }
      _inputWatchActive = true;
      _inputWatchPrevious.clear();
      document.getElementById('btn-input-watch-start').disabled = true;
      document.getElementById('btn-input-watch-stop').disabled = false;
      document.getElementById('btn-debug-probe').disabled = true;
      status.textContent = 'Watching inputs with read-only requests…';
      runInputWatchCycle();
    }

    async function stopInputWatch() {
      _inputWatchActive = false;
      if (_inputWatchTimer) clearTimeout(_inputWatchTimer);
      _inputWatchTimer = null;
      document.getElementById('btn-input-watch-stop').disabled = true;
      document.getElementById('btn-input-watch-start').disabled = false;
      document.getElementById('btn-debug-probe').disabled = false;
      document.getElementById('input-watch-status').textContent = 'Stopping input watch after the current read…';
      await stopSafeBoardProbe();
      if (!document.getElementById('btn-debug-stop').disabled) document.getElementById('btn-debug-stop').disabled = true;
    }

    async function sendGuardedDebugWrite() {
      const entry = getCurrentEntry();
      const slave = parseInt(document.getElementById('debug-slave').value, 10);
      const address = parseInt(document.getElementById('debug-write-address').value, 10);
      const value = parseInt(document.getElementById('debug-write-value').value, 10);
      const registerType = document.getElementById('debug-write-type').value;
      const hasOneBoardAcknowledgement = document.getElementById('debug-one-board-check').checked;
      const resultBox = document.getElementById('debug-write-result');
      const button = document.getElementById('btn-debug-write');
      if (!entry || ![slave, address, value].every(Number.isInteger) || slave < 1 || slave > 247 || address < 0 || address > 65535 || value < 0 || value > 65535) {
        resultBox.style.color = '#fca5a5';
        resultBox.textContent = 'Enter a valid slave ID, address, and value from 0 to 65535.';
        return;
      }
      if (registerType === 'coil' && ![0, 1].includes(value)) {
        resultBox.style.color = '#fca5a5';
        resultBox.textContent = 'Coil writes use only 0 (OFF) or 1 (ON).';
        return;
      }
      if (!hasOneBoardAcknowledgement) {
        resultBox.style.color = '#fcd34d';
        resultBox.textContent = 'Confirm that exactly one powered board is connected before continuing.';
        return;
      }
      if (!window.confirm(`Warning: this sends a write to slave ${slave}, ${registerType} address ${address}, value ${value}. It may activate equipment, alter settings, or reset a board. Continue?`)) return;
      if (!window.confirm('Final check: exactly one powered board is connected to this RS-485 line, and this command is documented for that board. Send it now?')) return;
      button.disabled = true;
      button.textContent = 'Sending…';
      resultBox.style.color = '#93c5fd';
      const functionCode = registerType === 'coil' ? '0x05' : '0x06';
      resultBox.textContent = `Sending one write: function ${functionCode}, slave ${slave}, ${registerType} ${address}, value ${value}…`;
      try {
        await apiCall('diagnostic_write', { entry_id: entry.entry_id, slave_id: slave, address, register_type: registerType, value });
        resultBox.style.color = '#86efac';
        resultBox.textContent = `Function ${functionCode} acknowledgement received for slave ${slave}, ${registerType} address ${address}. This confirms the Modbus reply, not the physical effect.`;
        setTimeout(refreshData, 200);
      } catch (error) {
        resultBox.style.color = '#fca5a5';
        resultBox.textContent = `Write failed: ${error.message}. Do not retry until you verify the command and connection.`;
      } finally {
        button.disabled = false;
        button.textContent = 'Send one manual write';
      }
    }

    function updateScanTransportNote() {
      // v2.8.0: ESPHome bridges fix baud/parity in their uart: block — the
      // scanner sweeps slave IDs only, so the line-setting inputs are disabled.
      const entry = getCurrentEntry();
      const fixed = Boolean(entry && entry.hub && entry.hub.baudrate_fixed);
      const note = document.getElementById('scan-fixed-baud-note');
      const rates = document.getElementById('scan-baudrates');
      const parities = document.getElementById('scan-parities');
      if (note) note.hidden = !fixed;
      if (rates) rates.disabled = fixed;
      if (parities) parities.disabled = fixed;
      if (fixed && rates && entry.hub.baudrate) rates.value = String(entry.hub.baudrate);
      if (fixed && parities && entry.hub.parity) parities.value = String(entry.hub.parity);
      return fixed;
    }

    async function scanRs485Bus() {
      const entry = getCurrentEntry();
      const fixedBaud = updateScanTransportNote();
      const baudrates = fixedBaud
        ? [parseInt(entry?.hub?.baudrate, 10) || 9600]
        : document.getElementById('scan-baudrates').value.split(',')
          .map(value => parseInt(value.trim(), 10)).filter(Number.isFinite);
      const parities = fixedBaud
        ? [String(entry?.hub?.parity || 'N').toUpperCase()]
        : document.getElementById('scan-parities').value.split(',')
          .map(value => value.trim().toUpperCase()).filter(Boolean);
      const startSlave = parseInt(document.getElementById('scan-start-slave').value, 10);
      const endSlave = parseInt(document.getElementById('scan-end-slave').value, 10);
      const button = document.getElementById('btn-scan-bus');
      const box = document.getElementById('scan-result-box');
      if (!entry || !baudrates.length || !parities.length || !parities.every(value => ['N', 'E', 'O'].includes(value)) || !Number.isFinite(startSlave) || !Number.isFinite(endSlave)) {
        toast('Enter valid baud rates, parity profiles (N, E, or O), and slave IDs', 'err');
        return;
      }
      button.disabled = true;
      button.textContent = '⌛ Scanning…';
      box.style.display = 'block';
      box.style.color = '#93c5fd';
      box.textContent = fixedBaud
        ? `Scanning slave IDs ${startSlave}–${endSlave} through the ESPHome bridge (line settings fixed in ESPHome)…`
        : `Scanning slave IDs ${startSlave}–${endSlave} at ${baudrates.join(', ')} baud with ${parities.join(', ')} parity…`;
      const progressTimer = setInterval(refreshScanProgress, 500);
      try {
        const result = await apiCall('scan_bus', {
          entry_id: entry.entry_id, baudrates, parities,
          start_slave: startSlave, end_slave: endSlave,
        });
        box.style.color = result.found?.length ? '#34d399' : '#fcd34d';
        box.innerHTML = result.found?.length
          ? `Found: ${result.found.map(item => {
              const suggestion = item.suggestions?.length
                ? ` — suggested template: ${escapeHtml(item.suggestions.join(', '))}`
                : '';
              const where = item.baudrate == null || result.baudrate_fixed
                ? 'via ESPHome bridge'
                : `at ${escapeHtml(item.baudrate)} baud, ${escapeHtml(item.parity || 'N')} parity`;
              const baud = Number(item.baudrate) || Number(entry?.hub?.baudrate) || 9600;
              return `<div style="margin-bottom:0.55rem;">slave ${escapeHtml(item.slave_id)} ${where} (${escapeHtml(item.response)})${suggestion} <button class="btn btn-secondary btn-sm" onclick="useDiscoveredTarget(${Number(item.slave_id)}, ${baud})">Use this target</button></div>`;
            }).join('')}`
          : `No responding devices found after ${result.probed || 0} probes. Check A/B wires, power, baud rate, and slave ID.`;
        await refreshData();
      } catch (error) {
        box.style.color = '#f87171';
        box.textContent = `Scan failed: ${error.message}`;
      } finally {
        clearInterval(progressTimer);
        await refreshScanProgress();
        button.disabled = false;
        button.textContent = '🔎 Scan RS-485 Bus';
      }
    }
