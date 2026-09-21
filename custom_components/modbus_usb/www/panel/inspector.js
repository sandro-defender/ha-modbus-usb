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

    // v2.7.1: function codes offered by the FC filter (two-digit hex, as
    // shown in the select) with their Modbus names.
    const INSPECTOR_FUNCTION_CODES = [
      ['01', 'Read Coils'],
      ['02', 'Read Discrete Inputs'],
      ['03', 'Read Holding Registers'],
      ['04', 'Read Input Registers'],
      ['05', 'Write Single Coil'],
      ['06', 'Write Single Register'],
      ['0F', 'Write Multiple Coils'],
      ['10', 'Write Multiple Registers'],
    ];

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
      stats.capture_coverage = inspectorCaptureCoverage(stats);
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

    // ─── v2.7.0: client-side filters ────────────────────────────
    // The filters run live in the browser over the already-decoded
    // transaction list; the WebSocket stream, pause/resume, and reload
    // behavior are untouched — filtered rows simply hide, and new pushes
    // respect the active filters. The filter state lives in state.js so it
    // persists across tab switches. v2.7.1 adds a function-code filter
    // (FC01–FC10 + exception) and named saved views (localStorage presets
    // re-applied whenever the tab opens).

    function normalizeInspectorHexQuery(text) {
      return String(text ?? '')
        .replace(/0[xX]/g, '')
        .replace(/[^0-9a-fA-F]/g, '')
        .toLowerCase();
    }

    function normalizeInspectorFilters(raw) {
      const source = raw && typeof raw === 'object' ? raw : {};
      return {
        slave: source.slave == null || source.slave === '' ? 'all' : String(source.slave),
        status: typeof source.status === 'string' && source.status ? source.status : 'all',
        fc: normalizeInspectorFunctionCode(source.fc),
        hex: typeof source.hex === 'string' ? source.hex : '',
      };
    }

    function normalizeInspectorFunctionCode(value) {
      // Accepts 'all', 'exception', '03', '0x03', 3, 'FC03' → 'all' | 'exception' | '03'.
      if (value == null || value === '' || value === 'all') return 'all';
      if (String(value).toLowerCase() === 'exception') return 'exception';
      const digits = String(value).replace(/^(fc|0x)/i, '').trim();
      const parsed = typeof value === 'number' ? value : parseInt(digits, 16);
      if (!Number.isFinite(parsed) || parsed < 0 || parsed > 0xFF) return 'all';
      return parsed.toString(16).toUpperCase().padStart(2, '0');
    }

    function inspectorResponseFrameList(item) {
      if (Array.isArray(item.response_frames) && item.response_frames.length) {
        return item.response_frames;
      }
      return item.response_frame ? [item.response_frame] : [];
    }

    function inspectorTransactionHasException(item) {
      return inspectorResponseFrameList(item).some(
        (frame) => frame && frame.frame_kind === 'exception_response'
      );
    }

    function inspectorTransactionFunctionCodes(item) {
      // Every function code involved in the transaction: the logged code,
      // the request frame, and each captured response frame (exception
      // frames contribute their base function code so an FC03 exception
      // still matches the FC03 filter).
      const codes = new Set();
      const add = (value) => {
        const normalized = normalizeInspectorFunctionCode(value);
        if (normalized !== 'all' && normalized !== 'exception') codes.add(normalized);
      };
      const txn = item.transaction || {};
      if (txn.function_code != null) add(txn.function_code);
      if (item.frame && typeof item.frame.function_code === 'number') add(item.frame.function_code);
      inspectorResponseFrameList(item).forEach((frame) => {
        if (!frame) return;
        if (frame.frame_kind === 'exception_response') {
          if (typeof frame.base_function_code === 'number') add(frame.base_function_code);
        } else if (typeof frame.function_code === 'number') {
          add(frame.function_code);
        }
      });
      return codes;
    }

    function transactionMatchesFunctionCodeFilter(item, fc) {
      const wanted = normalizeInspectorFunctionCode(fc);
      if (wanted === 'all') return true;
      if (wanted === 'exception') return inspectorTransactionHasException(item);
      return inspectorTransactionFunctionCodes(item).has(wanted);
    }

    function inspectorTransactionHexHaystack(item) {
      const txn = item.transaction || {};
      const parts = [];
      if (txn.request_hex) parts.push(txn.request_hex);
      if (txn.response_hex) parts.push(txn.response_hex);
      if (Array.isArray(txn.response_frames)) {
        txn.response_frames.forEach((frameHex) => { if (frameHex) parts.push(frameHex); });
      }
      // Parsed frames carry raw_hex too, which also covers pre-parsed views.
      inspectorResponseFrameList(item).forEach((frame) => {
        if (frame && frame.raw_hex) parts.push(frame.raw_hex);
      });
      if (item.frame && item.frame.raw_hex) parts.push(item.frame.raw_hex);
      return normalizeInspectorHexQuery(parts.join(' '));
    }

    function transactionMatchesInspectorFilters(item) {
      const filters = normalizeInspectorFilters(_inspectorFilters);
      const txn = item.transaction || {};
      if (filters.slave !== 'all' && String(txn.slave ?? '') !== String(filters.slave)) {
        return false;
      }
      if (filters.status !== 'all') {
        if (filters.status === 'ok' && txn.status !== 'ok') return false;
        if (filters.status === 'error' && txn.status !== 'error') return false;
        if (filters.status === 'exception' && !inspectorTransactionHasException(item)) return false;
      }
      if (!transactionMatchesFunctionCodeFilter(item, filters.fc)) return false;
      const needle = normalizeInspectorHexQuery(filters.hex);
      if (needle && !inspectorTransactionHexHaystack(item).includes(needle)) {
        return false;
      }
      return true;
    }

    function getFilteredTransactionIndices() {
      const view = _inspectorView;
      if (!view || !Array.isArray(view.transactions)) return [];
      return view.transactions
        .map((item, index) => (transactionMatchesInspectorFilters(item) ? index : -1))
        .filter((index) => index !== -1);
    }

    function activeInspectorFilterCount() {
      const filters = normalizeInspectorFilters(_inspectorFilters);
      let count = 0;
      if (filters.slave !== 'all') count += 1;
      if (filters.status !== 'all') count += 1;
      if (filters.fc !== 'all') count += 1;
      if (normalizeInspectorHexQuery(filters.hex)) count += 1;
      return count;
    }

    function inspectorSlaveOptions() {
      const view = _inspectorView;
      const slaves = new Set();
      if (view) {
        Object.keys(view.per_slave || {}).forEach((slave) => slaves.add(String(slave)));
        (view.transactions || []).forEach((item) => {
          const slave = (item.transaction || {}).slave;
          if (slave != null) slaves.add(String(slave));
        });
      }
      // A slave selected by a filter or saved view stays selectable even
      // before that slave shows up in the recent traffic.
      const filters = normalizeInspectorFilters(_inspectorFilters);
      if (filters.slave !== 'all') slaves.add(String(filters.slave));
      return [...slaves].sort((a, b) => Number(a) - Number(b));
    }

    function setInspectorFilter(key, value) {
      _inspectorFilters = normalizeInspectorFilters(_inspectorFilters);
      _inspectorFilters[key] = key === 'fc' ? normalizeInspectorFunctionCode(value) : value;
      // Hand-edited filters are no longer the saved view; the edited
      // filters stay, only the preset selection is dropped.
      if (_inspectorActivePreset) {
        _inspectorActivePreset = null;
        persistInspectorFilterPresets();
      }
      renderInspectorFilterBar();
      renderInspectorTab();
    }

    function clearInspectorFilters() {
      _inspectorFilters = { ...INSPECTOR_DEFAULT_FILTERS };
      if (_inspectorActivePreset) {
        _inspectorActivePreset = null;
        persistInspectorFilterPresets();
      }
      syncInspectorFilterControls();
      renderInspectorFilterBar();
      renderInspectorTab();
    }

    function syncInspectorFilterControls() {
      // Push the shared filter state into the static filter controls
      // (used after presets and clears; typing is never clobbered because
      // renderInspectorFilterBar skips the focused hex input).
      const filters = normalizeInspectorFilters(_inspectorFilters);
      const slave = document.getElementById('inspector-filter-slave');
      const status = document.getElementById('inspector-filter-status');
      const fc = document.getElementById('inspector-filter-fc');
      const hex = document.getElementById('inspector-filter-hex');
      if (slave) slave.value = filters.slave;
      if (status) status.value = filters.status;
      if (fc) fc.value = filters.fc;
      if (hex) hex.value = filters.hex;
    }

    // ─── v2.7.1: filter saved views (presets) ───────────────────

    function describeInspectorFilters(filters) {
      const normalized = normalizeInspectorFilters(filters);
      const parts = [];
      if (normalized.slave !== 'all') parts.push(`slave ${normalized.slave}`);
      if (normalized.status !== 'all') parts.push(normalized.status);
      if (normalized.fc !== 'all') parts.push(normalized.fc === 'exception' ? 'exception FC' : `FC${normalized.fc}`);
      if (normalizeInspectorHexQuery(normalized.hex)) parts.push(`hex "${normalized.hex.trim()}"`);
      return parts.length ? parts.join(' · ') : 'no filters';
    }

    function applyInspectorFilterPreset(name, options = {}) {
      const preset = (_inspectorFilterPresets || []).find((candidate) => candidate.name === name);
      if (!preset) {
        // Blank option (or a preset deleted in another tab): keep the
        // current filters, just drop the selection.
        if (_inspectorActivePreset) {
          _inspectorActivePreset = null;
          persistInspectorFilterPresets();
        }
        renderInspectorPresetBar();
        return false;
      }
      _inspectorFilters = normalizeInspectorFilters(preset.filters);
      _inspectorActivePreset = preset.name;
      persistInspectorFilterPresets();
      syncInspectorFilterControls();
      renderInspectorFilterBar();
      renderInspectorTab();
      if (!options.silent) toast(`Applied view "${preset.name}" — ${describeInspectorFilters(preset.filters)}`, 'inf');
      return true;
    }

    function applyStoredInspectorPreset() {
      // Called when the inspector tab opens: the persisted active view
      // (if any) is re-applied so it survives page reloads.
      if (!_inspectorActivePreset) {
        syncInspectorFilterControls();
        renderInspectorPresetBar();
        return false;
      }
      return applyInspectorFilterPreset(_inspectorActivePreset, { silent: true });
    }

    function saveInspectorFilterPreset() {
      if (!activeInspectorFilterCount()) {
        toast('Set at least one filter before saving a view', 'err');
        return;
      }
      const suggested = _inspectorActivePreset || describeInspectorFilters(_inspectorFilters);
      const input = window.prompt('Name for this saved view:', suggested);
      if (input === null) return;
      const name = input.trim().slice(0, 48);
      if (!name) {
        toast('A saved view needs a name', 'err');
        return;
      }
      const filters = normalizeInspectorFilters(_inspectorFilters);
      const existing = _inspectorFilterPresets.findIndex((preset) => preset.name === name);
      if (existing !== -1) {
        _inspectorFilterPresets[existing] = { name, filters };
      } else {
        if (_inspectorFilterPresets.length >= INSPECTOR_MAX_PRESETS) {
          toast(`At most ${INSPECTOR_MAX_PRESETS} saved views are kept — delete one first`, 'err');
          return;
        }
        _inspectorFilterPresets.push({ name, filters });
      }
      _inspectorActivePreset = name;
      persistInspectorFilterPresets();
      renderInspectorPresetBar();
      toast(`${existing !== -1 ? 'Updated' : 'Saved'} view "${name}"`, 'ok');
    }

    function deleteInspectorFilterPreset() {
      const name = _inspectorActivePreset;
      if (!name) return;
      if (!window.confirm(`Delete the saved view "${name}"?`)) return;
      _inspectorFilterPresets = _inspectorFilterPresets.filter((preset) => preset.name !== name);
      _inspectorActivePreset = null;
      persistInspectorFilterPresets();
      renderInspectorPresetBar();
      toast(`Deleted view "${name}"`, 'ok');
    }

    function renderInspectorPresetBar() {
      const select = document.getElementById('inspector-filter-preset');
      const deleteButton = document.getElementById('btn-inspector-delete-preset');
      if (select) {
        const presets = _inspectorFilterPresets || [];
        select.innerHTML = '<option value="">Saved views…</option>' + presets.map((preset) =>
          `<option value="${escapeHtml(preset.name)}" title="${escapeHtml(describeInspectorFilters(preset.filters))}">${escapeHtml(preset.name)}</option>`).join('');
        select.value = _inspectorActivePreset && presets.some((preset) => preset.name === _inspectorActivePreset)
          ? _inspectorActivePreset
          : '';
      }
      if (deleteButton) deleteButton.hidden = !_inspectorActivePreset;
    }

    function renderInspectorFilterBar() {
      const filters = normalizeInspectorFilters(_inspectorFilters);
      const slave = document.getElementById('inspector-filter-slave');
      const status = document.getElementById('inspector-filter-status');
      const fc = document.getElementById('inspector-filter-fc');
      const hex = document.getElementById('inspector-filter-hex');
      const clear = document.getElementById('btn-inspector-clear-filters');
      const count = document.getElementById('inspector-filter-count');
      if (slave) {
        const options = inspectorSlaveOptions();
        // Rebuild options without losing focus/selection on the other controls.
        slave.innerHTML = '<option value="all">all</option>' + options.map((s) =>
          `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
        slave.value = filters.slave;
      }
      if (status && status.value !== filters.status) status.value = filters.status;
      if (fc && fc.value !== filters.fc) fc.value = filters.fc;
      // Never clobber the hex input while the user is typing in it.
      if (hex && document.activeElement !== hex && hex.value !== filters.hex) {
        hex.value = filters.hex;
      }
      if (clear) clear.hidden = activeInspectorFilterCount() === 0;
      if (count) {
        const total = _inspectorView?.transactions?.length ?? 0;
        const shown = getFilteredTransactionIndices().length;
        count.textContent = activeInspectorFilterCount() ? `${shown} / ${total}` : '';
      }
      renderInspectorPresetBar();
    }

    // ─── Rendering ───────────────────────────────────────────────
    function renderInspectorTab() {
      const statsBar = document.getElementById('inspector-stats');
      const list = document.getElementById('inspector-list');
      if (!statsBar || !list) return;
      renderInspectorFilterBar();
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
      const coverage = captureCoverageStat(view);

      statsBar.innerHTML = `
        <div class="stat-card"><div class="stat-num" style="color:#60a5fa;">${stats.total ?? 0}</div><div class="stat-label">Transactions</div></div>
        <div class="stat-card"><div class="stat-num" style="color:${stats.errors ? '#f87171' : '#34d399'};">${stats.errors ?? 0}</div><div class="stat-label">Errors</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#2dd4bf;">${stats.responses ?? 0}</div><div class="stat-label">RX captured</div></div>
        <div class="stat-card" title="${escapeHtml(coverage.title)}"><div class="stat-num" style="color:${coverage.color};">${escapeHtml(coverage.text)}</div><div class="stat-label">Capture coverage</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#38bdf8;">${formatMs(stats.avg_ms)}</div><div class="stat-label">Avg duration</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#fbbf24;">${formatMs(stats.p95_ms)}</div><div class="stat-label">p95 duration</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#c084fc;">${formatMs(stats.max_ms)}</div><div class="stat-label">Slowest</div></div>
        <div class="stat-card inspector-slave-chips">${slaveChips || '<span class="badge badge-slate">no per-slave data</span>'}</div>
      `;

      if (!view.transactions.length) {
        list.innerHTML = '<div class="empty-box"><div class="empty-icon">📡</div><h3>No bus traffic recorded yet</h3><p>Transactions appear here as soon as the hub talks to a board.</p></div>';
        return;
      }

      // v2.7.0: the live list is filtered client-side; only matching
      // transactions render, but the selection indexes the full list.
      const indices = getFilteredTransactionIndices();
      if (!indices.length) {
        list.innerHTML = '<div class="empty-box"><div class="empty-icon">🔍</div><h3>No transactions match the filters</h3><p>Adjust the slave, status, function code, or hex filter above — or clear it to see the whole stream again.</p></div>';
        renderInspectorDetail();
        return;
      }

      list.innerHTML = indices.map((index) => {
        const item = view.transactions[index];
        const txn = item.transaction || {};
        const frame = item.frame || {};
        const statusBadge = txn.status === 'error'
          ? '<span class="badge badge-red">error</span>'
          : '<span class="badge badge-green">ok</span>';
        const durationBadge = `<span class="badge badge-${durationTone(txn.duration_ms)}">${formatMs(txn.duration_ms)}</span>`;
        const responseFrames = inspectorResponseFrameList(item);
        const responseBadge = responseFrames.length
          ? (responseFrames.length > 1
            ? `<span class="badge badge-purple" title="${responseFrames.length} response frames captured from the bus (batch or multi-read)">RX ×${responseFrames.length}</span>`
            : (responseFrames[0].frame_kind === 'exception_response'
              ? '<span class="badge badge-red" title="Exception response captured from the bus">RX EXC</span>'
              : '<span class="badge badge-blue" title="Real response bytes captured from the bus">RX</span>'))
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

    // ─── v2.7.1: capture coverage stat ──────────────────────────
    function inspectorCaptureCoverage(stats) {
      // Fraction (0–1) of transactions whose RX bytes were captured. Derived
      // live from the running counters so streamed pushes keep it current;
      // the server's capture_coverage is the fallback for canned views.
      if (!stats) return null;
      const total = Number(stats.total);
      if (Number.isFinite(total) && total > 0) {
        const responses = Number(stats.responses) || 0;
        return Math.min(1, Math.max(0, responses / total));
      }
      return typeof stats.capture_coverage === 'number' ? stats.capture_coverage : null;
    }

    function captureCoverageStat(view) {
      const stats = (view && view.stats) || {};
      if (view && view.capture && view.capture.supported === false) {
        return { text: 'n/a', color: '#94a3b8', title: 'Raw response capture is unavailable on this pymodbus release (no tracing hook).' };
      }
      const coverage = inspectorCaptureCoverage(stats);
      if (coverage === null) {
        return { text: '—', color: '#94a3b8', title: 'No transactions recorded yet.' };
      }
      const percent = Math.round(coverage * 100);
      const color = coverage >= 0.9 ? '#34d399' : coverage >= 0.5 ? '#fbbf24' : '#f87171';
      return {
        text: `${percent}%`,
        color,
        title: `${stats.responses ?? 0} of ${stats.total ?? 0} transactions have their RX bytes captured from the wire.`,
      };
    }

    // ─── v2.7.1: RX inter-frame gap mini waterfall ───────────────
    function responseFrameTimings(frames, transaction) {
      // Per-frame arrival offsets (ms since the request window opened) and
      // the gap since the previous frame; taken from the decoded frames
      // (inspector.py attaches arrival_ms/gap_ms) or, for pre-parsed views,
      // recomputed from transaction.response_frame_times_ms.
      const fallback = Array.isArray(transaction?.response_frame_times_ms)
        && transaction.response_frame_times_ms.length === frames.length
        ? transaction.response_frame_times_ms
        : null;
      let previous = 0;
      const timings = frames.map((frame, index) => {
        let arrival = frame && typeof frame.arrival_ms === 'number' ? frame.arrival_ms : null;
        if (arrival === null && fallback && typeof fallback[index] === 'number') arrival = fallback[index];
        if (arrival === null) return null;
        const gap = frame && typeof frame.gap_ms === 'number' ? frame.gap_ms : Math.max(0, arrival - previous);
        previous = arrival;
        return { arrival, gap };
      });
      return timings.every((timing) => timing !== null) ? timings : null;
    }

    function rxFrameWaterfall(frames, transaction) {
      const timings = responseFrameTimings(frames, transaction);
      if (!timings) {
        return '<div class="text-sm" style="color:var(--text-dim);">No per-frame arrival times recorded for this transaction (captured before v2.7.1).</div>';
      }
      const lastArrival = timings[timings.length - 1].arrival;
      const total = Math.max(
        lastArrival,
        typeof transaction?.duration_ms === 'number' ? transaction.duration_ms : 0,
        0.001,
      );
      const rows = timings.map((timing, index) => {
        const start = Math.max(0, timing.arrival - timing.gap);
        const left = (start / total) * 100;
        const width = Math.max(1.2, (timing.gap / total) * 100);
        const frame = frames[index] || {};
        const label = index === 0 ? 'after request' : `after #${index}`;
        const exc = frame.frame_kind === 'exception_response' ? ' rx-waterfall-bar-exc' : '';
        return `<div class="waterfall-row rx-waterfall-row" title="Frame #${index + 1} arrived ${formatMs(timing.arrival)} after the request window opened (${formatMs(timing.gap)} ${label})">
          <span class="waterfall-label mono">#${index + 1} <span class="rx-waterfall-gap-label">${escapeHtml(label)}</span></span>
          <span class="waterfall-track"><span class="waterfall-bar rx-waterfall-bar${exc}" style="left:${left.toFixed(2)}%; width:${Math.min(width, 100 - left).toFixed(2)}%;"></span><span class="rx-waterfall-tick" style="left:${((timing.arrival / total) * 100).toFixed(2)}%;"></span></span>
          <span class="waterfall-value mono">+${formatMs(timing.gap)}</span>
        </div>`;
      }).join('');
      return `<div class="rx-waterfall">
        ${rows}
        <div class="waterfall-row waterfall-total rx-waterfall-row">
          <span class="waterfall-label">Last frame at</span>
          <span class="waterfall-track"><span class="waterfall-bar waterfall-bar-total" style="width:${((lastArrival / total) * 100).toFixed(2)}%;"></span></span>
          <span class="waterfall-value mono badge-${durationTone(lastArrival)}">${formatMs(lastArrival)}</span>
        </div>
      </div>`;
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
      if (typeof frame.arrival_ms === 'number') {
        // v2.7.1: arrival offset of a captured RX frame since the request.
        add('Arrival', `t+${formatMs(frame.arrival_ms)}`, 'slate');
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

    function multiFrameResponseSection(frames, transaction) {
      // v2.7.0: a full raw RX stream (batch reads, block readers, exception
      // plus follow-up) renders as a list of decoded frames instead of only
      // the last pair. v2.7.1: the per-frame arrival times render as a mini
      // waterfall of inter-frame gaps above the list, and each frame shows
      // its arrival offset.
      const timings = responseFrameTimings(frames, transaction) || [];
      return `<div class="inspector-detail-section">
        <div class="card-title" style="margin-bottom:0.5rem;">Response frames (RX) — ${frames.length} frames</div>
        <div class="rx-waterfall-card">
          <div class="rx-waterfall-title">Inter-frame gaps</div>
          ${rxFrameWaterfall(frames, transaction)}
        </div>
        <div class="inspector-frame-list">
          ${frames.map((frame, index) => `
            <div class="inspector-frame-item">
              <div class="inspector-frame-item-head">
                <span class="inspector-frame-item-index mono">#${index + 1}</span>
                <span class="inspector-frame-item-summary mono">${escapeHtml(frame.summary || frame.raw_hex || 'unparsed frame')}</span>
                ${timings[index] ? `<span class="badge badge-slate mono" title="Arrival ${formatMs(timings[index].arrival)} after the request window opened">t+${formatMs(timings[index].arrival)}</span>` : ''}
                ${frame.frame_kind === 'exception_response' ? '<span class="badge badge-red">EXC</span>' : ''}
              </div>
              <div class="frame-hex mono">${escapeHtml(frame.raw_hex || 'no frame recorded')}</div>
              <div class="frame-grid">${frameFieldChips(frame).join('')}</div>
              <div class="frame-grid" style="margin-top:0.5rem;">${crcChips(frame)}</div>
              ${(frame.errors || []).length ? `<div class="frame-errors">${frame.errors.map((message) => `<div>⚠️ ${escapeHtml(message)}</div>`).join('')}</div>` : ''}
            </div>`).join('')}
        </div>
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
      const {
        transaction,
        frame,
        response_frame: responseFrame,
        response_frames: responseFramesRaw,
      } = view.transactions[_inspectorSelected];
      const responseFrames = (Array.isArray(responseFramesRaw) && responseFramesRaw.length)
        ? responseFramesRaw
        : (responseFrame ? [responseFrame] : []);
      const requestFallback = '<div class="text-sm" style="color:var(--text-dim);">This transaction has no request frame — the operation could not be captured or reconstructed.</div>';
      const responseFallback = `<div class="text-sm" style="color:var(--text-dim);">${transaction.status === 'error'
        ? 'No response bytes captured — the board did not answer or the reply was unreadable.'
        : (view.capture && view.capture.supported === false)
          ? 'Raw response capture is unavailable: this pymodbus release exposes no transaction tracing hook, so only the request frame is shown.'
          : 'No raw response bytes were captured for this transaction.'}</div>`;
      const rxSummary = responseFrames.length > 1
        ? ` · <span class="badge badge-purple">${responseFrames.length} RX frames</span>`
        : '';

      detail.innerHTML = `
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(frame?.summary || transaction.operation || 'Transaction')}</div>
            <div class="card-subtitle">${escapeHtml(formatLogTime(transaction.timestamp))} · ${escapeHtml(transaction.operation || '')} · slave ${escapeHtml(transaction.slave ?? '—')}${transaction.request_captured ? ' · <span class="badge badge-blue">TX captured</span>' : ''}${rxSummary}</div>
          </div>
          <span class="badge badge-${durationTone(transaction.duration_ms)}">${formatMs(transaction.duration_ms)}</span>
        </div>
        ${frameAnalyzerSection('Request frame (TX)', frame, requestFallback)}
        ${responseFrames.length > 1
          ? multiFrameResponseSection(responseFrames, transaction)
          : frameAnalyzerSection('Response frame (RX)', responseFrame, responseFallback)}
        <div class="inspector-detail-section">
          <div class="card-title" style="margin-bottom:0.5rem;">Latency waterfall</div>
          ${latencyWaterfall(transaction)}
        </div>
        ${transaction.error ? `<div class="frame-errors"><div>❌ ${escapeHtml(transaction.error)}</div></div>` : ''}
      `;
    }
