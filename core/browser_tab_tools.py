from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .chrome_cdp import (
    _cdp_url,
    chrome_connect_cdp,
    chrome_current_tab,
    chrome_is_connected,
    chrome_tabs,
    chrome_use_tab,
)
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


def _devtools_new_target(url: str) -> dict[str, Any]:
    endpoint = urllib.parse.urlparse(_cdp_url())
    host = endpoint.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("JARVIS Chrome CDP endpoint must stay on loopback")
    port = endpoint.port or 9222
    encoded = urllib.parse.quote(url, safe="")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{encoded}",
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        if response.status != 200:
            raise RuntimeError(f"Chrome DevTools refused new tab creation with HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError("Chrome DevTools did not return a valid new target")
    return payload


def chrome_new_tab(url: str = "about:blank") -> str:
    """Create a real Chrome tab through the local CDP endpoint and select it for later browser tools."""
    target_url = _normalize_tab_url(url)
    if not chrome_is_connected():
        chrome_connect_cdp()

    before = json.loads(chrome_tabs())
    before_indexes = {int(row.get("index", -1)) for row in before if isinstance(row, dict)}
    target = _devtools_new_target(target_url)

    deadline = time.time() + 8.0
    selected_index: int | None = None
    while time.time() < deadline:
        rows = json.loads(chrome_tabs())
        new_rows = [
            row for row in rows
            if isinstance(row, dict) and int(row.get("index", -1)) not in before_indexes
        ]
        if new_rows:
            selected_index = int(new_rows[-1]["index"])
            break
        if len(rows) > len(before):
            selected_index = int(rows[-1]["index"])
            break
        time.sleep(0.15)

    if selected_index is None:
        raise RuntimeError(
            f"Chrome created target {target.get('id')} but JARVIS could not observe/select the new tab"
        )

    chrome_use_tab(selected_index)
    current = json.loads(chrome_current_tab())
    current_url = str(current.get("url") or "")
    if target_url != "about:blank" and not current_url:
        raise RuntimeError("New Chrome tab was selected but its URL could not be verified")

    evidence = {
        "created": True,
        "selected": selected_index,
        "requested_url": target_url,
        "url": current_url,
        "title": current.get("title", ""),
        "session_type": current.get("session_type", "unknown"),
        "target_id": target.get("id", ""),
    }
    return "VERIFIED: " + json.dumps(evidence, ensure_ascii=False)


def register_browser_tab_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "chrome_new_tab",
        "Create a real new tab in the connected Chrome CDP session, automatically select that new tab as the active page, and verify it. When the user says 'open a new tab in Google', call this with url='https://www.google.com/' instead of launching another Chrome application. After it succeeds, use browser_page_state/browser_type/browser_press on this selected tab.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "about:blank or an http(s) URL to open in the new tab",
                }
            },
            "additionalProperties": False,
        },
        chrome_new_tab,
    ))
