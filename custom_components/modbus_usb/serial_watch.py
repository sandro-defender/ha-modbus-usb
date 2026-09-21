"""USB adapter watch: hot-plug recovery and port-ownership logic.

Pure, Home-Assistant-free helpers for the serial transport:

- ``is_adapter_gone_error`` / ``classify_port_error`` map serial failures
  to explicit states (``adapter_lost``) or port-status reasons
  (``ok`` / ``missing`` / ``busy`` / ``permission`` / ``unknown``).
- ``BackoffSchedule`` is the 1 s → 30 s capped exponential backoff used
  while waiting for a re-plugged adapter.
- ``AdapterIdentity`` is the USB identity (VID:PID + serial number) the
  coordinator learns at the first successful connect and stores in
  ``entry.data["adapter_identity"]``.
- ``resolve_port_path`` re-discovers the adapter's port after a hot-plug:
  configured path → its ``/dev/serial/by-id`` link → learned identity →
  the only ``ttyUSB``/``ttyACM`` present. It never guesses when several
  adapters match (``ambiguous``).
- ``stable_by_id_path`` suggests the persistent by-id link for a dynamic
  device node (the panel's "Use stable path" button).
- ``find_port_holders`` best-effort scans ``/proc/*/fd`` to name the
  process holding a busy port open.
- ``probe_port_ownership`` is the one-shot open/close probe behind
  ``modbus_usb/get_serial_status``: it classifies why a port cannot be
  owned (ok / missing / busy / permission / unknown) and, for busy
  ports, names the holding process best-effort.

Every filesystem touch goes through injectable callables (``listdir``,
``realpath``, ``exists``, ``readlink``, ``proc_root``) so the logic is
unit-testable without touching ``/dev`` or ``/proc``.
"""

from __future__ import annotations

import errno
import os
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

BY_ID_DIR = "/dev/serial/by-id"

# ── Adapter-gone error classification ────────────────────────────────────

#: errno values that mean "the USB adapter is physically gone".
_ADAPTER_GONE_ERRNOS = {errno.ENOENT, errno.EIO, errno.ENXIO}

try:
    from serial import SerialException
except ImportError:  # pragma: no cover - pyserial is a hard dependency
    SerialException = None  # type: ignore[assignment,misc]


def is_adapter_gone_error(error: BaseException | None) -> bool:
    """True when ``error`` means the USB adapter disappeared.

    Matches pyserial's ``SerialException`` (any of them — pyserial wraps
    every OS-level serial failure) and ``OSError`` with ENOENT / EIO /
    ENXIO. Plain permission or busy errors are NOT adapter-gone.
    """
    if error is None:
        return False
    if SerialException is not None and isinstance(error, SerialException):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) in (
        _ADAPTER_GONE_ERRNOS
    ):
        return True
    return False


# ── Port-open status (why a port cannot be opened) ──────────────────────

PORT_STATUS_OK = "ok"
PORT_STATUS_MISSING = "missing"
PORT_STATUS_BUSY = "busy"
PORT_STATUS_PERMISSION = "permission"
PORT_STATUS_UNKNOWN = "unknown"

PORT_STATUS_HINTS = {
    PORT_STATUS_OK: "{port} opens cleanly and is available to Home Assistant.",
    PORT_STATUS_MISSING: (
        "{port} does not exist. Check the USB cable, adapter power, and "
        "USB passthrough; by-id candidates are listed below when available."
    ),
    PORT_STATUS_BUSY: (
        "{port} is already open by another program. Close it (e.g. a desktop "
        "Modbus poller or a leftover getty) so Home Assistant can own the bus."
    ),
    PORT_STATUS_PERMISSION: (
        "Home Assistant lacks permission to open {port}. Add the "
        "homeassistant user to the dialout group (or run the container with "
        "--device and matching group access) and re-check."
    ),
    PORT_STATUS_UNKNOWN: (
        "{port} could not be opened for an unexpected reason: {detail}"
    ),
}


def classify_port_error(
    error: BaseException | None,
    *,
    path: str | None,
    path_exists: bool,
) -> tuple[str, str]:
    """Map a port-open failure to ``(reason, human hint)``.

    ``reason`` is one of ``ok`` / ``missing`` / ``busy`` / ``permission`` /
    ``unknown``. ``error is None`` means the probe opened the port cleanly.
    """
    label = path or "the configured port"
    if error is None:
        return (
            PORT_STATUS_OK,
            PORT_STATUS_HINTS[PORT_STATUS_OK].format(port=label),
        )
    if not path_exists:
        return (
            PORT_STATUS_MISSING,
            PORT_STATUS_HINTS[PORT_STATUS_MISSING].format(port=label),
        )
    errno_value = getattr(error, "errno", None)
    text = str(error).lower()
    if errno_value in (errno.EACCES, errno.EPERM) or "permission" in text:
        return (
            PORT_STATUS_PERMISSION,
            PORT_STATUS_HINTS[PORT_STATUS_PERMISSION].format(port=label),
        )
    if (
        errno_value in (errno.EBUSY, errno.EAGAIN, errno.ENXIO)
        or "busy" in text
        or "resource" in text
        or "exclusive" in text
    ):
        return (
            PORT_STATUS_BUSY,
            PORT_STATUS_HINTS[PORT_STATUS_BUSY].format(port=label),
        )
    if errno_value == errno.ENOENT or "no such file" in text:
        return (
            PORT_STATUS_MISSING,
            PORT_STATUS_HINTS[PORT_STATUS_MISSING].format(port=label),
        )
    return (
        PORT_STATUS_UNKNOWN,
        PORT_STATUS_HINTS[PORT_STATUS_UNKNOWN].format(port=label, detail=error),
    )


def find_port_holders(
    path: str,
    *,
    proc_root: str = "/proc",
    listdir: Callable[[str], Any] = os.listdir,
    readlink: Callable[[str], str] = os.readlink,
    realpath: Callable[[str], str] = os.path.realpath,
) -> list[dict[str, Any]]:
    """Best-effort list of processes holding ``path`` open (via /proc/*/fd).

    Each hit is ``{"pid": int, "name": str | None}`` (name from
    ``/proc/<pid>/comm``). Never raises: unavailable /proc (containers,
    non-Linux) or unreadable fd tables simply yield ``[]``.
    """
    if not path:
        return []
    try:
        target = realpath(path)
    except OSError:
        return []
    try:
        pid_names = [name for name in listdir(proc_root) if name.isdigit()]
    except OSError:
        return []
    holders: list[dict[str, Any]] = []
    for pid_name in pid_names:
        fd_dir = os.path.join(proc_root, pid_name, "fd")
        try:
            fd_names = listdir(fd_dir)
        except OSError:
            continue
        holds = False
        for fd_name in fd_names:
            try:
                if readlink(os.path.join(fd_dir, fd_name)) == target:
                    holds = True
                    break
            except OSError:
                continue
        if not holds:
            continue
        name: str | None = None
        try:
            with open(
                os.path.join(proc_root, pid_name, "comm"),
                encoding="ascii",
                errors="replace",
            ) as handle:
                name = handle.read().strip()[:15] or None
        except OSError:
            pass
        holders.append({"pid": int(pid_name), "name": name})
    return holders


# ── Re-discovery backoff ─────────────────────────────────────────────────


@dataclass(frozen=True)
class BackoffSchedule:
    """Exponential re-discovery backoff: 1 s, 2 s, 4 s, … capped at 30 s.

    ``jitter`` (seconds, default 0) adds a random component of at most
    ``jitter`` seconds to each delay; tests pass ``rand`` to stay
    deterministic.
    """

    initial: float = 1.0
    cap: float = 30.0
    factor: float = 2.0
    jitter: float = 0.0

    def delay_for_attempt(
        self, attempt: int, rand: Callable[[], float] = random.random
    ) -> float:
        """Seconds to wait before the check number ``attempt`` (0-based)."""
        if attempt < 0:
            raise ValueError("attempt must be >= 0")
        delay = min(self.cap, self.initial * (self.factor**attempt))
        if self.jitter > 0:
            delay += rand() * self.jitter
        return max(0.0, delay)


# ── Adapter identity ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdapterIdentity:
    """USB adapter identity learned at the first successful connect.

    VID:PID plus the USB serial number uniquely identify one physical
    adapter even when the OS assigns it a different ttyUSB index.
    """

    vid: str
    pid: str
    serial_number: str | None = None
    hwid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "vid": self.vid,
            "pid": self.pid,
            "serial_number": self.serial_number,
            "hwid": self.hwid,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> AdapterIdentity | None:
        """Parse an ``entry.data["adapter_identity"]`` record (or None)."""
        if not isinstance(data, Mapping):
            return None
        try:
            vid = str(data.get("vid") or "")
            pid = str(data.get("pid") or "")
        except (TypeError, ValueError):
            return None
        if not vid or not pid:
            return None
        serial_number = data.get("serial_number")
        hwid = data.get("hwid")
        return cls(
            vid=vid,
            pid=pid,
            serial_number=str(serial_number) if serial_number else None,
            hwid=str(hwid) if hwid else None,
        )

    def matches(self, port: Mapping[str, Any] | None) -> bool:
        """True when a list-port record describes this exact adapter."""
        if not port:
            return False
        if str(port.get("vid")) != self.vid or str(port.get("pid")) != self.pid:
            return False
        if self.serial_number:
            return str(port.get("serial_number") or "") == self.serial_number
        return True


def identity_from_port(port: Mapping[str, Any] | None) -> AdapterIdentity | None:
    """Learn an identity from one list-port record (None when too vague)."""
    if not port or not port.get("vid") or not port.get("pid"):
        return None
    return AdapterIdentity(
        vid=str(port["vid"]),
        pid=str(port["pid"]),
        serial_number=str(port["serial_number"]) if port.get("serial_number") else None,
        hwid=str(port["hwid"]) if port.get("hwid") else None,
    )


def redact_serial_number(value: str | None) -> str | None:
    """Redact a USB serial number, keeping only its last 4 characters."""
    if value is None:
        return None
    text = str(value)
    if len(text) <= 4:
        return text
    return "***" + text[-4:]


# ── Port re-discovery ────────────────────────────────────────────────────

RESOLVED_CONFIGURED = "configured"
RESOLVED_BY_ID = "by_id"
RESOLVED_IDENTITY = "identity"
RESOLVED_FALLBACK = "fallback"
RESOLVED_AMBIGUOUS = "ambiguous"
RESOLVED_NONE = "none"


@dataclass(frozen=True)
class PortResolution:
    """Result of an adapter re-discovery attempt."""

    path: str | None
    source: str  # one of the RESOLVED_* constants
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.path is not None


def _is_dynamic_tty(path: str) -> bool:
    return "/dev/ttyUSB" in path or "/dev/ttyACM" in path


def list_port_records() -> list[dict[str, Any]]:
    """Serial ports visible to this host, as plain records.

    Same record shape as ``api.helpers._list_serial_ports`` (port,
    persistent_path, description, details, manufacturer, product, vid,
    pid, hwid, serial_number, chipset). Never raises: a missing pyserial
    or unreadable sysfs yields ``[]``.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports: list[dict[str, Any]] = []
    for port in list_ports.comports():
        details_parts = [
            value
            for value in (port.manufacturer, port.product, port.hwid)
            if value and value != "n/a"
        ]
        persistent_path = stable_by_id_path(port.device)
        chipset = port.product or port.description or "Standard Serial Adapter"
        if port.manufacturer and port.manufacturer != "n/a":
            chipset = f"{port.manufacturer} ({chipset})"
        ports.append(
            {
                "port": port.device,
                "persistent_path": persistent_path,
                "description": port.description or "Serial device",
                "details": " · ".join(details_parts),
                "manufacturer": port.manufacturer
                if port.manufacturer != "n/a"
                else None,
                "product": port.product if port.product != "n/a" else None,
                "vid": f"0x{port.vid:04X}" if port.vid is not None else None,
                "pid": f"0x{port.pid:04X}" if port.pid is not None else None,
                "hwid": port.hwid if port.hwid != "n/a" else None,
                "serial_number": port.serial_number
                if port.serial_number != "n/a"
                else None,
                "chipset": chipset,
            }
        )
    return sorted(ports, key=lambda item: item["port"])


def _by_id_link_resolving_to(
    port: str,
    *,
    listdir: Callable[[str], Any] = os.listdir,
    realpath: Callable[[str], str] = os.path.realpath,
    exists: Callable[[str], bool] = os.path.exists,
) -> str | None:
    """The existing /dev/serial/by-id link that resolves to ``port``."""
    try:
        target = realpath(port)
    except OSError:
        return None
    try:
        names = sorted(listdir(BY_ID_DIR))
    except OSError:
        return None
    for name in names:
        link = os.path.join(BY_ID_DIR, name)
        try:
            if not exists(link):
                continue
            if realpath(link) == target:
                return link
        except OSError:
            continue
    return None


def probe_port_ownership(
    path: str | None,
    *,
    path_exists: bool | None = None,
    open_port: Callable[[str], Any] | None = None,
    proc_root: str = "/proc",
    listdir: Callable[[str], Any] = os.listdir,
    readlink: Callable[[str], str] = os.readlink,
    realpath: Callable[[str], str] = os.path.realpath,
    exists: Callable[[str], bool] = os.path.exists,
) -> dict[str, Any]:
    """Classify why a serial port cannot currently be owned.

    Returns ``{"reason", "hint", "holders"}`` where ``reason`` is one of
    ``ok`` / ``missing`` / ``busy`` / ``permission`` / ``unknown`` and
    ``hint`` is a one-sentence, actionable explanation (busy hints name
    the holding process when /proc is readable). Never raises: a
    failed probe simply degrades to ``unknown``.

    ``open_port(path)`` performs the actual open/close probe and must
    raise on failure (default: pyserial open + immediate close, ~200 ms
    timeout). Callers must only run this when the integration is NOT
    currently holding the port.
    """
    if not path:
        return {
            "reason": PORT_STATUS_MISSING,
            "hint": "No port is configured for this hub yet.",
            "holders": [],
        }
    if path_exists is None:
        try:
            path_exists = exists(path)
        except OSError:
            path_exists = False
    if not path_exists:
        _reason, hint = classify_port_error(
            OSError(errno.ENOENT, "No such file or directory"),
            path=path,
            path_exists=False,
        )
        return {"reason": PORT_STATUS_MISSING, "hint": hint, "holders": []}

    error: BaseException | None = None
    try:
        if open_port is None:
            import serial as pyserial

            serial_port = pyserial.Serial(port=path, timeout=0.2)
            serial_port.close()
        else:
            open_port(path)
    except Exception as err:
        error = err

    reason, hint = classify_port_error(error, path=path, path_exists=True)
    holders: list[dict[str, Any]] = []
    if reason == PORT_STATUS_BUSY:
        try:
            holders = find_port_holders(
                path,
                proc_root=proc_root,
                listdir=listdir,
                readlink=readlink,
                realpath=realpath,
            )
        except Exception:  # never break the status probe
            holders = []
        if holders:
            names = ", ".join(
                f"{item.get('name') or 'process'} (pid {item['pid']})"
                for item in holders[:3]
            )
            hint = f"{hint.rstrip('.')}. Currently held open by {names}."
    return {"reason": reason, "hint": hint, "holders": holders}


def stable_by_id_path(
    port: str | None,
    *,
    listdir: Callable[[str], Any] = os.listdir,
    realpath: Callable[[str], str] = os.path.realpath,
    exists: Callable[[str], bool] = os.path.exists,
) -> str | None:
    """Suggest the persistent by-id path for a dynamic ttyUSB/ttyACM port.

    Returns the ``/dev/serial/by-id/*`` link that resolves to ``port`` —
    stable across reboots and re-plugging — or ``None`` when ``port`` is
    not a dynamic device node (or is already a by-id path) or no matching
    link exists.
    """
    if not port or not isinstance(port, str):
        return None
    if port.startswith(BY_ID_DIR + "/"):
        return port
    if not _is_dynamic_tty(port):
        return None
    return _by_id_link_resolving_to(
        port, listdir=listdir, realpath=realpath, exists=exists
    )


def _stable_path_for_record(record: Mapping[str, Any]) -> str:
    """Prefer the record's persistent by-id path over the dynamic node."""
    persistent = record.get("persistent_path")
    if persistent and str(persistent).startswith(BY_ID_DIR + "/"):
        return str(persistent)
    return str(record.get("port"))


def resolve_port_path(
    configured: str,
    ports: list[Mapping[str, Any]],
    identity: AdapterIdentity | None = None,
    *,
    listdir: Callable[[str], Any] = os.listdir,
    realpath: Callable[[str], str] = os.path.realpath,
    exists: Callable[[str], bool] = os.path.exists,
) -> PortResolution:
    """Re-discover the adapter's port after a hot-plug.

    Order: (a) the configured path when it still exists; (b) the
    ``/dev/serial/by-id`` link that resolves to the configured path;
    (c) the learned USB adapter identity (VID:PID + serial number) —
    preferred over any plain ttyUSB; (d) the only ttyUSB/ttyACM present.
    When several adapters match (identity, or several dynamic TTYs with
    no identity) the result is ``ambiguous`` with the candidates — the
    caller must stay in ``adapter_lost`` and tell the user, never guess.
    """
    if not configured:
        return PortResolution(None, RESOLVED_NONE)
    if exists(configured):
        return PortResolution(configured, RESOLVED_CONFIGURED)

    if not configured.startswith(BY_ID_DIR + "/"):
        by_id_link = _by_id_link_resolving_to(
            configured, listdir=listdir, realpath=realpath, exists=exists
        )
        if by_id_link is not None:
            return PortResolution(by_id_link, RESOLVED_BY_ID)

    if identity is not None:
        matches = sorted(
            {
                _stable_path_for_record(record)
                for record in ports
                if record.get("port") and identity.matches(record)
            }
        )
        if len(matches) == 1:
            return PortResolution(matches[0], RESOLVED_IDENTITY)
        if len(matches) > 1:
            return PortResolution(None, RESOLVED_AMBIGUOUS, True, tuple(matches))

    tty_ports = sorted(
        {
            str(record["port"])
            for record in ports
            if record.get("port") and _is_dynamic_tty(str(record["port"]))
        }
    )
    if len(tty_ports) == 1:
        return PortResolution(tty_ports[0], RESOLVED_FALLBACK)
    if not tty_ports:
        return PortResolution(None, RESOLVED_NONE)
    # Several dynamic TTYs and no identity to pick between: do not guess.
    return PortResolution(None, RESOLVED_AMBIGUOUS, True, tuple(tty_ports))
