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


def _browser_context():
    tools = _browser_globals()
    return tools._BROWSER


def _chrome_executable() -> str:
    candidates = [
        os.getenv("JARVIS_CHROME_EXE", "").strip(),
        shutil.which("chrome.exe") or "",
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Google Chrome executable was not found. Set JARVIS_CHROME_EXE to chrome.exe.")


def _managed_profile_dir() -> Path:
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    return workspace / ".jarvis" / "chrome-cdp-profile"


def chrome_start_managed() -> str:
    """Start a dedicated visible Google Chrome instance with CDP enabled for JARVIS."""
    endpoint = _cdp_url()
    port = 9222
    if ":" in endpoint.rsplit("/", 1)[-1]:
        try:
            port = int(endpoint.rsplit(":", 1)[-1])
        except ValueError:
            port = 9222

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
                        "note": "This is a JARVIS-managed Chrome profile. It is a real Chrome window but does not inherit the already-running personal Chrome session.",
                    }, ensure_ascii=False)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"Chrome started with pid={process.pid}, but CDP did not become available at {endpoint}: {last_error}")


def chrome_connect_cdp() -> str:
    """Attach Playwright to Chrome exposing a CDP endpoint."""
    tools = _browser_globals()
    from playwright.sync_api import sync_playwright

    endpoint = _cdp_url()
    if tools._BROWSER is not None:
        try:
            pages = tools._BROWSER.contexts[0].pages if tools._BROWSER.contexts else []
            if pages:
                tools._PAGE = pages[-1]
                return json.dumps({
                    "connected": True,
                    "reused": True,
                    "endpoint": endpoint,
                    "pages": _page_rows(pages),
                    "active_url": tools._PAGE.url,
                    "active_title": tools._PAGE.title(),
                }, ensure_ascii=False)
        except Exception:
            tools._BROWSER = None

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=5000)
    except Exception as exc:
        raise RuntimeError(
            f"Could not connect to Chrome CDP at {endpoint}. "
            "The already-running Chrome cannot be attached after launch unless it was started with remote debugging. "
            "Use chrome_start_managed for a JARVIS-managed Chrome instance, or restart Chrome with --remote-debugging-port=9222."
        ) from exc

    contexts = browser.contexts
    pages: list[Any] = []
    for context in contexts:
        pages.extend(context.pages)

    if not pages:
        context = contexts[0] if contexts else browser.new_context()
        tools._PAGE = context.new_page()
        pages = [tools._PAGE]
    else:
        tools._PAGE = pages[-1]

    tools._BROWSER = browser
    tools._JARVIS_PLAYWRIGHT = pw
    return json.dumps({
        "connected": True,
        "reused": False,
        "endpoint": endpoint,
        "pages": _page_rows(pages),
        "active_url": tools._PAGE.url,
        "active_title": tools._PAGE.title(),
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
        "title": tools._PAGE.title(),
        "url": tools._PAGE.url,
    }, ensure_ascii=False)


def chrome_current_tab() -> str:
    tools = _browser_globals()
    if tools._PAGE is None:
        raise RuntimeError("No browser tab is selected.")
    return json.dumps({
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
        "Connect JARVIS to a Chrome session through the configured Chrome DevTools Protocol endpoint and reuse its available tabs/session state.",
        Risk.MEDIUM,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_connect_cdp,
    ))
    registry.register(ToolSpec(
        "chrome_tabs",
        "List tabs exposed by the connected Chrome session with stable indexes, titles, and URLs.",
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
        "Return the title and URL of the currently selected Chrome tab.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_current_tab,
    ))
