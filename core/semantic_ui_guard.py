from __future__ import annotations

import ctypes
import json
import os
from typing import Any, Callable

from .tools import ToolRegistry, ToolSpec

_GUARDED = {"ui_activate", "ui_type", "ui_hotkey", "ui_batch"}


def _foreground_is_chrome() -> bool:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        import psutil

        name = psutil.Process(int(pid.value)).name().casefold()
        return name in {"chrome.exe", "chromium.exe", "chrome", "chromium"}
    except Exception:
        return False


def _browser_block() -> str | None:
    if not _foreground_is_chrome():
        return None
    try:
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
    except Exception as exc:
        return f"BROWSER_ACTION_BLOCKED: browser challenge guard could not verify the current Chrome page: {type(exc).__name__}: {exc}"
    return None


def _wrap(handler: Callable[..., str]) -> Callable[..., str]:
    def guarded(**kwargs: Any) -> str:
        blocked = _browser_block()
        if blocked:
            return blocked
        return handler(**kwargs)

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
