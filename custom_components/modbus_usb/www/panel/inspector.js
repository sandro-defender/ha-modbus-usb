/* inspector.js — Traffic Inspector tab: RTU frame analyzer & latency waterfall.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * The backend parses frames (custom_components/modbus_usb/inspector.py); this
 * module only renders the decoded view returned by modbus_usb/traffic_inspector.
 */
    let _inspectorView = null;
    let _inspectorSelected = null;
    let _inspectorLoading = false;

    const INSPECTOR_STAGE_LABELS = {
      lock_wait_ms: 'Bus lock wait',
      connect_ms: 'Port connect',
      frame_delay_ms: 'Inter-frame delay',
      request_ms: 'Serial request',
    };

    function durationTone(durationMs) {
      if (typeof durationMs !== 'number') return 'slate';
      if (durationMs < 50) return 'green';
      if (durationMs < 250) return 'amber';
      return 'red';
    }

    function formatMs(value) {
      return typeof value === 'number' ? `${value.toFixed(1)} ms` : '—';
    }

    async function loadTrafficInspector(force = false) {
      const pane = document.getElementById('pane-inspector');
      if (!pane || (!force && !pane.classList.contains('active'))) return;
      const entry = getCurrentEntry();
      const list = document.getElementById('inspector-list');
      if (!entry) {
        list.innerHTML = '<div class="empty-box"><div class="empty-icon">📡</div><h3>No hub configured</h3></div>';
        return;
      }
      if (_inspectorLoading) return;
      _inspectorLoading = true;
      try {
        _inspectorView = await apiCall('traffic_inspector', { entry_id: entry.entry_id, limit: 100 });
        if (_inspectorSelected !== null && _inspectorView?.transactions?.length <= _inspectorSelected) {
          _inspectorSelected = null;
        }
        renderInspectorTab();
      } catch (error) {
        list.innerHTML = `<div class="empty-box"><div class="empty-icon">⚠️</div><h3>Inspector unavailable</h3><p>${escapeHtml(error.message)}</p></div>`;
      } finally {
        _inspectorLoading = false;
      }
    }

    function renderInspectorTab() {
      const statsBar = document.getElementById('inspector-stats');
      const list = document.getElementById('inspector-list');
      if (!statsBar || !list) return;
      const view = _inspectorView;
      if (!view || !Array.isArray(view.transactions)) {
        statsBar.innerHTML = '';
        list.innerHTML = '<div class="empty-box"><div class="empty-icon">📡</div><h3>No bus traffic recorded yet</h3><p>Transactions appear here as soon as the hub talks to a board.</p></div>';
        return;
      }

      const stats = view.stats || {};
      const slaveChips = Object.entries(view.per_slave || {}).map(([slave, info]) => {
        const tone = info.errors ? 'amber' : 'blue';
        return `<span class="badge badge-${tone}" title="Average ${formatMs(info.avg_ms)} · max ${formatMs(info.max_ms)}">Slave ${escapeHtml(slave)}: ${info.count} txn · avg ${formatMs(info.avg_ms)}</span>`;
      }).join('');

      statsBar.innerHTML = `
        <div class="stat-card"><div class="stat-num" style="color:#60a5fa;">${stats.total ?? 0}</div><div class="stat-label">Transactions</div></div>
        <div class="stat-card"><div class="stat-num" style="color:${stats.errors ? '#f87171' : '#34d399'};">${stats.errors ?? 0}</div><div class="stat-label">Errors</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#38bdf8;">${formatMs(stats.avg_ms)}</div><div class="stat-label">Avg duration</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#fbbf24;">${formatMs(stats.p95_ms)}</div><div class="stat-label">p95 duration</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#c084fc;">${formatMs(stats.max_ms)}</div><div class="stat-label">Slowest</div></div>
        <div class="stat-card inspector-slave-chips">${slaveChips || '<span class="badge badge-slate">no per-slave data</span>'}</div>
      `;

      if (!view.transactions.length) {
        list.innerHTML = '<div class="empty-box"><div class="empty-icon">📡</div><h3>No bus traffic recorded yet</h3><p>Transactions appear here as soon as the hub talks to a board.</p></div>';
        return;
      }

      list.innerHTML = view.transactions.map((item, index) => {
        const txn = item.transaction || {};
        const frame = item.frame || {};
        const statusBadge = txn.status === 'error'
          ? '<span class="badge badge-red">error</span>'
          : '<span class="badge badge-green">ok</span>';
        const durationBadge = `<span class="badge badge-${durationTone(txn.duration_ms)}">${formatMs(txn.duration_ms)}</span>`;
        const selected = index === _inspectorSelected ? ' selected' : '';
        return `<button class="inspector-row${selected}" onclick="selectInspectorTransaction(${index})" aria-pressed="${index === _inspectorSelected}">
          <span class="inspector-row-time">${escapeHtml(formatLogTime(txn.timestamp))}</span>
          <span class="inspector-row-op mono">${escapeHtml(txn.operation || '—')} · slave ${escapeHtml(txn.slave ?? '—')}</span>
          <span class="inspector-row-frame mono">${escapeHtml(frame.summary || frame.function_code || txn.function_code || 'no frame')}</span>
          <span class="inspector-row-badges">${statusBadge}${durationBadge}</span>
        </button>`;
      }).join('');

      renderInspectorDetail();
    }

    function selectInspectorTransaction(index) {
      _inspectorSelected = index;
      renderInspectorTab();
    }

    function frameFieldChips(frame) {
      const chips = [];
      const add = (label, value, tone = 'slate') => {
        if (value === undefined || value === null) return;
        chips.push(`<div class="frame-chip"><span class="frame-chip-label">${escapeHtml(label)}</span><span class="frame-chip-value mono badge-${tone}">${escapeHtml(value)}</span></div>`);
      };
      add('Slave ID', `0x${(frame.slave_id ?? 0).toString(16).toUpperCase().padStart(2, '0')} (${frame.slave_id})`, 'blue');
      add('Function', `0x${(frame.function_code ?? 0).toString(16).toUpperCase().padStart(2, '0')} · ${frame.function_name}`, 'purple');
      if (frame.exception_code !== undefined && frame.exception_code !== null) {
        add('Exception', `0x${frame.exception_code.toString(16).toUpperCase().padStart(2, '0')} · ${frame.exception_name}`, 'red');
      }
      if (frame.address !== undefined) add('Address', `0x${frame.address.toString(16).toUpperCase().padStart(4, '0')} (${frame.address})`, 'blue');
      if (frame.count !== undefined) add(frame.frame_kind === 'write_response' ? 'Count echo' : 'Count', String(frame.count), 'slate');
      if (frame.frame_kind === 'write_frame' && frame.value !== undefined) {
        add('Value', `0x${frame.value.toString(16).toUpperCase().padStart(4, '0')}${frame.coil_on === undefined ? '' : frame.coil_on ? ' (ON)' : ' (OFF)'}`, 'amber');
      }
      if (frame.byte_count !== undefined) add('Byte Count', String(frame.byte_count), 'slate');
      if (frame.data_hex) add('Data payload', frame.data_hex, 'green');
      if (Array.isArray(frame.values) && frame.values.length && frame.values.length <= 16) {
        add('Decoded', frame.values.join(', '), 'green');
      }
      return chips;
    }

    function crcChips(frame) {
      if (!frame.crc_present) {
        return '<div class="frame-chip"><span class="frame-chip-label">CRC16</span><span class="frame-chip-value badge-red">missing (frame too short)</span></div>';
      }
      const tone = frame.crc_valid ? 'green' : 'red';
      const verdict = frame.crc_valid ? 'PASS ✓' : 'FAIL ✗';
      return `
        <div class="frame-chip"><span class="frame-chip-label">CRC Low byte</span><span class="frame-chip-value mono badge-${tone}">0x${frame.crc_low.toString(16).toUpperCase().padStart(2, '0')}</span></div>
        <div class="frame-chip"><span class="frame-chip-label">CRC High byte</span><span class="frame-chip-value mono badge-${tone}">0x${frame.crc_high.toString(16).toUpperCase().padStart(2, '0')}</span></div>
        <div class="frame-chip"><span class="frame-chip-label">Computed CRC</span><span class="frame-chip-value mono badge-slate">0x${frame.crc_expected.toString(16).toUpperCase().padStart(4, '0')}</span></div>
        <div class="frame-chip"><span class="frame-chip-label">CRC validation</span><span class="frame-chip-value badge-${tone}">${verdict}</span></div>
      `;
    }

    function latencyWaterfall(transaction) {
      const latency = transaction.latency || {};
      const stages = Object.entries(INSPECTOR_STAGE_LABELS)
        .map(([key, label]) => ({ key, label, ms: latency[key] }))
        .filter((stage) => typeof stage.ms === 'number');
      const total = typeof transaction.duration_ms === 'number'
        ? transaction.duration_ms
        : stages.reduce((sum, stage) => sum + stage.ms, 0);
      if (!stages.length && !total) {
        return '<div class="text-sm" style="color:var(--text-dim);">No latency breakdown recorded for this transaction.</div>';
      }
      const rows = stages.map((stage) => {
        const pct = total > 0 ? Math.max(1.5, (stage.ms / total) * 100) : 0;
        return `<div class="waterfall-row">
          <span class="waterfall-label">${escapeHtml(stage.label)}</span>
          <span class="waterfall-track"><span class="waterfall-bar" style="width:${pct.toFixed(1)}%;"></span></span>
          <span class="waterfall-value mono">${formatMs(stage.ms)}</span>
        </div>`;
      }).join('');
      const tone = durationTone(total);
      return `${rows}
        <div class="waterfall-row waterfall-total">
          <span class="waterfall-label">Total transaction</span>
          <span class="waterfall-track"><span class="waterfall-bar waterfall-bar-total" style="width:100%;"></span></span>
          <span class="waterfall-value mono badge-${tone}">${formatMs(total)}</span>
        </div>`;
    }

    function renderInspectorDetail() {
      const detail = document.getElementById('inspector-detail');
      if (!detail) return;
      const view = _inspectorView;
      if (!view || _inspectorSelected === null || !view.transactions[_inspectorSelected]) {
        detail.innerHTML = '<div class="empty-box"><div class="empty-icon">🔍</div><h3>No transaction selected</h3><p>Pick a transaction on the left to decode its RTU frame.</p></div>';
        return;
      }
      const { transaction, frame } = view.transactions[_inspectorSelected];
      const frameSection = frame ? `
        <div class="inspector-detail-section">
          <div class="card-title" style="margin-bottom:0.5rem;">Frame analyzer</div>
          <div class="frame-hex mono">${escapeHtml(frame.raw_hex || transaction.request_hex || 'no frame recorded')}</div>
          <div class="frame-grid">${frameFieldChips(frame).join('')}</div>
          <div class="frame-grid" style="margin-top:0.5rem;">${crcChips(frame)}</div>
          ${(frame.errors || []).length ? `<div class="frame-errors">${frame.errors.map((message) => `<div>⚠️ ${escapeHtml(message)}</div>`).join('')}</div>` : ''}
        </div>` : '<div class="text-sm" style="color:var(--text-dim);">This transaction has no reconstructed request frame.</div>';

      detail.innerHTML = `
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(frame?.summary || transaction.operation || 'Transaction')}</div>
            <div class="card-subtitle">${escapeHtml(formatLogTime(transaction.timestamp))} · ${escapeHtml(transaction.operation || '')} · slave ${escapeHtml(transaction.slave ?? '—')}</div>
          </div>
          <span class="badge badge-${durationTone(transaction.duration_ms)}">${formatMs(transaction.duration_ms)}</span>
        </div>
        ${frameSection}
        <div class="inspector-detail-section">
          <div class="card-title" style="margin-bottom:0.5rem;">Latency waterfall</div>
          ${latencyWaterfall(transaction)}
        </div>
        ${transaction.error ? `<div class="frame-errors"><div>❌ ${escapeHtml(transaction.error)}</div></div>` : ''}
      `;
    }
