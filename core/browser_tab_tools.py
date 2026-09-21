from __future__ import annotations

import json
import urllib.parse

from .chrome_cdp import (
    _runtime,
    chrome_is_connected,
)
from .chrome_session_tools import ensure_chrome_connection
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec


def _normalize_tab_url(url: str) -> str:
    value = str(url or "about:blank").strip()
    if not value:
        value = "about:blank"
    if value == "about:blank":
        return value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("New browser tabs may open only about:blank or an http(s) URL")
    return value


def chrome_new_tab(url: str = "about:blank") -> str:
    """Create/reuse and navigate one exact Page on the owning CDP thread."""
    target_url = _normalize_tab_url(url)
    if not chrome_is_connected():
        ensure_chrome_connection()
    result = _runtime().call("new_tab", url=target_url)
    if not isinstance(result, dict) or result.get("verified") is not True:
        raise RuntimeError("Browser tab creation returned no verified page evidence")
    return "VERIFIED: " + json.dumps(result, ensure_ascii=False)


def register_browser_tab_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "chrome_new_tab",
        "Open and select a real Chrome tab in the connected CDP session and verify it. JARVIS creates a blank target first, selects that exact Playwright page, then performs and verifies any requested http(s) navigation so a stale about:blank page can never count as success. For browser/search requests, use chrome_connect_cdp then this tool instead of launch_installed_app/open_application for Chrome. When managed Chrome starts with its temporary about:blank placeholder, this tool reuses that placeholder instead of leaving an extra blank tab.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "about:blank or an http(s) URL to open/select in Chrome",
                }
            },
            "additionalProperties": False,
        },
        chrome_new_tab,
    ))
