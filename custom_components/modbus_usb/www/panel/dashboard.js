/* dashboard.js — Dashboard tab: live readings, relay toggles, and filters.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    // ─── TAB 3: RENDER DASHBOARD ────────────────────────────────
    function renderDashboardTab() {
      const entry = getCurrentEntry();
      const devices = entry ? (entry.devices || []) : [];
      const entities = entry ? (entry.entities || []) : [];

      // Stats counters
      let sensors = 0, switches = 0, binary = 0, numbers = 0;
      entities.forEach(e => {
        if (e.entity_type === 'sensor') sensors++;
        else if (e.entity_type === 'switch') switches++;
        else if (e.entity_type === 'binary_sensor') binary++;
        else if (e.entity_type === 'number') numbers++;
      });
      document.getElementById('stat-total-dev').textContent = devices.length;
      document.getElementById('stat-total-ent').textContent = entities.length;
      document.getElementById('stat-sensors').textContent = sensors;
      document.getElementById('stat-switches').textContent = switches;
      document.getElementById('stat-binary').textContent = binary;
      document.getElementById('stat-numbers').textContent = numbers;

      // Filter chips
      const filterBar = document.getElementById('dashboard-filter-bar');
      filterBar.innerHTML = `
        <button class="filter-chip ${_dashboardFilter === 'all' ? 'active' : ''}" onclick="filterDashboard('all', this)">
          All Devices (${entities.length})
        </button>
        ${devices.map(d => {
          const dEnts = entities.filter(e => e.device_id === d.id);
          return `
            <button class="filter-chip ${_dashboardFilter === d.id ? 'active' : ''}" onclick="filterDashboard('${d.id}', this)">
              ${d.name} (${dEnts.length})
            </button>
          `;
        }).join('')}
      `;

      // Filter entities
      const displayEnts = _dashboardFilter === 'all'
        ? entities
        : entities.filter(e => e.device_id === _dashboardFilter);

      const grid = document.getElementById('dashboard-tile-grid');
      if (displayEnts.length === 0) {
        grid.innerHTML = `
          <div class="empty-box card" style="grid-column:1/-1;">
            <div class="empty-icon">📊</div>
            <h3>No Entities for this Selection</h3>
            <p>Add sensors, switches, or apply templates to view live metrics here.</p>
          </div>
        `;
        return;
      }

      grid.innerHTML = displayEnts.map(ent => {
        const type = ent.entity_type || 'sensor';
        const typeIcon = { sensor: '📈', switch: '💡', binary_sensor: '🔵', number: '🔢' }[type] || '📊';
        const tileAccent = {
          sensor: 'linear-gradient(90deg, #3b82f6, #6366f1)',
          switch: 'linear-gradient(90deg, #8b5cf6, #ec4899)',
          binary_sensor: 'linear-gradient(90deg, #f59e0b, #f97316)',
          number: 'linear-gradient(90deg, #10b981, #06b6d4)'
        }[type] || 'linear-gradient(90deg, #3b82f6, #8b5cf6)';

        const dev = devices.find(d => d.id === ent.device_id);
        const state = getLiveState(ent, dev);
        const val = state ? state.state : '—';
        const unit = ent.unit_of_measurement || state?.attributes?.unit_of_measurement || '';

        let valHtml = '';
        if (type === 'switch') {
          const isOn = val === 'on';
          valHtml = `
            <div style="margin: 0.5rem 0;">
              <label class="switch-toggle">
                <input type="checkbox" ${isOn ? 'checked' : ''} onchange="toggleEntitySwitch('${ent.id}', this.checked)">
                <span class="switch-slider"></span>
              </label>
              <span style="font-size:0.8rem; font-weight:700; margin-left:0.5rem; color:${isOn ? '#34d399' : '#94a3b8'}">${isOn ? 'ON' : 'OFF'}</span>
            </div>
          `;
        } else if (type === 'binary_sensor') {
          const isOn = val === 'on';
          valHtml = `<div class="tile-value" style="font-size:1.1rem; color:${isOn ? '#34d399' : '#94a3b8'};">${isOn ? '🟢 ACTIVE' : '⚫ IDLE'}</div>`;
        } else {
          const displayVal = val === null || val === 'unknown' || val === 'unavailable' ? '—' : parseFloat(val);
          const formatted = isNaN(displayVal) ? val : displayVal.toLocaleString(undefined, { maximumFractionDigits: 3 });
          valHtml = `<div class="tile-value">${formatted}<span style="font-size:0.75rem; font-weight:400; margin-left:3px; color:var(--text-dim);">${unit}</span></div>`;
        }

        return `
          <div class="entity-tile" style="--tile-accent: ${tileAccent};">
            <div class="tile-header">
              <span class="badge badge-slate" style="font-size:0.65rem;">${dev ? dev.name : 'Modbus'}</span>
              <span style="font-size:0.9rem;">${typeIcon}</span>
            </div>
            ${valHtml}
            <div class="tile-name" title="${ent.name}">${ent.name}</div>
            <div class="tile-sub mono">${ent.register_type || 'holding'} @ ${ent.address ?? 0}</div>
          </div>
        `;
      }).join('');
    }

    function filterDashboard(devId, btn) {
      _dashboardFilter = devId;
      document.querySelectorAll('#dashboard-filter-bar .filter-chip').forEach(c => c.classList.remove('active'));
      if (btn) btn.classList.add('active');
      renderDashboardTab();
    }
