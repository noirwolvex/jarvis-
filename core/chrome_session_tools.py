from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from .chrome_cdp import _cdp_url, _runtime, chrome_start_managed as _start_managed_chrome
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_START_LOCK = threading.Lock()


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
    endpoint = _cdp_url()
    existed_before = _probe_cdp() is not None
    startup: dict[str, Any] = {}
    duplicates_closed = 0

    if not existed_before:
        startup = json.loads(ensure_managed_chrome())

    targets = _wait_for_page_target(timeout=5.0 if not existed_before else 1.5)
    if not existed_before and not targets:
        raise RuntimeError(
            "Managed Chrome CDP became available but its startup page target did not appear; "
            "refusing to create a second fallback about:blank page."
        )

    if not existed_before:
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

    session_type = "managed" if not existed_before else "real"
    result = _runtime().call("connect", endpoint=endpoint, session_type=session_type)
    result.update(
        {
            "reused": existed_before,
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
            "Ensure the dedicated JARVIS Chrome/CDP session is running. This operation is idempotent: if Chrome CDP is already available it reuses the existing session and MUST NOT open another about:blank window.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_managed_chrome,
        )
    )
    registry.register(
        ToolSpec(
            "chrome_connect_cdp",
            "Safely connect JARVIS to Chrome CDP. Fresh managed startup waits for page targets to stabilize, removes duplicate about:blank startup targets, and only then attaches Playwright.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_chrome_connection,
        )
    )
