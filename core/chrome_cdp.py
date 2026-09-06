from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def _browser_globals():
    from . import tools
    return tools


def _cdp_url() -> str:
    return os.getenv("JARVIS_CHROME_CDP_URL", "http://127.0.0.1:9222").rstrip("/")


def _chrome_executable() -> str:
    candidates = [
        os.getenv("JARVIS_CHROME_EXE", "").strip(),
        shutil.which("chrome.exe") or "",
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Google Chrome executable was not found. Set JARVIS_CHROME_EXE to chrome.exe.")


def _managed_profile_dir() -> Path:
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    return workspace / ".jarvis" / "chrome-cdp-profile"


def _cdp_port(endpoint: str) -> int:
    try:
        return int(endpoint.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return 9222


def chrome_start_managed() -> str:
    """Start a dedicated visible Google Chrome instance with CDP enabled for JARVIS."""
    endpoint = _cdp_url()
    port = _cdp_port(endpoint)
    exe = _chrome_executable()
    profile = _managed_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    last_error = "unknown error"
    while time.time() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0) as response:
                if response.status == 200:
                    return json.dumps({
                        "started": True,
                        "pid": process.pid,
                        "endpoint": endpoint,
                        "profile": str(profile),
                        "session_type": "managed",
                        "note": "This is a JARVIS-managed Chrome profile. It is a real Chrome window but does not inherit the already-running personal Chrome session.",
                    }, ensure_ascii=False)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"Chrome started with pid={process.pid}, but CDP did not become available at {endpoint}: {last_error}")


def _attach(endpoint: str) -> tuple[Any, Any, list[Any]]:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(endpoint, timeout=5000)
    contexts = browser.contexts
    pages: list[Any] = []
    for context in contexts:
        pages.extend(context.pages)
    return pw, browser, pages


def chrome_connect_cdp() -> str:
    """Connect to CDP Chrome; automatically launch and attach a JARVIS-managed Chrome when unavailable."""
    tools = _browser_globals()
    endpoint = _cdp_url()

    if tools._BROWSER is not None:
        try:
            pages: list[Any] = []
            for context in tools._BROWSER.contexts:
                pages.extend(context.pages)
            if pages:
                tools._PAGE = pages[-1]
                return json.dumps({
                    "connected": True,
                    "reused": True,
                    "endpoint": endpoint,
                    "session_type": getattr(tools, "_JARVIS_CHROME_SESSION_TYPE", "connected"),
                    "pages": _page_rows(pages),
                    "active_url": tools._PAGE.url,
                    "active_title": tools._PAGE.title(),
                }, ensure_ascii=False)
        except Exception:
            tools._BROWSER = None

    real_error = None
    try:
        pw, browser, pages = _attach(endpoint)
        if not pages:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            tools._PAGE = context.new_page()
            pages = [tools._PAGE]
        else:
            tools._PAGE = pages[-1]
        tools._BROWSER = browser
        tools._JARVIS_PLAYWRIGHT = pw
        tools._JARVIS_CHROME_SESSION_TYPE = "real"
        return json.dumps({
            "connected": True,
            "reused": False,
            "endpoint": endpoint,
            "session_type": "real",
            "pages": _page_rows(pages),
            "active_url": tools._PAGE.url,
            "active_title": tools._PAGE.title(),
        }, ensure_ascii=False)
    except Exception as exc:
        real_error = str(exc)

    managed = json.loads(chrome_start_managed())
    pw, browser, pages = _attach(endpoint)
    if not pages:
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        tools._PAGE = context.new_page()
        pages = [tools._PAGE]
    else:
        tools._PAGE = pages[-1]
    tools._BROWSER = browser
    tools._JARVIS_PLAYWRIGHT = pw
    tools._JARVIS_CHROME_SESSION_TYPE = "managed"
    return json.dumps({
        "connected": True,
        "reused": False,
        "fallback_from_real": True,
        "real_error": real_error,
        "endpoint": endpoint,
        "session_type": "managed",
        "managed_pid": managed.get("pid"),
        "profile": managed.get("profile"),
        "pages": _page_rows(pages),
        "active_url": tools._PAGE.url,
        "active_title": tools._PAGE.title(),
        "note": "JARVIS could not attach to the already-running personal Chrome, so it launched an isolated real Chrome profile. Personal Chrome cookies and tabs were not inherited.",
    }, ensure_ascii=False)


def _page_rows(pages: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        try:
            rows.append({"index": index, "title": page.title(), "url": page.url})
        except Exception:
            rows.append({"index": index, "title": "", "url": getattr(page, "url", "")})
    return rows


def chrome_tabs() -> str:
    tools = _browser_globals()
    if tools._BROWSER is None:
        raise RuntimeError("Chrome CDP is not connected. Run chrome_connect_cdp first.")
    pages: list[Any] = []
    for context in tools._BROWSER.contexts:
        pages.extend(context.pages)
    return json.dumps(_page_rows(pages), ensure_ascii=False)


def chrome_use_tab(index: int) -> str:
    tools = _browser_globals()
    if tools._BROWSER is None:
        raise RuntimeError("Chrome CDP is not connected. Run chrome_connect_cdp first.")
    pages: list[Any] = []
    for context in tools._BROWSER.contexts:
        pages.extend(context.pages)
    if index < 0 or index >= len(pages):
        raise IndexError(f"Tab index {index} is out of range; {len(pages)} tabs are available.")
    tools._PAGE = pages[index]
    return json.dumps({
        "selected": index,
        "session_type": getattr(tools, "_JARVIS_CHROME_SESSION_TYPE", "unknown"),
        "title": tools._PAGE.title(),
        "url": tools._PAGE.url,
    }, ensure_ascii=False)


def chrome_current_tab() -> str:
    tools = _browser_globals()
    if tools._PAGE is None:
        raise RuntimeError("No browser tab is selected.")
    return json.dumps({
        "session_type": getattr(tools, "_JARVIS_CHROME_SESSION_TYPE", "unknown"),
        "title": tools._PAGE.title(),
        "url": tools._PAGE.url,
    }, ensure_ascii=False)


def register_chrome_cdp_tools(registry) -> None:
    from .tools import ToolSpec
    from .permissions import Risk

    registry.register(ToolSpec(
        "chrome_start_managed",
        "Launch a dedicated visible Google Chrome instance with CDP enabled for JARVIS. Uses an isolated JARVIS profile and does not copy or inherit the already-running personal Chrome session.",
        Risk.MEDIUM,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_start_managed,
    ))
    registry.register(ToolSpec(
        "chrome_connect_cdp",
        "Connect JARVIS to Chrome through CDP. Automatically tries an existing remotely-debuggable Chrome first, then launches an isolated JARVIS-managed real Chrome if unavailable. No mode parameter is required.",
        Risk.MEDIUM,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_connect_cdp,
    ))
    registry.register(ToolSpec(
        "chrome_tabs",
        "List tabs exposed by the connected Chrome session with stable indexes, titles, URLs, and session context.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_tabs,
    ))
    registry.register(ToolSpec(
        "chrome_use_tab",
        "Select a tab from the connected Chrome session by index so existing browser tools operate on that actual tab.",
        Risk.LOW,
        {"type": "object", "properties": {"index": {"type": "integer", "minimum": 0}}, "required": ["index"]},
        chrome_use_tab,
    ))
    registry.register(ToolSpec(
        "chrome_current_tab",
        "Return the title, URL, and session type of the currently selected Chrome tab.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_current_tab,
    ))
