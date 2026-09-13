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

    # Re-check after entering the lock so repeated/concurrent tool calls in the same
    # Full Access process cannot race and launch multiple blank Chrome windows.
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
    """Connect only after Chrome has published its startup page, avoiding duplicate about:blank tabs."""
    endpoint = _cdp_url()
    existed_before = _probe_cdp() is not None
    startup: dict[str, Any] = {}

    if not existed_before:
        startup = json.loads(ensure_managed_chrome())

    # A fresh managed Chrome is launched with one about:blank page. /json/version can
    # become ready before that page appears in /json/list. If Playwright connects in
    # that gap, chrome_cdp._cmd_connect creates another page. Wait for the real startup
    # page instead of racing it.
    targets = _wait_for_page_target(timeout=5.0 if not existed_before else 1.5)
    if not existed_before and not targets:
        raise RuntimeError(
            "Managed Chrome CDP became available but its startup page target did not appear; "
            "refusing to create a second fallback about:blank page."
        )

    session_type = "managed" if not existed_before else "real"
    result = _runtime().call("connect", endpoint=endpoint, session_type=session_type)
    result.update(
        {
            "reused": existed_before,
            "startup_guard": True,
            "page_target_ready": bool(targets),
            "startup_target_count": len(targets),
        }
    )
    if startup:
        result["managed_pid"] = startup.get("pid")
        result["profile"] = startup.get("profile")
        result["startup_log"] = startup.get("log")
    return json.dumps(result, ensure_ascii=False)


def register_chrome_session_tools(registry: ToolRegistry) -> None:
    # Replace both original Chrome entry points. Full Access must never call the raw
    # fallback connector because it can race Chrome startup and create a second blank tab.
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
            "Safely connect JARVIS to Chrome CDP. If managed Chrome must start, wait for its real startup page target before attaching so Playwright cannot create a duplicate about:blank tab.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_chrome_connection,
        )
    )
