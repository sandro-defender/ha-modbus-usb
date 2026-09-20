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
