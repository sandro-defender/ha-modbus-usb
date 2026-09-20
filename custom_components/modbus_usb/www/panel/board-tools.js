/* board-tools.js — Board Tools launcher, template controls, and discovery targets.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    function renderBoardTools() {
      const select = document.getElementById('board-tools-device');
      const entry = getCurrentEntry();
      if (!select || !entry) return;
      const selected = select.value;
      const discoveredOption = _discoveredTarget
        ? `<option value="__discovered__">Discovered target · slave ${escapeHtml(_discoveredTarget.slave)} · ${escapeHtml(_discoveredTarget.baudrate)} baud</option>`
        : '';
      select.innerHTML = `<option value="">Select a configured board…</option>${discoveredOption}${(entry.devices || []).map(device =>
        `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name || device.model || 'Modbus board')} · slave ${escapeHtml(device.slave_id ?? entry.hub?.slave_id ?? 1)}</option>`
      ).join('')}`;
      if ([...select.options].some(option => option.value === selected)) select.value = selected;
      describeBoardTools();
    }

    function getBoardToolsDevice() {
      const entry = getCurrentEntry();
      const id = document.getElementById('board-tools-device')?.value;
      return [entry, (entry?.devices || []).find(device => device.id === id), id === '__discovered__' ? _discoveredTarget : null];
    }

    function findStandardControlTemplate(device) {
      const model = String(device?.model || '').trim().toLowerCase();
      const manufacturer = String(device?.manufacturer || '').trim().toLowerCase();
      return _templates.find(template => template.device_controls &&
        String(template.model || '').trim().toLowerCase() === model &&
        (!manufacturer || String(template.manufacturer || '').trim().toLowerCase() === manufacturer));
    }

    function boardControlProtocol(device) {
      return device?.device_controls?.protocol || findStandardControlTemplate(device)?.device_controls?.protocol || '';
    }

    function describeBoardTools() {
      const [, device, discovered] = getBoardToolsDevice();
      const description = document.getElementById('board-tools-description');
      if (!description) return;
      if (discovered) {
        description.textContent = `Discovered target: slave ${discovered.slave} at ${discovered.baudrate} baud. Choose a configured board for template commands, or use the custom-command button. Nothing has been sent.`;
        return;
      }
      if (!device) {
        description.textContent = 'Select a board to load its saved template controls.';
        return;
      }
      const protocol = boardControlProtocol(device);
      const standard = findStandardControlTemplate(device);
      if (protocol === 'eletechsup_r413e16') {
        description.innerHTML = `Template controls: baud rate, slave ID, factory reset, all-channel operations, and per-channel actions.${device.device_controls ? '' : ' <button class="btn btn-secondary btn-sm" onclick="restoreSelectedTemplateControls()">Restore standard template controls</button>'}`;
      } else if (protocol === 'eletechsup_r4d6f20') {
        description.innerHTML = `Template controls: relay actions, board ID readback, baud rate, parity, and factory reset.${device.device_controls ? '' : ' <button class="btn btn-secondary btn-sm" onclick="restoreSelectedTemplateControls()">Restore standard template controls</button>'}`;
      } else if (standard) {
        description.innerHTML = `A standard template was found for this board. <button class="btn btn-secondary btn-sm" onclick="restoreSelectedTemplateControls()">Restore template controls</button>`;
      } else {
        description.textContent = 'No command template is saved for this board. Use the documented custom command fields above; every write requires confirmation.';
      }
    }

    async function restoreSelectedTemplateControls() {
      const [entry, device] = getBoardToolsDevice();
      if (!entry || !device) return;
      try {
        const result = await apiCall('restore_device_template_controls', { entry_id: entry.entry_id, device_id: device.id });
        toast(`Restored command controls from ${result.template}`, 'ok');
        await refreshData();
      } catch (error) { toast(`Could not restore template controls: ${error.message}`, 'err'); }
    }

    function useSelectedBoardForCustomCommand() {
      const [entry, device, discovered] = getBoardToolsDevice();
      if (!entry || (!device && !discovered)) { toast('Select a board or discovered target first', 'err'); return; }
      const slave = discovered?.slave ?? device.slave_id ?? entry.hub?.slave_id ?? 1;
      document.getElementById('diag-slave').value = slave;
      document.getElementById('debug-slave').value = slave;
      document.getElementById('btn-diag-read').closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
      document.getElementById('diag-address').focus();
      toast(`Custom command target set to slave ${slave}. Nothing has been sent.`, 'ok');
    }

    function useDiscoveredTarget(slave, baudrate) {
      _discoveredTarget = { slave, baudrate };
      document.getElementById('diag-slave').value = slave;
      document.getElementById('debug-slave').value = slave;
      const rates = document.getElementById('scan-baudrates');
      const configured = rates.value.split(',').map(value => value.trim()).filter(Boolean);
      if (!configured.includes(String(baudrate))) rates.value = [baudrate, ...configured].join(',');
      const select = document.getElementById('board-tools-device');
      if (select) {
        renderBoardTools();
        select.value = '__discovered__';
        describeBoardTools();
        select.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
        select.focus();
      }
      toast(`Discovered target loaded into Board Tools: slave ${slave} at ${baudrate} baud. Nothing has been sent.`, 'ok');
    }

    function openSelectedBoardTools() {
      const [, device, discovered] = getBoardToolsDevice();
      if (discovered) { toast('Choose a configured board for template controls, or use the custom-command button. Nothing has been sent.', 'ok'); return; }
      if (!device) { toast('Select a board first', 'err'); return; }
      const protocol = boardControlProtocol(device);
      if (protocol === 'eletechsup_r413e16') return openR413e16Controls(device.id);
      if (protocol === 'eletechsup_r4d6f20') return openR4d6f20Controls(device.id);
      useSelectedBoardForCustomCommand();
    }
