from __future__ import annotations

import json
import urllib.parse

from .chrome_cdp import (
    _runtime,
    chrome_is_connected,
)
from .chrome_session_tools import ensure_chrome_connection
from .chrome_user_browser import preferred_existing_chrome
from .browser_semantic import BrowserChallengeBlocked
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
    """Create a tab in the user's open Chrome, launching only when needed."""
    target_url = _normalize_tab_url(url)
    window = preferred_existing_chrome()
    if window is None and not chrome_is_connected():
        connected = json.loads(ensure_chrome_connection())
        if connected.get("session_type") == "existing-window":
            window = connected["window"]
    if window is not None:
        from .chrome_existing_window import navigate_existing_chrome
        try:
            result = navigate_existing_chrome(target_url, new_tab=True, window=window)
        except BrowserChallengeBlocked as exc:
            return str(exc)
        if result.get("verified") is not True:
            raise RuntimeError("Existing Chrome tab returned no verified evidence")
        return "VERIFIED: " + json.dumps(result, ensure_ascii=False)
    return new_tab_in_selected_session(target_url)


def new_tab_in_selected_session(url: str) -> str:
    """Keep compound operations on their already resolved CDP session."""
    target_url = _normalize_tab_url(url)
    result = _runtime().call("new_tab", url=target_url)
    if not isinstance(result, dict) or result.get("verified") is not True:
        raise RuntimeError("Browser tab creation returned no verified page evidence")
    return "VERIFIED: " + json.dumps(result, ensure_ascii=False)


def register_browser_tab_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "chrome_new_tab",
        "Open and verify a tab in the user's existing Chrome window, using matching CDP or guarded Windows toolbar input. Start Chrome only if no browser window exists. Use only for an explicit new-tab request. Reuse a fresh managed about:blank startup placeholder instead of leaving an extra blank tab.",
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
