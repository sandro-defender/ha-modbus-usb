/* header.js — Header actions: update check/install/restart and hub selection.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    function renderUpdateAction() {
      const button = document.getElementById('btn-check-update');
      if (!button) return;
      const updateAvailable = Boolean(_availableUpdate?.update_available);
      button.classList.toggle('btn-primary', updateAvailable && !_restartPending);
      button.classList.toggle('btn-secondary', _restartPending);
      button.classList.toggle('btn-ghost', !updateAvailable && !_restartPending);
      button.textContent = _restartPending
        ? 'Restart Home Assistant'
        : updateAvailable ? `Update ${_availableUpdate.latest_version}` : 'Check update';
      button.setAttribute('aria-label', _restartPending
        ? 'Restart Home Assistant to finish the integration update'
        : updateAvailable ? `Install version ${_availableUpdate.latest_version}` : 'Check for integration update');
      button.title = _restartPending
        ? 'Restart Home Assistant to load the installed update'
        : updateAvailable ? `Install version ${_availableUpdate.latest_version}` : 'Check HACS and GitHub for an update';
    }

    async function handleUpdateAction() {
      if (_restartPending) {
        await restartHomeAssistant();
      } else if (_availableUpdate?.update_available) {
        await installAvailableUpdate();
      } else {
        await checkForUpdate();
      }
    }

    async function refreshHacsRepositoryInformation() {
      if (!_isLiveHA || !_hass?.callWS) return false;

      try {
        const repositories = await _hass.callWS({
          type: 'hacs/repositories/list',
          categories: ['integration'],
        });
        const repository = (Array.isArray(repositories) ? repositories : []).find((item) =>
          String(item.full_name || '').toLowerCase() === HACS_REPOSITORY,
        );
        if (!repository?.id) return false;

        await _hass.callWS({
          type: 'hacs/repository/refresh',
          repository: String(repository.id),
        });
        return true;
      } catch (error) {
        // HACS is optional and refresh requires an administrator. The GitHub
        // check below remains useful when either condition is not met.
        console.debug('Could not refresh HACS repository information:', error);
        return false;
      }
    }

    async function checkForUpdate() {
      const button = document.getElementById('btn-check-update');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = 'Refreshing HACS…';
      try {
        const hacsRefreshed = await refreshHacsRepositoryInformation();
        button.textContent = 'Checking…';
        const update = await apiCall('check_update');
        _availableUpdate = update;
        if (update.update_available) {
          toast(`${hacsRefreshed ? 'HACS refreshed. ' : ''}Version ${update.latest_version} is available`, 'ok');
        } else {
          toast(`${hacsRefreshed ? 'HACS refreshed — ' : ''}you are up to date (${update.current_version})`, 'ok');
        }
      } catch (error) {
        _availableUpdate = null;
        toast(`Could not check for an update: ${error.message}`, 'err');
      } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        renderUpdateAction();
      }
    }

    async function installAvailableUpdate() {
      if (!_availableUpdate?.update_available) {
        await checkForUpdate();
        return;
      }
      const button = document.getElementById('btn-check-update');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      const originalText = button.textContent;
      button.textContent = 'Starting…';
      try {
        const result = await apiCall('install_update');
        if (result.started) {
          _availableUpdate = null;
          _restartPending = true;
          toast('Update installed. Restart Home Assistant to load it.', 'ok');
        } else if (result.release_url) {
          window.open(result.release_url, '_blank', 'noopener,noreferrer');
          toast('Open the release page to update, then reload the integration.', 'ok');
        }
      } catch (error) {
        toast(`Could not start the update: ${error.message}`, 'err');
      } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        if (_restartPending) renderUpdateAction();
        else button.textContent = originalText;
      }
    }

    async function restartHomeAssistant() {
      if (!confirm('Restart Home Assistant now? Dashboards, automations, and this panel will be temporarily unavailable.')) return;
      const button = document.getElementById('btn-check-update');
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = 'Restarting…';
      try {
        await apiCall('restart_home_assistant');
        toast('Home Assistant is restarting. Please wait a moment, then reload this page.', 'ok');
      } catch (error) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        renderUpdateAction();
        toast(`Could not restart Home Assistant: ${error.message}`, 'err');
      }
    }

    function renderHubSelector() {
      const wrap = document.getElementById('hub-select-wrap');
      const sel = document.getElementById('hub-select');
      if (_entries.length <= 1) {
        wrap.style.display = 'none';
        return;
      }
      wrap.style.display = 'flex';
      sel.innerHTML = _entries.map(e => `
        <option value="${e.entry_id}" ${e.entry_id === _currentEntryId ? 'selected' : ''}>
          ${e.title || e.entry_id} (${e.hub?.port || 'USB'})
        </option>
      `).join('');
    }

    function onHubChange(entryId) {
      _currentEntryId = entryId;
      refreshData();
    }

    function renderHeaderSubtitle() {
      const entry = getCurrentEntry();
      const sub = document.getElementById('header-subtitle');
      if (!entry) {
        sub.innerHTML = `<span>No Modbus USB controller found</span>`;
        return;
      }
      const port = entry.hub?.port || '/dev/ttyUSB0';
      const baud = entry.hub?.baudrate || 9600;
      const devCount = (entry.devices || []).length;
      const entCount = (entry.entities || []).length;
      sub.innerHTML = `
        <span class="mono badge badge-slate">${port} @ ${baud} 8N1</span>
        <span>${devCount} separated device${devCount === 1 ? '' : 's'}</span>
        <span>•</span>
        <span>${entCount} entities</span>
      `;
    }
