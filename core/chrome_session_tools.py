from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from typing import Any

from .chrome_cdp import _cdp_url, chrome_start_managed as _start_managed_chrome
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_START_LOCK = threading.Lock()


def _probe_cdp(timeout: float = 0.6) -> dict[str, Any] | None:
    """Return Chrome DevTools metadata when the configured local CDP endpoint is already alive."""
    endpoint = urllib.parse.urlparse(_cdp_url())
    host = endpoint.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    port = endpoint.port or 9222
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


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


def register_chrome_session_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "chrome_start_managed",
            "Ensure the dedicated JARVIS Chrome/CDP session is running. This operation is idempotent: if Chrome CDP is already available it reuses the existing session and MUST NOT open another about:blank window. Prefer chrome_connect_cdp first; use this only when a managed Chrome session is needed.",
            Risk.MEDIUM,
            {"type": "object", "properties": {}, "additionalProperties": False},
            ensure_managed_chrome,
        )
    )
