"""Structural tests for the sidebar panel shell and its asset wiring.

The panel HTML must stay a thin shell: all CSS/JS lives in panel/ and is
loaded via link/script tags in dependency order (state first, main last).
These tests are Home Assistant-free.
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.fast


WWW_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "modbus_usb", "www"
)
PANEL_HTML = os.path.join(WWW_DIR, "modbus-panel.html")


def _html() -> str:
    with open(PANEL_HTML, encoding="utf-8") as f:
        return f.read()


def _panel_script(name: str) -> str:
    with open(os.path.join(WWW_DIR, "panel", name), encoding="utf-8") as f:
        return f.read()


def test_panel_has_no_inline_script_or_style() -> None:
    html = _html()
    assert "<style" not in html, "CSS must live in panel/panel.css"
    inline_scripts = re.findall(r"<script(?![^>]*src=)[^>]*>", html)
    assert not inline_scripts, (
        f"Inline scripts must live in panel/*.js: {inline_scripts}"
    )


def test_panel_referenced_assets_exist() -> None:
    html = _html()
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    styles = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
    assert scripts, "Expected panel/*.js script tags"
    assert styles == ["panel/panel.css"]
    for asset in (*scripts, *styles):
        assert not os.path.isabs(asset), f"Asset URL must be relative: {asset}"
        path = os.path.join(WWW_DIR, asset)
        assert os.path.isfile(path), f"Missing panel asset: {asset}"


def test_panel_script_load_order() -> None:
    html = _html()
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    assert scripts[0] == "panel/state.js", "Shared state must load first"
    assert scripts[-1] == "panel/main.js", "Event wiring must load last"
    assert len(scripts) == len(set(scripts)), "Duplicate script tag"


def test_panel_has_no_hardcoded_version() -> None:
    html = _html()
    assert "?v=" not in html, "Panel assets are served no-cache; no ?v= needed"


def test_panel_wires_inspector_and_designer_tabs() -> None:
    html = _html()
    for tab in ("inspector", "designer"):
        assert f'id="tab-btn-{tab}"' in html, f"missing {tab} tab button"
        assert f'id="pane-{tab}"' in html, f"missing {tab} pane"
        assert f"switchTab('{tab}')" in html, f"{tab} tab is not wired"
        assert f'aria-controls="pane-{tab}"' in html
    scripts = re.findall(r'<script src="([^"]+)"></script>', _html())
    assert "panel/inspector.js" in scripts
    assert "panel/designer.js" in scripts
    assert scripts.index("panel/inspector.js") < scripts.index("panel/designer.js")


def test_panel_wires_inspector_live_stream_controls() -> None:
    html = _html()
    assert 'id="btn-inspector-pause"' in html, "missing pause/resume stream button"
    assert "toggleInspectorPause()" in html, "pause button is not wired"
    assert 'id="inspector-live-badge"' in html, "missing live stream badge"

    inspector_js = _panel_script("inspector.js")
    state_js = _panel_script("state.js")
    core_js = _panel_script("core.js")
    assert "modbus_usb/subscribe_traffic" in inspector_js
    assert "subscribeMessage" in inspector_js
    assert "toggleInspectorPause" in inspector_js
    assert "ensureTrafficSubscription" in core_js
    assert "teardownTrafficSubscription" in core_js
    for variable in (
        "_inspectorUnsubscribe",
        "_inspectorStreamEntryId",
        "_inspectorPaused",
        "_inspectorBuffered",
        "_inspectorLive",
    ):
        assert variable in state_js, f"{variable} must live in state.js"


def test_panel_wires_designer_save_and_apply() -> None:
    html = _html()
    assert 'id="designer-apply-device"' in html, "missing apply-target device select"
    assert 'id="btn-designer-apply"' in html, "missing save & apply button"
    assert "saveAndApplyDesignerTemplate()" in html, "apply button is not wired"

    designer_js = _panel_script("designer.js")
    assert "save_and_apply_template" in designer_js
    assert "renderDesignerApplyTargets" in designer_js
    assert "designerFingerprintSection" in designer_js
    # The one-step flow must render the fingerprint probe results.
    assert "fingerprint_all_matched" in designer_js


# ───────────────── v2.7.0: inspector filters & multi-frame RX ─────────────────


def test_panel_wires_inspector_filters() -> None:
    html = _html()
    inspector_js = _panel_script("inspector.js")
    state_js = _panel_script("state.js")

    # Filter controls exist in the HTML and are wired to the JS handlers.
    assert 'id="inspector-filters"' in html, "missing inspector filter bar"
    assert 'id="inspector-filter-slave"' in html, "missing slave filter select"
    assert 'id="inspector-filter-status"' in html, "missing status filter select"
    assert 'id="inspector-filter-hex"' in html, "missing hex search input"
    assert 'id="btn-inspector-clear-filters"' in html, "missing clear-filters button"
    assert 'id="inspector-filter-count"' in html, "missing filter match counter"
    for status in ("all", "ok", "error", "exception"):
        assert f'<option value="{status}">' in html, (
            f"missing status filter option {status}"
        )
    assert "setInspectorFilter('slave', this.value)" in html
    assert "setInspectorFilter('status', this.value)" in html
    assert "setInspectorFilter('hex', this.value)" in html
    assert "clearInspectorFilters()" in html

    # Filter state lives in state.js so it survives tab switches.
    assert "_inspectorFilters" in state_js

    # The list is filtered client-side, live, over the full transaction list.
    for function in (
        "transactionMatchesInspectorFilters",
        "getFilteredTransactionIndices",
        "renderInspectorFilterBar",
        "normalizeInspectorHexQuery",
        "inspectorResponseFrameList",
        "clearInspectorFilters",
    ):
        assert function in inspector_js, f"missing inspector filter function {function}"
    assert "getFilteredTransactionIndices()" in inspector_js
    # New live pushes still render through the filter.
    assert "scheduleInspectorRender" in inspector_js
    # Pause/live-stream behavior is preserved.
    assert "toggleInspectorPause" in inspector_js
    assert "handleTrafficStreamMessage" in inspector_js


def test_panel_renders_multi_frame_responses() -> None:
    inspector_js = _panel_script("inspector.js")
    core_js = _panel_script("core.js")

    # The detail pane renders a list of decoded frames for multi-frame RX.
    assert "multiFrameResponseSection" in inspector_js
    assert "Response frames (RX)" in inspector_js
    # Row badge distinguishes multi-frame captures.
    assert "RX ×" in inspector_js
    # The standalone mock exercises the multi-frame shape end to end.
    assert "response_frames" in core_js
    assert 'id="inspector-detail"' in _html()


# ───────────────── v2.7.0: designer editor UX & template import ─────────────────


def test_panel_wires_designer_editor_ux() -> None:
    html = _html()
    designer_js = _panel_script("designer.js")

    # Line-number gutter next to the YAML textarea.
    assert 'id="designer-line-numbers"' in html, "missing line-number gutter"
    assert 'class="designer-editor"' in html, "missing editor wrapper"
    # Ctrl+Enter validation hint.
    assert "Ctrl+Enter" in html
    assert "Tab indents" in html

    for function in (
        "updateDesignerLineNumbers",
        "syncDesignerGutterScroll",
        "insertDesignerIndent",
        "outdentDesignerLines",
        "handleDesignerEditorKeydown",
    ):
        assert function in designer_js, f"missing designer editor function {function}"
    # Ctrl+Enter triggers the validation flow; Tab is an indent key.
    assert "event.key === 'Enter'" in designer_js
    assert "runDesignerValidation" in designer_js
    assert "event.key !== 'Tab'" in designer_js
    # The gutter stays in sync with the textarea.
    assert "textarea.addEventListener('input'" in designer_js
    assert "textarea.addEventListener('scroll'" in designer_js
    assert "textarea.addEventListener('keydown'" in designer_js


def test_panel_wires_designer_template_import() -> None:
    html = _html()
    designer_js = _panel_script("designer.js")
    core_js = _panel_script("core.js")

    assert 'id="designer-import-template"' in html, "missing import template select"
    assert "importDesignerTemplate()" in html, "import button is not wired"

    assert "importDesignerTemplate" in designer_js
    assert "renderDesignerImportOptions" in designer_js
    # Imported content lands in the editor and pre-fills the filename.
    assert "raw_yaml" in designer_js
    assert "designer-filename" in designer_js
    # The option list is refreshed whenever templates are reloaded.
    assert "renderDesignerImportOptions()" in core_js


# ───────────── v2.7.1: FC filter, saved views, RX waterfall, designer errors ─────────────


def test_panel_wires_inspector_function_code_filter() -> None:
    html = _html()
    inspector_js = _panel_script("inspector.js")
    state_js = _panel_script("state.js")

    assert 'id="inspector-filter-fc"' in html, "missing function-code filter select"
    assert "setInspectorFilter('fc', this.value)" in html
    # FC01–FC10 plus the exception pseudo-code are selectable.
    for code in ("01", "02", "03", "04", "05", "06", "0F", "10"):
        assert f'<option value="{code}">FC{code}' in html, f"missing FC{code} option"
    assert '<option value="exception">' in html
    # The default filter state carries the new dimension.
    assert re.search(r"INSPECTOR_DEFAULT_FILTERS\s*=[^{]*\{[^}]*fc:\s*'all'", state_js)
    for function in (
        "INSPECTOR_FUNCTION_CODES",
        "normalizeInspectorFunctionCode",
        "inspectorTransactionFunctionCodes",
        "transactionMatchesFunctionCodeFilter",
        "inspectorTransactionHasException",
    ):
        assert function in inspector_js, f"missing FC filter function {function}"
    # Exception frames match through their base function code as well.
    assert "base_function_code" in inspector_js


def test_panel_wires_inspector_saved_views() -> None:
    html = _html()
    inspector_js = _panel_script("inspector.js")
    state_js = _panel_script("state.js")
    core_js = _panel_script("core.js")

    assert 'id="inspector-presets"' in html, "missing saved-views bar"
    assert 'id="inspector-filter-preset"' in html, "missing preset select"
    assert 'id="btn-inspector-save-preset"' in html, "missing save-view button"
    assert 'id="btn-inspector-delete-preset"' in html, "missing delete-view button"
    assert "applyInspectorFilterPreset(this.value)" in html
    assert "saveInspectorFilterPreset()" in html
    assert "deleteInspectorFilterPreset()" in html

    # Presets persist in localStorage via state.js.
    assert (
        "INSPECTOR_PRESETS_STORAGE_KEY = 'modbus_usb_inspector_filter_presets'"
        in state_js
    )
    assert (
        "INSPECTOR_ACTIVE_PRESET_STORAGE_KEY = 'modbus_usb_inspector_active_preset'"
        in state_js
    )
    assert "INSPECTOR_MAX_PRESETS" in state_js
    assert "_inspectorFilterPresets" in state_js
    assert "_inspectorActivePreset" in state_js
    assert "function persistInspectorFilterPresets" in state_js
    assert "localStorage.setItem(INSPECTOR_PRESETS_STORAGE_KEY" in state_js

    for function in (
        "applyInspectorFilterPreset",
        "applyStoredInspectorPreset",
        "saveInspectorFilterPreset",
        "deleteInspectorFilterPreset",
        "renderInspectorPresetBar",
        "syncInspectorFilterControls",
    ):
        assert function in inspector_js, f"missing preset function {function}"
    assert "persistInspectorFilterPresets()" in inspector_js
    # The stored view is applied when the inspector tab opens.
    assert re.search(
        r"tabName === 'inspector'[\s\S]{0,400}applyStoredInspectorPreset\(\)", core_js
    ), "switchTab('inspector') must apply the stored preset"


def test_panel_renders_rx_frame_waterfall_and_coverage() -> None:
    inspector_js = _panel_script("inspector.js")
    core_js = _panel_script("core.js")
    css = _panel_script("panel.css")

    for function in (
        "responseFrameTimings",
        "rxFrameWaterfall",
        "captureCoverageStat",
        "inspectorCaptureCoverage",
    ):
        assert function in inspector_js, f"missing inspector function {function}"
    assert "Inter-frame gaps" in inspector_js
    assert "Capture coverage" in inspector_js
    # Timing comes from the decoded frames (arrival_ms/gap_ms) with a
    # fallback to the raw per-transaction list.
    assert "arrival_ms" in inspector_js
    assert "gap_ms" in inspector_js
    assert "response_frame_times_ms" in inspector_js
    assert "capture_coverage" in inspector_js
    # The standalone mock ships timing so the waterfall renders offline.
    assert "response_frame_times_ms" in core_js
    assert "capture_coverage" in core_js
    for selector in (".rx-waterfall-card", ".rx-waterfall-bar", ".rx-waterfall-tick"):
        assert selector in css, f"missing waterfall style {selector}"


def test_panel_wires_designer_error_highlighting_and_export() -> None:
    html = _html()
    designer_js = _panel_script("designer.js")
    core_js = _panel_script("core.js")
    css = _panel_script("panel.css")

    assert 'id="btn-designer-export"' in html, "missing export-draft button"
    assert "exportDesignerDraft()" in html

    for function in (
        "setDesignerErrorLine",
        "focusDesignerLine",
        "designerLineRange",
        "designerStructuralErrorBox",
        "designerDraftFilename",
        "exportDesignerDraft",
    ):
        assert function in designer_js, f"missing designer function {function}"
    # Structural error location from designer_validate drives the gutter.
    assert "error_line" in designer_js
    assert "error_column" in designer_js
    assert "error_path" in designer_js
    assert "designer-gutter-line-error" in designer_js
    assert (
        "setDesignerErrorLine(_designerResult?.valid ? null : _designerResult?.error_line)"
        in designer_js
    )
    # Export is a client-side blob download of the editor content.
    assert "new Blob(" in designer_js
    assert "text/yaml" in designer_js
    assert "URL.createObjectURL" in designer_js
    assert "URL.revokeObjectURL" in designer_js
    # The standalone mock returns a located error for the preview.
    assert "error_line" in core_js
    for selector in (".designer-gutter-line-error", ".designer-error-location"):
        assert selector in css, f"missing designer style {selector}"


def test_panel_wires_hub_ownership_banner_and_stable_path() -> None:
    """v2.9.0: hub tab explains a non-openable port in one sentence and
    offers the persistent by-id path via the existing save_hub command."""
    hub_js = _panel_script("hub.js")
    core_js = _panel_script("core.js")
    # One-sentence ownership banner (reason colours: ok/missing/busy/...).
    assert "renderHubOwnershipRow" in hub_js
    assert "hub-ownership-row" in hub_js
    assert "hub-ownership-banner" in hub_js
    for reason in ("ok", "missing", "busy", "permission", "unknown"):
        assert f"{reason}:" in hub_js or f"'{reason}'" in hub_js, reason
    assert "HUB_OWNERSHIP_COLORS" in hub_js
    # "Use stable path" button, saved via the existing save_hub command.
    assert "btn-use-stable-path" in hub_js
    assert "applyStableHubPath" in hub_js
    assert "apiCall('save_hub'" in hub_js
    # Ownership is read-only; the mock mode carries the new fields.
    assert "apiCall('get_serial_status'" in hub_js
    assert "ownership" in core_js
    assert "stable_path" in core_js
    assert "by_id_candidates" in core_js


def test_panel_wires_scan_stop_and_progress_stream() -> None:
    html = _html()
    diag = _panel_script("diagnostics.js")
    core = _panel_script("core.js")
    # Stop button lives in the progress row and is wired to stopRs485Scan()
    assert 'id="btn-stop-scan-bus"' in html
    assert 'onclick="stopRs485Scan()"' in html
    # Live progress subscription + stop command + progress-line format
    for token in (
        "function stopRs485Scan()",
        "apiCall('stop_bus_scan'",
        "modbus_usb/subscribe_scan_progress",
        "function ensureScanProgressSubscription(",
        "function applyScanProgressEvent(",
        "teardownScanProgressSubscription()",
        "slave ${scan.slave}/${total} @ ${scan.baudrate} 8${scan.parity || 'N'}1",
        "result.cancelled",
    ):
        assert token in diag, f"diagnostics.js missing: {token}"
    # The 500 ms polling fallback stays in place
    assert "setInterval(refreshScanProgress, 500)" in diag
    # Mock mode emulates scan progress and accepts the new commands
    for token in (
        "mockRunScan",
        "stop_bus_scan",
        "subscribe_scan_progress",
        "_mockScan",
    ):
        assert token in core, f"core.js missing: {token}"
