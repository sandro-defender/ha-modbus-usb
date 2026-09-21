"""Unit tests for the pure USB adapter-watch helpers (serial_watch.py)."""

from __future__ import annotations

import errno
import os

from serial import SerialException

from custom_components.modbus_usb.serial_watch import (
    BY_ID_DIR,
    AdapterIdentity,
    BackoffSchedule,
    PortResolution,
    classify_port_error,
    find_port_holders,
    identity_from_port,
    is_adapter_gone_error,
    redact_serial_number,
    resolve_port_path,
    stable_by_id_path,
)

# ── BackoffSchedule ───────────────────────────────────────────────────────


def test_backoff_sequence_doubles_to_30s_cap():
    """1 s → 2 s → 4 s → 8 s → 16 s → 30 s cap (never exceeds the cap)."""
    schedule = BackoffSchedule()
    delays = [schedule.delay_for_attempt(i) for i in range(8)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_backoff_jitter_is_bounded_and_deterministic_with_rand():
    schedule = BackoffSchedule(jitter=1.0)
    # rand() -> 0.0: no extra delay
    assert schedule.delay_for_attempt(0, rand=lambda: 0.0) == 1.0
    # rand() -> 1.0: full jitter added
    assert schedule.delay_for_attempt(0, rand=lambda: 1.0) == 2.0
    # even with jitter the base stays capped
    assert schedule.delay_for_attempt(9, rand=lambda: 1.0) <= 30.0 + 1.0


def test_backoff_rejects_negative_attempt():
    schedule = BackoffSchedule()
    try:
        schedule.delay_for_attempt(-1)
    except ValueError:
        return
    raise AssertionError("expected ValueError for negative attempt")


# ── AdapterIdentity ───────────────────────────────────────────────────────


def test_identity_roundtrip_and_redaction():
    identity = AdapterIdentity(vid="0x0403", pid="0x6015", serial_number="ABC12345")
    data = identity.as_dict()
    assert AdapterIdentity.from_dict(data) == identity
    assert redact_serial_number("ABC12345") == "***2345"
    assert redact_serial_number("AB12") == "AB12"
    assert redact_serial_number(None) is None


def test_identity_from_dict_rejects_invalid_records():
    assert AdapterIdentity.from_dict(None) is None
    assert AdapterIdentity.from_dict({"vid": "", "pid": "0x0001"}) is None
    assert AdapterIdentity.from_dict({"pid": "0x0001"}) is None
    assert AdapterIdentity.from_dict("not a mapping") is None


def test_identity_matches_requires_vid_pid_and_serial_when_known():
    identity = AdapterIdentity(vid="0x0403", pid="0x6015", serial_number="SN1")
    # dict-shaped record (as produced by list_port_records)
    good = {"vid": "0x0403", "pid": "0x6015", "serial_number": "SN1"}
    assert identity.matches(good)
    assert not identity.matches(
        {"vid": "0x0403", "pid": "0x6015", "serial_number": "OTHER"}
    )
    assert not identity.matches({"vid": "0x0403", "pid": "0x6016"})
    assert not identity.matches(None)


def test_identity_without_serial_matches_vid_pid_only():
    identity = AdapterIdentity(vid="0x0403", pid="0x6015")
    assert identity.matches({"vid": "0x0403", "pid": "0x6015"})
    assert not identity.matches({"vid": "0x0403", "pid": "0x6016"})


def test_identity_from_port():
    assert identity_from_port(None) is None
    assert identity_from_port({"vid": "0x0403"}) is None
    identity = identity_from_port(
        {"vid": "0x0403", "pid": "0x6015", "serial_number": "SN9", "hwid": "USB-1"}
    )
    assert identity == AdapterIdentity(
        vid="0x0403", pid="0x6015", serial_number="SN9", hwid="USB-1"
    )


# ── is_adapter_gone_error ─────────────────────────────────────────────────


def test_adapter_gone_error_recognizes_serial_exception_and_gone_errnos():
    assert is_adapter_gone_error(SerialException("device moved"))
    assert is_adapter_gone_error(OSError(errno.ENOENT, "No such device"))
    assert is_adapter_gone_error(OSError(errno.EIO, "I/O error"))
    assert is_adapter_gone_error(OSError(errno.ENXIO, "No such device"))


def test_adapter_gone_error_rejects_unrelated_failures():
    assert not is_adapter_gone_error(OSError(errno.EBUSY, "Device or resource busy"))
    assert not is_adapter_gone_error(OSError(errno.EACCES, "Permission denied"))
    assert not is_adapter_gone_error(ValueError("bad config"))
    assert not is_adapter_gone_error(None)


# ── classify_port_error ───────────────────────────────────────────────────


def test_classify_port_error_ok():
    reason, hint = classify_port_error(None, path="/dev/ttyUSB0", path_exists=True)
    assert reason == "ok"
    assert "/dev/ttyUSB0" in hint


def test_classify_port_error_missing():
    reason, _hint = classify_port_error(
        OSError(errno.ENOENT, "No such file or directory"),
        path="/dev/ttyUSB7",
        path_exists=False,
    )
    assert reason == "missing"


def test_classify_port_error_permission_mentions_dialout():
    reason, hint = classify_port_error(
        OSError(errno.EACCES, "Permission denied"),
        path="/dev/ttyUSB0",
        path_exists=True,
    )
    assert reason == "permission"
    assert "dialout" in hint


def test_classify_port_error_busy():
    for error in (
        OSError(errno.EBUSY, "Device or resource busy"),
        OSError(errno.ENXIO, "No such device or address"),
        RuntimeError("could not lock port (exclusive use)"),
    ):
        reason, _hint = classify_port_error(
            error, path="/dev/ttyUSB0", path_exists=True
        )
        assert reason == "busy", error


def test_classify_port_error_unknown_fallback():
    reason, hint = classify_port_error(
        RuntimeError("weird failure"), path="/dev/ttyUSB0", path_exists=True
    )
    assert reason == "unknown"
    assert "weird failure" in hint


# ── find_port_holders ─────────────────────────────────────────────────────


def test_find_port_holders_scans_proc(tmp_path):
    """A fake /proc tree with one process holding the port open."""
    target = tmp_path / "tty"
    target.write_text("")
    holder = tmp_path / "proc" / "1234"
    (holder / "fd").mkdir(parents=True)
    (holder / "fd" / "3").symlink_to(target)
    (holder / "comm").write_text("minicom\n")
    nobody = tmp_path / "proc" / "999"
    (nobody / "fd").mkdir(parents=True)
    (nobody / "fd" / "3").symlink_to(tmp_path / "other")
    (nobody / "comm").write_text("bash\n")

    holders = find_port_holders(str(target), proc_root=str(tmp_path / "proc"))
    assert holders == [{"pid": 1234, "name": "minicom"}]


def test_find_port_holders_never_raises_on_missing_proc():
    assert find_port_holders("/dev/ttyUSB0", proc_root="/nonexistent/proc") == []
    assert find_port_holders("") == []


# ── resolve_port_path ─────────────────────────────────────────────────────


class _FakeFS:
    """Injectable listdir/realpath/exists over a by-id dir + device nodes."""

    def __init__(self, links=None, resolved=None, existing=None):
        self.links = links or {}  # link name -> resolved target
        self.resolved = resolved or {}  # any path -> resolved path
        self.existing = set(existing or ())

    def listdir(self, path):
        if path == BY_ID_DIR:
            return sorted(self.links)
        raise FileNotFoundError(path)

    def realpath(self, path):
        if path in self.resolved:
            return self.resolved[path]
        name = os.path.basename(path)
        if path.startswith(BY_ID_DIR + "/") and name in self.links:
            return self.links[name]
        return path

    def exists(self, path):
        return path in self.existing


def _fs_kwargs(fs: _FakeFS):
    return dict(listdir=fs.listdir, realpath=fs.realpath, exists=fs.exists)


def test_resolve_port_path_prefers_existing_configured_path():
    fs = _FakeFS(existing=("/dev/ttyUSB3",))
    result = resolve_port_path(
        "/dev/ttyUSB3",
        [{"port": "/dev/ttyUSB0", "persistent_path": None}],
        AdapterIdentity(vid="0x0403", pid="0x6015"),
        **_fs_kwargs(fs),
    )
    assert result.found and result.path == "/dev/ttyUSB3"
    assert result.source == "configured"
    assert not result.ambiguous


def test_resolve_port_path_existing_by_id_link_returns_as_is():
    fs = _FakeFS(existing=("/dev/serial/by-id/usb-x",))
    result = resolve_port_path("/dev/serial/by-id/usb-x", [], None, **_fs_kwargs(fs))
    assert result.path == "/dev/serial/by-id/usb-x"
    assert result.source == "configured"


def test_resolve_port_path_falls_back_to_by_id_link_of_configured_port():
    fs = _FakeFS(
        links={"usb-FTDI_D2XX_0123": "/dev/ttyUSB0"},
        resolved={"/dev/ttyUSB0": "/dev/ttyUSB0"},
        existing=("/dev/serial/by-id/usb-FTDI_D2XX_0123",),
    )
    result = resolve_port_path("/dev/ttyUSB0", [], None, **_fs_kwargs(fs))
    assert result.path == "/dev/serial/by-id/usb-FTDI_D2XX_0123"
    assert result.source == "by_id"


def test_resolve_port_path_identity_beats_plain_tty_fallback():
    fs = _FakeFS()
    ports = [
        {
            "port": "/dev/ttyUSB0",
            "persistent_path": None,
            "vid": "0x9999",
            "pid": "0x8888",
            "serial_number": "OTHER",
        },
        {
            "port": "/dev/ttyUSB1",
            "persistent_path": "/dev/serial/by-id/usb-FTDI_0001",
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
        },
    ]
    identity = AdapterIdentity(vid="0x0403", pid="0x6015", serial_number="SN1")
    result = resolve_port_path("/dev/ttyUSB9", ports, identity, **_fs_kwargs(fs))
    # identity match wins over the "only ttyUSB" fallback — and prefers the
    # record's persistent by-id path when available.
    assert result.path == "/dev/serial/by-id/usb-FTDI_0001"
    assert result.source == "identity"


def test_resolve_port_path_identity_without_persistent_path_uses_port():
    fs = _FakeFS()
    ports = [
        {
            "port": "/dev/ttyUSB2",
            "persistent_path": None,
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
        },
    ]
    identity = AdapterIdentity(vid="0x0403", pid="0x6015", serial_number="SN1")
    result = resolve_port_path("/dev/ttyUSB9", ports, identity, **_fs_kwargs(fs))
    assert result.path == "/dev/ttyUSB2"
    assert result.source == "identity"


def test_resolve_port_path_ambiguous_identity_never_guesses():
    fs = _FakeFS()
    ports = [
        {
            "port": "/dev/ttyUSB0",
            "persistent_path": None,
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
        },
        {
            "port": "/dev/ttyUSB1",
            "persistent_path": None,
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
        },
    ]
    identity = AdapterIdentity(vid="0x0403", pid="0x6015", serial_number="SN1")
    result = resolve_port_path("/dev/ttyUSB9", ports, identity, **_fs_kwargs(fs))
    assert not result.found
    assert result.ambiguous
    assert result.source == "ambiguous"
    assert result.candidates == ("/dev/ttyUSB0", "/dev/ttyUSB1")


def test_resolve_port_path_single_tty_fallback_without_identity():
    fs = _FakeFS()
    ports = [
        {
            "port": "/dev/ttyUSB4",
            "persistent_path": None,
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
        },
        {
            "port": "/dev/ttyACM0",
            "persistent_path": None,
            "vid": "0x1234",
            "pid": "0x5678",
            "serial_number": "SN2",
        },
    ]
    # no identity stored at all → two dynamic TTYs → ambiguous
    result = resolve_port_path("/dev/ttyUSB9", ports, None, **_fs_kwargs(fs))
    assert not result.found and result.ambiguous
    assert result.candidates == ("/dev/ttyACM0", "/dev/ttyUSB4")

    # with a single dynamic TTY → fallback to it
    result = resolve_port_path("/dev/ttyUSB9", ports[:1], None, **_fs_kwargs(fs))
    assert result.path == "/dev/ttyUSB4"
    assert result.source == "fallback"


def test_resolve_port_path_nothing_present():
    fs = _FakeFS()
    result = resolve_port_path("/dev/ttyUSB9", [], None, **_fs_kwargs(fs))
    assert not result.found
    assert result.source == "none"
    assert not result.ambiguous


def test_resolve_port_path_empty_configured():
    fs = _FakeFS()
    result = resolve_port_path("", [], None, **_fs_kwargs(fs))
    assert not result.found
    assert result.source == "none"


# ── stable_by_id_path ─────────────────────────────────────────────────────


def test_stable_by_id_path_suggests_link_for_dynamic_tty():
    fs = _FakeFS(
        links={"usb-FTDI_D2XX_0001": "/dev/ttyUSB0"},
        existing=("/dev/serial/by-id/usb-FTDI_D2XX_0001",),
    )
    assert (
        stable_by_id_path("/dev/ttyUSB0", **_fs_kwargs(fs))
        == "/dev/serial/by-id/usb-FTDI_D2XX_0001"
    )


def test_stable_by_id_path_passes_through_existing_by_id():
    fs = _FakeFS(existing=("/dev/serial/by-id/usb-x",))
    assert (
        stable_by_id_path("/dev/serial/by-id/usb-x", **_fs_kwargs(fs))
        == "/dev/serial/by-id/usb-x"
    )


def test_stable_by_id_path_no_suggestion_when_no_link_or_not_dynamic():
    fs = _FakeFS()
    assert stable_by_id_path("/dev/ttyUSB0", **_fs_kwargs(fs)) is None
    assert stable_by_id_path("/dev/socket/modbus0", **_fs_kwargs(fs)) is None
    assert stable_by_id_path(None, **_fs_kwargs(fs)) is None
    assert stable_by_id_path("", **_fs_kwargs(fs)) is None


def test_stable_by_id_path_never_raises_when_by_id_dir_missing():
    fs = _FakeFS()  # listdir raises for any path
    assert stable_by_id_path("/dev/ttyUSB1", **_fs_kwargs(fs)) is None


# ── PortResolution sanity ─────────────────────────────────────────────────


def test_port_resolution_found_property():
    assert PortResolution("/dev/ttyUSB0", "configured").found
    assert not PortResolution(None, "none").found


# ── probe_port_ownership ─────────────────────────────────────────────────


def test_probe_port_ownership_missing_when_path_absent():
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    opened = []
    result = probe_port_ownership(
        "/dev/ttyUSB7", path_exists=False, open_port=lambda p: opened.append(p)
    )
    assert result["reason"] == "missing"
    assert "/dev/ttyUSB7" in result["hint"]
    assert result["holders"] == []
    assert opened == []  # never opens a nonexistent port


def test_probe_port_ownership_ok_on_clean_open():
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    result = probe_port_ownership(
        "/dev/ttyUSB0", path_exists=True, open_port=lambda p: None
    )
    assert result["reason"] == "ok"
    assert result["holders"] == []


def test_probe_port_ownership_busy_names_holding_process(tmp_path):
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    target = tmp_path / "tty"
    target.write_text("")
    holder = tmp_path / "proc" / "777"
    (holder / "fd").mkdir(parents=True)
    (holder / "fd" / "5").symlink_to(target)
    (holder / "comm").write_text("minicom\n")

    def _busy(path):
        import errno as _errno

        raise OSError(_errno.EBUSY, "Device or resource busy")

    result = probe_port_ownership(
        str(target),
        path_exists=True,
        open_port=_busy,
        proc_root=str(tmp_path / "proc"),
    )
    assert result["reason"] == "busy"
    assert result["holders"] == [{"pid": 777, "name": "minicom"}]
    assert "minicom" in result["hint"]


def test_probe_port_ownership_permission_mentions_dialout():
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    def _denied(path):
        import errno as _errno

        raise OSError(_errno.EACCES, "Permission denied")

    result = probe_port_ownership("/dev/ttyUSB0", path_exists=True, open_port=_denied)
    assert result["reason"] == "permission"
    assert "dialout" in result["hint"]


def test_probe_port_ownership_unknown_for_unclassified_failures():
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    def _boom(path):
        raise RuntimeError("something else entirely")

    result = probe_port_ownership("/dev/ttyUSB0", path_exists=True, open_port=_boom)
    assert result["reason"] == "unknown"
    assert "something else entirely" in result["hint"]


def test_probe_port_ownership_no_path_is_missing():
    from custom_components.modbus_usb.serial_watch import probe_port_ownership

    result = probe_port_ownership(None, open_port=lambda p: None)
    assert result["reason"] == "missing"
    assert result["holders"] == []
