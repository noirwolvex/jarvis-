from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from .chrome_cdp import _cdp_url, _runtime, chrome_start_managed as _start_managed_chrome
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec
from .chrome_user_browser import existing_chrome_connection

_START_LOCK = threading.Lock()


def cdp_owns_window(window: dict[str, Any]) -> bool:
    """Bind the configured local listener to a fresh, exact Chrome process identity."""
    try:
        import psutil
        endpoint = urllib.parse.urlparse(_cdp_url())
        host = endpoint.hostname
        if endpoint.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        if endpoint.username is not None or endpoint.password is not None:
            return False
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        hwnd, pid = window.get("hwnd"), window.get("process_id")
        created = window.get("process_created")
        if type(hwnd) is not int or hwnd <= 0 or type(pid) is not int or pid <= 0:
            return False
        if type(created) not in {int, float} or not math.isfinite(created) or created <= 0:
            return False
        process = psutil.Process(pid)
        if not process.is_running() or process.create_time() != created:
            return False
        if process.name().casefold() not in {"chrome.exe", "chrome", "chromium.exe", "chromium", "google-chrome", "google-chrome-stable"}:
            return False
        allowed_addresses = {"127.0.0.1", "::1"} if host == "localhost" else {host}
        owners = set()
        for connection in psutil.net_connections(kind="tcp"):
            address = connection.laddr
            if connection.status == psutil.CONN_LISTEN and address and address.port == port and address.ip in allowed_addresses:
                owners.add(connection.pid)
        # Unknown owners and split IPv4/IPv6 ownership are not a safe binding.
        return owners == {pid} and process.is_running() and process.create_time() == created
    except Exception:
        return False


def _loopback_port() -> int | None:
    endpoint = urllib.parse.urlparse(_cdp_url())
    host = endpoint.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return endpoint.port or 9222


def _probe_cdp(timeout: float = 0.6) -> dict[str, Any] | None:
    """Return Chrome DevTools metadata when the configured local CDP endpoint is already alive."""
    port = _loopback_port()
    if port is None:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _page_targets(timeout: float = 0.6) -> list[dict[str, Any]]:
    """Return page targets that Chrome has actually published through DevTools."""
    port = _loopback_port()
    if port is None:
        return []
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as response:
            if response.status != 200:
                return []
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict) and row.get("type") == "page"]


def _wait_for_page_target(timeout: float = 5.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        targets = _page_targets()
        if targets:
            return targets
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.1)


def _settle_page_targets(timeout: float = 1.2, stable_polls: int = 3) -> list[dict[str, Any]]:
    """Wait briefly for Chrome's startup target list to stop changing."""
    deadline = time.monotonic() + max(0.0, timeout)
    previous_ids: tuple[str, ...] | None = None
    stable = 0
    latest: list[dict[str, Any]] = []
    while True:
        latest = _page_targets()
        current_ids = tuple(sorted(str(row.get("id") or "") for row in latest))
        if latest and current_ids == previous_ids:
            stable += 1
        else:
            stable = 0
            previous_ids = current_ids
        if latest and stable >= max(1, stable_polls):
            return latest
        if time.monotonic() >= deadline:
            return latest
        time.sleep(0.1)


def _close_page_target(target_id: str, timeout: float = 1.0) -> bool:
    port = _loopback_port()
    if port is None or not target_id:
        return False
    encoded = urllib.parse.quote(str(target_id), safe="")
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/close/{encoded}",
            timeout=timeout,
        ) as response:
            return response.status == 200
    except Exception:
        return False


def _dedupe_startup_blank_targets(targets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep one fresh managed about:blank target and close only duplicate startup blanks."""
    blanks = [
        row for row in targets
        if str(row.get("url") or "").strip().lower() == "about:blank"
        and str(row.get("id") or "").strip()
    ]
    if len(blanks) <= 1:
        return targets, 0

    keep_id = str(blanks[0].get("id") or "")
    closed = 0
    for row in blanks[1:]:
        target_id = str(row.get("id") or "")
        if target_id and target_id != keep_id and _close_page_target(target_id):
            closed += 1

    if closed:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            remaining = _page_targets()
            remaining_blanks = [
                row for row in remaining
                if str(row.get("url") or "").strip().lower() == "about:blank"
            ]
            if len(remaining_blanks) <= 1:
                return remaining, closed
            time.sleep(0.1)

    return _page_targets(), closed


def ensure_managed_chrome() -> str:
    """Ensure one managed Chrome/CDP session exists instead of opening another about:blank window."""
    current = existing_chrome_connection()
    if current is not None:
        return json.dumps(current, ensure_ascii=False)
    existing = _probe_cdp()
    if existing is not None:
        return json.dumps(
            {
                "started": False,
                "reused": True,
                "endpoint": _cdp_url(),
                "session_type": "managed-or-existing-cdp",
                "browser": str(existing.get("Browser") or ""),
                "note": "Chrome CDP is already available; no new Chrome window was opened.",
            },
            ensure_ascii=False,
        )

    with _START_LOCK:
        current = existing_chrome_connection()
        if current is not None:
            return json.dumps(current, ensure_ascii=False)
        existing = _probe_cdp()
        if existing is not None:
            return json.dumps(
                {
                    "started": False,
                    "reused": True,
                    "endpoint": _cdp_url(),
                    "session_type": "managed-or-existing-cdp",
                    "browser": str(existing.get("Browser") or ""),
                    "note": "Chrome CDP became available while waiting; no new Chrome window was opened.",
                },
                ensure_ascii=False,
            )

        result = json.loads(_start_managed_chrome())
        result["reused"] = False
        result["single_instance_guard"] = True
        return json.dumps(result, ensure_ascii=False)


def ensure_chrome_connection() -> str:
    """Connect after Chrome's startup targets stabilize, keeping one managed blank page."""
    current = existing_chrome_connection()
    if current is not None:
        return json.dumps(current, ensure_ascii=False)
    endpoint = _cdp_url()
    existed_before = _probe_cdp() is not None
    startup: dict[str, Any] = {}
    duplicates_closed = 0

    if not existed_before:
        startup = json.loads(ensure_managed_chrome())
        if startup.get("session_type") == "existing-window":
            return json.dumps(startup, ensure_ascii=False)

    started_here = startup.get("started") is True

    targets = _wait_for_page_target(timeout=5.0 if started_here else 1.5)
    if started_here and not targets:
        raise RuntimeError(
            "Managed Chrome CDP became available but its startup page target did not appear; "
            "refusing to create a second fallback about:blank page."
        )

    if started_here:
        # Chrome can publish a second about:blank shortly after the first even from one
        # managed startup invocation. Let the target list settle, then close only extra
        # about:blank targets in this fresh isolated JARVIS session before Playwright attaches.
        settled = _settle_page_targets(timeout=1.2, stable_polls=3)
        if settled:
            targets = settled
        targets, duplicates_closed = _dedupe_startup_blank_targets(targets)
        startup_blanks = [
            row for row in targets
            if str(row.get("url") or "").strip().lower() == "about:blank"
        ]
        if len(startup_blanks) > 1:
            raise RuntimeError(
                "Managed Chrome still exposed multiple about:blank startup targets after deduplication; "
                "refusing to attach until the session is unambiguous."
            )

    session_type = "managed" if started_here else "real"
    result = _runtime().call("connect", endpoint=endpoint, session_type=session_type)
    result.update(
        {
            "reused": not started_here,
            "startup_guard": True,
            "page_target_ready": bool(targets),
            "startup_target_count": len(targets),
            "startup_blank_duplicates_closed": duplicates_closed,
        }
    )
    if startup:
        result["managed_pid"] = startup.get("pid")
        result["profile"] = startup.get("profile")
        result["startup_log"] = startup.get("log")
    return json.dumps(result, ensure_ascii=False)


def register_chrome_session_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "chrome_start_managed",
            "Reuse the user's open Chrome window before considering startup. This operation is idempotent: matching CDP or guarded Windows toolbar navigation uses the existing window. Start managed Chrome only when no browser window exists; do not open another about:blank window unnecessarily.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_managed_chrome,
        )
    )
    registry.register(
        ToolSpec(
            "chrome_connect_cdp",
            "Reuse the user's existing Chrome window, preferring its matching CDP connection. An existing-window result supports guarded navigation/search without CDP. Start managed Chrome only if needed; fresh startup removes duplicate about:blank startup targets before attaching Playwright.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_chrome_connection,
        )
    )
