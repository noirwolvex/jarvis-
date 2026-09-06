from __future__ import annotations

import ctypes
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec


def _windows() -> list[tuple[int, str, int]]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    result: list[tuple[int, str, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append((int(hwnd), title, int(pid.value)))
        return True

    user32.EnumWindows(callback, 0)
    return result


def list_windows() -> str:
    foreground = int(ctypes.windll.user32.GetForegroundWindow()) if os.name == "nt" else 0
    rows = []
    for hwnd, title, pid in _windows():
        rows.append({"hwnd": hwnd, "title": title, "pid": pid, "foreground": hwnd == foreground})
    return json.dumps(rows[:200], ensure_ascii=False)


def focus_window_advanced(title: str) -> str:
    needle = title.strip().lower()
    candidates = [(hwnd, name) for hwnd, name, _pid in _windows() if needle in name.lower()]
    if not candidates:
        raise RuntimeError(f"No visible window matches: {title}")
    hwnd, actual = candidates[0]
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    if int(user32.GetForegroundWindow()) != hwnd:
        raise RuntimeError(f"Could not focus window: {actual}")
    return f"VERIFIED: focused '{actual}' (hwnd={hwnd})"


def close_window(title: str) -> str:
    needle = title.strip().lower()
    candidates = [hwnd for hwnd, name, _pid in _windows() if needle in name.lower()]
    if not candidates:
        raise RuntimeError(f"No visible window matches: {title}")
    hwnd = candidates[0]
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    time.sleep(0.4)
    still_open = any(candidate == hwnd for candidate, _name, _pid in _windows())
    return f"Window close requested for hwnd={hwnd}; closed={not still_open}"


def inspect_window(title: str = "") -> str:
    if os.name != "nt":
        raise RuntimeError("UI inspection is supported on Windows only")
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    if title.strip():
        window = desktop.window(title_re=f".*{re.escape(title.strip())}.*")
        window.wait("visible", timeout=2)
    else:
        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        if not hwnd:
            return "No foreground window."
        window = desktop.window(handle=hwnd)

    controls: list[dict[str, Any]] = []
    try:
        descendants = window.descendants()
    except Exception as exc:
        return f"UIA inspection failed: {type(exc).__name__}: {exc}"

    for control in descendants[:250]:
        try:
            info = control.element_info
            name = info.name or ""
            control_type = info.control_type or ""
            rect = control.rectangle()
            if name or control_type:
                controls.append({
                    "name": name,
                    "type": control_type,
                    "enabled": bool(control.is_enabled()),
                    "visible": bool(control.is_visible()),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                })
        except Exception:
            continue
    return json.dumps({"window": title or "foreground", "controls": controls}, ensure_ascii=False)


def desktop_move(x: int, y: int, duration: float = 0.1) -> str:
    import pyautogui
    pyautogui.moveTo(x, y, duration=max(0.0, duration))
    return f"Moved mouse to ({x}, {y})"


def desktop_scroll(clicks: int) -> str:
    import pyautogui
    pyautogui.scroll(clicks)
    return f"Scrolled desktop by {clicks}"


def desktop_double_click(x: int, y: int) -> str:
    import pyautogui
    pyautogui.doubleClick(x=x, y=y, interval=0.08)
    return f"Double-clicked desktop at ({x}, {y})"


def wait_seconds(seconds: float) -> str:
    seconds = max(0.0, min(float(seconds), 30.0))
    time.sleep(seconds)
    return f"Waited {seconds:.2f} seconds"


def open_path(path: str) -> str:
    if os.name != "nt":
        raise RuntimeError("Opening paths is supported on Windows only")
    raw = Path(path).expanduser()
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    resolved = (workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if workspace not in resolved.parents and resolved != workspace:
        raise PermissionError(f"Path is outside JARVIS_WORKSPACE: {resolved}")
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    os.startfile(str(resolved))
    return f"Opened path: {resolved}"


def browser_links() -> str:
    from .tools import _PAGE
    if _PAGE is None:
        return "No browser page is open."
    links = _PAGE.locator("a").evaluate_all(
        "els => els.slice(0, 300).map(a => ({text:(a.innerText||a.textContent||'').trim(), href:a.href})).filter(x => x.text || x.href)"
    )
    return json.dumps(links, ensure_ascii=False)


def browser_wait(selector: str, timeout_ms: int = 15000) -> str:
    from .tools import _PAGE
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    _PAGE.locator(selector).first.wait_for(state="visible", timeout=max(500, min(timeout_ms, 60000)))
    return f"VERIFIED: selector is visible: {selector}"


def browser_press(key: str) -> str:
    from .tools import _PAGE
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    _PAGE.keyboard.press(key)
    return f"Pressed browser key: {key}"


def browser_screenshot() -> str:
    from .tools import _PAGE
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    out_dir = workspace / ".jarvis" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"browser-{int(time.time() * 1000)}.png"
    _PAGE.screenshot(path=str(path), full_page=False)
    return f"Browser screenshot saved to {path}"


def register_advanced_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "list_windows",
        "Inspect all visible Windows windows with titles, process IDs, handles, and which window is currently foreground. Use this before choosing among multiple windows.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        list_windows,
    ))
    registry.register(ToolSpec(
        "focus_window_advanced",
        "Reliably focus a visible Windows window by partial title and verify it became foreground.",
        Risk.LOW,
        {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        focus_window_advanced,
    ))
    registry.register(ToolSpec(
        "close_window",
        "Request graceful close of a visible Windows window by partial title and report whether it disappeared.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        close_window,
    ))
    registry.register(ToolSpec(
        "inspect_window",
        "Inspect the foreground or named Windows window through UI Automation and return visible/enabled controls, names, types, and screen rectangles. Use this before coordinate clicking when possible.",
        Risk.LOW,
        {"type": "object", "properties": {"title": {"type": "string"}}, "additionalProperties": False},
        inspect_window,
    ))
    registry.register(ToolSpec(
        "desktop_move",
        "Move the mouse to absolute screen coordinates without clicking.",
        Risk.LOW,
        {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "duration": {"type": "number"}}, "required": ["x", "y"]},
        desktop_move,
    ))
    registry.register(ToolSpec(
        "desktop_scroll",
        "Scroll the currently active Windows application. Positive values scroll up; negative values scroll down.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"clicks": {"type": "integer"}}, "required": ["clicks"]},
        desktop_scroll,
    ))
    registry.register(ToolSpec(
        "desktop_double_click",
        "Double-click absolute screen coordinates.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]},
        desktop_double_click,
    ))
    registry.register(ToolSpec(
        "wait",
        "Wait briefly for an application, animation, page, or asynchronous operation to settle. Maximum 30 seconds.",
        Risk.SAFE,
        {"type": "object", "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 30}}, "required": ["seconds"]},
        wait_seconds,
    ))
    registry.register(ToolSpec(
        "open_path",
        "Open a workspace file or folder with its associated Windows application.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        open_path,
    ))
    registry.register(ToolSpec(
        "browser_links",
        "List visible links on the current browser page with their text and URLs so the agent can choose the correct navigation target.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        browser_links,
    ))
    registry.register(ToolSpec(
        "browser_wait",
        "Wait for a CSS selector to become visible in the current browser page.",
        Risk.LOW,
        {"type": "object", "properties": {"selector": {"type": "string"}, "timeout_ms": {"type": "integer", "minimum": 500, "maximum": 60000}}, "required": ["selector"]},
        browser_wait,
    ))
    registry.register(ToolSpec(
        "browser_press",
        "Press a keyboard key in the current Playwright browser page, such as Enter, Escape, or Control+L.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
        browser_press,
    ))
    registry.register(ToolSpec(
        "browser_screenshot",
        "Capture the current visible browser viewport to a timestamped JARVIS screenshot.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        browser_screenshot,
    ))

    from .dialog_tools import register_dialog_tools
    register_dialog_tools(registry)
