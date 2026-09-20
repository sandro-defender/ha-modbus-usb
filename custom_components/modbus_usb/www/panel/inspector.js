/* inspector.js — Traffic Inspector tab: RTU frame analyzer & latency waterfall.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * The backend parses frames (custom_components/modbus_usb/inspector.py) and
 * captures real response bytes (custom_components/modbus_usb/capture.py);
 * this module renders the decoded view returned by modbus_usb/traffic_inspector
 * and keeps it live through the modbus_usb/subscribe_traffic WebSocket stream.
 */
    let _inspectorView = null;
    let _inspectorSelected = null;
    let _inspectorLoading = false;
    let _inspectorRenderTimer = null;

    const INSPECTOR_STREAM_LIMIT = 100;

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
        _inspectorView = await apiCall('traffic_inspector', { entry_id: entry.entry_id, limit: INSPECTOR_STREAM_LIMIT });
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

    // ─── Live WebSocket stream ──────────────────────────────────
    function setInspectorLive(live) {
      _inspectorLive = live;
      const badge = document.getElementById('inspector-live-badge');
      if (badge) badge.hidden = !live;
      const subtitle = document.getElementById('inspector-list-subtitle');
      if (subtitle) {
        subtitle.textContent = live
          ? 'Live stream active — new transactions appear as they happen.'
          : 'Select a transaction to decode its RTU frame.';
      }
    }

    async function ensureTrafficSubscription() {
      const pane = document.getElementById('pane-inspector');
      if (!pane || !pane.classList.contains('active')) return;
      const entry = getCurrentEntry();
      if (!_isLiveHA || !_hass?.connection?.subscribeMessage || !entry) {
        setInspectorLive(false);
        return;
      }
      if (_inspectorUnsubscribe && _inspectorStreamEntryId === entry.entry_id) return;
      await teardownTrafficSubscription();
      try {
        _inspectorStreamEntryId = entry.entry_id;
        _inspectorUnsubscribe = await _hass.connection.subscribeMessage(
          (message) => handleTrafficStreamMessage(message),
          { type: 'modbus_usb/subscribe_traffic', entry_id: entry.entry_id },
        );
        setInspectorLive(true);
      } catch (error) {
        // The reload button and periodic polling remain as a fallback when
        // the subscription command is unavailable (e.g. older integration).
        console.debug('Traffic stream unavailable, falling back to polling:', error);
        _inspectorUnsubscribe = null;
        _inspectorStreamEntryId = null;
        setInspectorLive(false);
      }
    }

    async function teardownTrafficSubscription() {
      const dispose = _inspectorUnsubscribe;
      _inspectorUnsubscribe = null;
      _inspectorStreamEntryId = null;
      setInspectorLive(false);
      if (dispose) {
        try { await dispose(); } catch (_) { /* connection already gone */ }
      }
    }

    function handleTrafficStreamMessage(message) {
      if (message?.type !== 'event' || !message.event?.transaction) return;
      if (_inspectorPaused) {
        _inspectorBuffered += 1;
        updateInspectorPauseButton();
        return;
      }
      pushInspectorTransaction(message.event.transaction);
    }

    function pushInspectorTransaction(analyzed) {
      if (!_inspectorView || !Array.isArray(_inspectorView.transactions)) {
        loadTrafficInspector(true);
        return;
      }
      _inspectorView.transactions.unshift(analyzed);
      if (_inspectorView.transactions.length > INSPECTOR_STREAM_LIMIT) {
        _inspectorView.transactions.length = INSPECTOR_STREAM_LIMIT;
      }
      if (_inspectorSelected !== null) {
        _inspectorSelected += 1;
        if (_inspectorSelected >= _inspectorView.transactions.length) _inspectorSelected = null;
      }
      const txn = analyzed.transaction || {};
      const stats = _inspectorView.stats || (_inspectorView.stats = {});
      stats.total = (stats.total || 0) + 1;
      if (txn.status === 'error') stats.errors = (stats.errors || 0) + 1;
      if (txn.response_hex) stats.responses = (stats.responses || 0) + 1;
      if (txn.slave != null) {
        const perSlave = _inspectorView.per_slave || (_inspectorView.per_slave = {});
        const bucket = perSlave[String(txn.slave)] || (perSlave[String(txn.slave)] = {
          count: 0, errors: 0, samples: 0, avg_ms: null, min_ms: null, max_ms: null, p95_ms: null, last_seen: null,
        });
        const duration = txn.duration_ms;
        if (typeof duration === 'number') {
          const samples = bucket.samples || 0;
          bucket.avg_ms = samples
            ? Math.round((((bucket.avg_ms || 0) * samples) + duration) / (samples + 1) * 10) / 10
            : Math.round(duration * 10) / 10;
          bucket.min_ms = bucket.min_ms == null ? duration : Math.min(bucket.min_ms, duration);
          bucket.max_ms = bucket.max_ms == null ? duration : Math.max(bucket.max_ms, duration);
          bucket.p95_ms = bucket.max_ms;
          bucket.samples = samples + 1;
        }
        bucket.count += 1;
        if (txn.status === 'error') bucket.errors += 1;
        bucket.last_seen = txn.timestamp;
      }
      scheduleInspectorRender();
    }

    function scheduleInspectorRender() {
      // Fast polling cycles can push several transactions per second; batch
      // them into at most one DOM update every 250 ms.
      if (_inspectorRenderTimer) return;
      _inspectorRenderTimer = setTimeout(() => {
        _inspectorRenderTimer = null;
        renderInspectorTab();
      }, 250);
    }

    function toggleInspectorPause() {
      _inspectorPaused = !_inspectorPaused;
      if (_inspectorPaused) {
        updateInspectorPauseButton();
        return;
      }
      const missed = _inspectorBuffered;
      _inspectorBuffered = 0;
      updateInspectorPauseButton();
      // Re-sync with the server log so nothing recorded while paused is lost.
      loadTrafficInspector(true);
      if (missed) toast(`Stream resumed — reloaded after ${missed} paused transaction(s)`, 'inf');
    }

    function updateInspectorPauseButton() {
      const button = document.getElementById('btn-inspector-pause');
      if (!button) return;
      button.textContent = _inspectorPaused
        ? `▶ Resume stream${_inspectorBuffered ? ` (${_inspectorBuffered})` : ''}`
        : '⏸ Pause stream';
      button.classList.toggle('btn-primary', _inspectorPaused);
      button.classList.toggle('btn-secondary', !_inspectorPaused);
    }

    // ─── Rendering ──────────────────────────────────────────────
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
        <div class="stat-card"><div class="stat-num" style="color:#2dd4bf;">${stats.responses ?? 0}</div><div class="stat-label">RX captured</div></div>
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
        const responseBadge = item.response_frame
          ? (item.response_frame.frame_kind === 'exception_response'
            ? '<span class="badge badge-red" title="Exception response captured from the bus">RX EXC</span>'
            : '<span class="badge badge-blue" title="Real response bytes captured from the bus">RX</span>')
          : '<span class="badge badge-slate" title="No raw response bytes captured">no RX</span>';
        const selected = index === _inspectorSelected ? ' selected' : '';
        return `<button class="inspector-row${selected}" onclick="selectInspectorTransaction(${index})" aria-pressed="${index === _inspectorSelected}">
          <span class="inspector-row-time">${escapeHtml(formatLogTime(txn.timestamp))}</span>
          <span class="inspector-row-op mono">${escapeHtml(txn.operation || '—')} · slave ${escapeHtml(txn.slave ?? '—')}</span>
          <span class="inspector-row-frame mono">${escapeHtml(frame.summary || frame.function_code || txn.function_code || 'no frame')}</span>
          <span class="inspector-row-badges">${responseBadge}${statusBadge}${durationBadge}</span>
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

    function frameAnalyzerSection(title, frame, fallbackHtml) {
      if (!frame) {
        return `<div class="inspector-detail-section">
          <div class="card-title" style="margin-bottom:0.5rem;">${escapeHtml(title)}</div>
          ${fallbackHtml}
        </div>`;
      }
      return `<div class="inspector-detail-section">
        <div class="card-title" style="margin-bottom:0.5rem;">${escapeHtml(title)}</div>
        <div class="frame-hex mono">${escapeHtml(frame.raw_hex || 'no frame recorded')}</div>
        <div class="frame-grid">${frameFieldChips(frame).join('')}</div>
        <div class="frame-grid" style="margin-top:0.5rem;">${crcChips(frame)}</div>
        ${(frame.errors || []).length ? `<div class="frame-errors">${frame.errors.map((message) => `<div>⚠️ ${escapeHtml(message)}</div>`).join('')}</div>` : ''}
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
      const { transaction, frame, response_frame: responseFrame } = view.transactions[_inspectorSelected];
      const requestFallback = '<div class="text-sm" style="color:var(--text-dim);">This transaction has no request frame — the operation could not be captured or reconstructed.</div>';
      const responseFallback = `<div class="text-sm" style="color:var(--text-dim);">${transaction.status === 'error'
        ? 'No response bytes captured — the board did not answer or the reply was unreadable.'
        : (view.capture && view.capture.supported === false)
          ? 'Raw response capture is unavailable: this pymodbus release exposes no transaction tracing hook, so only the request frame is shown.'
          : 'No raw response bytes were captured for this transaction.'}</div>`;

      detail.innerHTML = `
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(frame?.summary || transaction.operation || 'Transaction')}</div>
            <div class="card-subtitle">${escapeHtml(formatLogTime(transaction.timestamp))} · ${escapeHtml(transaction.operation || '')} · slave ${escapeHtml(transaction.slave ?? '—')}${transaction.request_captured ? ' · <span class="badge badge-blue">TX captured</span>' : ''}</div>
          </div>
          <span class="badge badge-${durationTone(transaction.duration_ms)}">${formatMs(transaction.duration_ms)}</span>
        </div>
        ${frameAnalyzerSection('Request frame (TX)', frame, requestFallback)}
        ${frameAnalyzerSection('Response frame (RX)', responseFrame, responseFallback)}
        <div class="inspector-detail-section">
          <div class="card-title" style="margin-bottom:0.5rem;">Latency waterfall</div>
          ${latencyWaterfall(transaction)}
        </div>
        ${transaction.error ? `<div class="frame-errors"><div>❌ ${escapeHtml(transaction.error)}</div></div>` : ''}
      `;
    }
