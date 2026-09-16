from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
from typing import Any, Callable

from .tools import ToolRegistry, ToolSpec

_GUARDED = {"ui_activate", "ui_type", "ui_hotkey", "ui_batch", "ui_focus"}


def _foreground_is_chrome() -> bool:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return False
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            raise RuntimeError("No foreground window could be identified")
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            raise RuntimeError("Foreground process identity could not be read")
        import psutil

        name = psutil.Process(int(pid.value)).name().casefold()
        return name in {"chrome.exe", "chromium.exe", "chrome", "chromium", "msedge.exe", "firefox.exe", "brave.exe"}
    except Exception as exc:
        raise RuntimeError("Foreground browser boundary could not be inspected") from exc


def _browser_block() -> str | None:
    try:
        if not _foreground_is_chrome():
            return None
        from .browser_guard import browser_check_challenge
        from .chrome_cdp import chrome_is_connected

        if not chrome_is_connected():
            # Semantic UI should not mutate an unmanaged browser surface because the hard
            # challenge guard cannot prove the selected tab is safe.
            return (
                "BROWSER_ACTION_BLOCKED: JARVIS will not use desktop semantic input on an unmanaged "
                "Chrome window. Use the managed CDP browser tools so human-verification boundaries can be enforced."
            )
        payload = json.loads(browser_check_challenge())
        if bool(payload.get("challenge_detected")):
            return "BROWSER_ACTION_BLOCKED: " + json.dumps(
                {
                    "action": "STOP_AND_REQUEST_USER",
                    "reason": "A human-verification or anti-bot challenge is present in Chrome.",
                    "url": payload.get("url", ""),
                    "title": payload.get("title", ""),
                    "evidence": payload.get("evidence", []),
                },
                ensure_ascii=False,
            )
        # A selected CDP tab is not proof that it belongs to this foreground HWND.
        # Route browser actions through CDP, which binds each action to its inspected tab.
        return ("BROWSER_ACTION_BLOCKED: Foreground browser input cannot be bound to the inspected CDP tab. "
                "Use semantic DOM/CDP browser tools for this browser action.")
    except Exception as exc:
        return f"BROWSER_ACTION_BLOCKED: browser challenge guard could not verify the current Chrome page: {type(exc).__name__}: {exc}"
    return None


def _wrap(handler: Callable[..., str]) -> Callable[..., str]:
    def guarded(**kwargs: Any) -> str:
        from .semantic_ui_tools import BrowserBoundaryError
        try:
            # Handlers enforce this boundary AFTER resolving/focusing the intended target,
            # and ui_batch repeats it at every nested mutation. Checking only the old
            # foreground window here allows a later focus change to bypass the boundary.
            return handler(**kwargs)
        except BrowserBoundaryError as exc:
            return str(exc)

    return guarded


def guard_semantic_ui_tools(registry: ToolRegistry) -> None:
    """Apply the browser challenge boundary to every semantic UI mutation."""
    for name in _GUARDED:
        spec = registry._tools.get(name)
        if spec is None:
            continue
        registry._tools[name] = ToolSpec(
            name=spec.name,
            description=spec.description,
            risk=spec.risk,
            input_schema=spec.input_schema,
            handler=_wrap(spec.handler),
        )
