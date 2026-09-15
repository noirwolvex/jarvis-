from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .chrome_cdp import (
    _cdp_url,
    chrome_current_tab,
    chrome_is_connected,
    chrome_page_operation,
    chrome_tabs,
    chrome_use_tab,
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


def _managed_blank_placeholder(rows: list[dict[str, Any]], current: dict[str, Any], target_url: str) -> int | None:
    if target_url == "about:blank" or str(current.get("session_type") or "") != "managed":
        return None
    blanks = [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("url") or "") == "about:blank"
        and not str(row.get("title") or "").strip()
    ]
    if not blanks:
        return None
    return int(blanks[-1].get("index", -1))


def _verified_navigation(target_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Navigate the currently selected Playwright page and reject a stale blank target."""
    result = chrome_page_operation("goto", url=target_url)
    if not isinstance(result, dict):
        raise RuntimeError("Chrome navigation did not return page evidence")
    current = json.loads(chrome_current_tab())
    current_url = str(current.get("url") or result.get("url") or "").strip()
    if not current_url or current_url == "about:blank":
        raise RuntimeError(
            f"Chrome tab stayed on about:blank instead of navigating to {target_url}"
        )
    parsed = urllib.parse.urlparse(current_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            f"Chrome tab navigation produced an invalid destination: {current_url or 'empty URL'}"
        )
    return result, current


def chrome_new_tab(url: str = "about:blank") -> str:
    """Create/select a real Chrome tab through CDP, then navigate it through the selected Playwright page."""
    target_url = _normalize_tab_url(url)
    if not chrome_is_connected():
        ensure_chrome_connection()

    before = json.loads(chrome_tabs())
    current_before = json.loads(chrome_current_tab())
    reusable_index = _managed_blank_placeholder(before, current_before, target_url)

    if reusable_index is not None and reusable_index >= 0:
        chrome_use_tab(reusable_index)
        result, current = _verified_navigation(target_url)
        evidence = {
            "created": False,
            "reused_managed_placeholder": True,
            "selected": reusable_index,
            "requested_url": target_url,
            "url": str(current.get("url") or result.get("url") or ""),
            "title": str(current.get("title") or result.get("title") or ""),
            "session_type": current.get("session_type", "managed"),
            "target_id": "",
        }
        return "VERIFIED: " + json.dumps(evidence, ensure_ascii=False)

    before_indexes = {int(row.get("index", -1)) for row in before if isinstance(row, dict)}

    # Create only an empty target through the raw DevTools endpoint. Navigation is done
    # after Playwright has selected that exact page. This avoids Chrome races where
    # /json/new reports success while the page still exposes about:blank to Playwright.
    target = _devtools_new_target("about:blank")

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
        time.sleep(0.10)

    if selected_index is None:
        raise RuntimeError(
            f"Chrome created target {target.get('id')} but JARVIS could not observe/select the new tab"
        )

    chrome_use_tab(selected_index)

    if target_url == "about:blank":
        current = json.loads(chrome_current_tab())
        current_url = str(current.get("url") or "")
    else:
        _result, current = _verified_navigation(target_url)
        current_url = str(current.get("url") or "")

    evidence = {
        "created": True,
        "reused_managed_placeholder": False,
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
