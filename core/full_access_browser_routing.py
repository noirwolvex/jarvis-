from __future__ import annotations

import json
import re
from pathlib import PureWindowsPath
from typing import Any, Callable
from urllib.parse import urlparse

from .chrome_cdp import chrome_is_connected, chrome_page_operation
from .chrome_session_tools import ensure_chrome_connection
from .tools import ToolRegistry, ToolSpec

_CHROME_NAMES = {
    "chrome",
    "chrome browser",
    "google chrome",
    "google chrome browser",
    "chromium",
}


def _normalized_app_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    text = re.sub(r"\s+(app|application)$", "", text).strip()
    return text


def _is_chrome_query(value: str) -> bool:
    text = _normalized_app_name(value)
    if text in _CHROME_NAMES:
        return True
    try:
        stem = PureWindowsPath(str(value).strip().strip('"')).stem.casefold()
    except Exception:
        stem = ""
    return stem in {"chrome", "chromium"}


def _validated_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only http:// and https:// URLs are allowed")
    return value


def _ensure_cdp() -> dict[str, Any]:
    if not chrome_is_connected():
        return json.loads(ensure_chrome_connection())
    return {"reused": True, "already_connected": True}


def _strict_navigate(url: str) -> str:
    target = _validated_url(url)
    _ensure_cdp()
    result = chrome_page_operation("goto", url=target)
    return f"VERIFIED: loaded {result['title']} — {result['url']} through managed Chrome CDP"


def _replace_handler(
    registry: ToolRegistry,
    name: str,
    handler: Callable[..., str],
    *,
    description: str | None = None,
) -> None:
    spec = registry._tools.get(name)
    if spec is None:
        return
    registry._tools[name] = ToolSpec(
        name=spec.name,
        description=description or spec.description,
        risk=spec.risk,
        input_schema=spec.input_schema,
        handler=handler,
    )


def register_full_access_browser_routing(registry: ToolRegistry) -> None:
    """Prevent Full Access browser tasks from escaping the one guarded Chrome/CDP session."""

    launch_spec = registry._tools.get("launch_installed_app")
    original_launch = launch_spec.handler if launch_spec is not None else None

    def launch_installed_app(query: str, timeout_seconds: float = 15.0) -> str:
        if _is_chrome_query(query):
            result = _ensure_cdp()
            return "VERIFIED: Chrome routed through managed CDP — " + json.dumps(result, ensure_ascii=False)
        if original_launch is None:
            raise RuntimeError("Installed application launcher is unavailable")
        return original_launch(query=query, timeout_seconds=timeout_seconds)

    open_spec = registry._tools.get("open_application")
    original_open = open_spec.handler if open_spec is not None else None

    def open_application(command: str) -> str:
        if _is_chrome_query(command):
            result = _ensure_cdp()
            return "VERIFIED: Chrome routed through managed CDP — " + json.dumps(result, ensure_ascii=False)
        if original_open is None:
            raise RuntimeError("Application launcher is unavailable")
        return original_open(command=command)

    _replace_handler(
        registry,
        "browser_navigate",
        _strict_navigate,
        description="Navigate the guarded managed Chrome/CDP tab to an HTTP(S) URL. Full Access must not launch a separate Playwright/Chromium fallback browser.",
    )
    _replace_handler(
        registry,
        "open_url",
        _strict_navigate,
        description="Open an HTTP(S) URL inside the guarded managed Chrome/CDP session rather than the system default browser.",
    )
    _replace_handler(
        registry,
        "launch_installed_app",
        launch_installed_app,
        description=(
            "Resolve, launch, and verify a Windows application. Chrome/Chromium requests are routed through the guarded managed CDP session; "
            "other installed applications use the normal resolver."
        ),
    )
    _replace_handler(
        registry,
        "open_application",
        open_application,
        description=(
            "Open a Windows application directly. Chrome/Chromium commands are routed through the guarded managed CDP session; "
            "other applications keep the normal direct launcher."
        ),
    )
